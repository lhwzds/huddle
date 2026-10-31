"""Transactional SQLite event log and materialized conversation state."""
from __future__ import annotations

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import aiosqlite
from .bus import Bus

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations(
 id TEXT PRIMARY KEY, title TEXT, created_at TEXT NOT NULL,
 last_seq INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(
 conv_id TEXT NOT NULL REFERENCES conversations(id), seq INTEGER NOT NULL,
 type TEXT NOT NULL, data TEXT NOT NULL, ts TEXT NOT NULL,
 PRIMARY KEY(conv_id, seq));
CREATE TABLE IF NOT EXISTS messages(
 id TEXT PRIMARY KEY, conv_id TEXT NOT NULL REFERENCES conversations(id),
 pos INTEGER NOT NULL, author TEXT NOT NULL, turn INTEGER NOT NULL,
 reply_to TEXT, text TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, effort TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS messages_conv_pos ON messages(conv_id, pos);
CREATE TABLE IF NOT EXISTS instructions(
 id TEXT PRIMARY KEY, conv_id TEXT NOT NULL REFERENCES conversations(id),
 agent TEXT, text TEXT NOT NULL, source_message_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_steps(
 id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id TEXT NOT NULL, agent TEXT NOT NULL,
 message_id TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage(
 day TEXT PRIMARY KEY, tokens INTEGER NOT NULL DEFAULT 0);
"""

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _message(row) -> dict:
    return {"id": row["id"], "author": row["author"], "turn": row["turn"],
            "reply_to": row["reply_to"].split(",") if row["reply_to"] else [],
            "text": row["text"], "status": row["status"], "effort": row["effort"],
            "pos": row["pos"], "created_at": row["created_at"]}


def _conversation(row) -> dict:
    data = {key: row[key] for key in ("id", "title", "created_at")}
    data["group"] = row["group_key"] if "group_key" in row.keys() else None
    return data

class ConversationLock:
    """Task-reentrant lock for atomic runtime operations composed of store writes.

    Exclusive between tasks; ownership is never inherited by child tasks.
    """
    def __init__(self):
        self._lock = asyncio.Lock()
        self._owner = None
        self._depth = 0

    async def acquire(self):
        task = asyncio.current_task()
        if self._owner is not task:
            await self._lock.acquire()
            self._owner = task
        self._depth += 1
        return True

    def release(self):
        if self._owner is not asyncio.current_task():
            raise RuntimeError("Conversation lock is not owned by this task")
        self._depth -= 1
        if not self._depth:
            self._owner = None
            self._lock.release()

    def locked(self):
        return self._lock.locked()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *args):
        self.release()


class _Tx:
    """What a caller can do inside `Store.transaction()`."""
    def __init__(self, store):
        self._store = store

    async def execute(self, sql, args=()):
        await self._store.db.execute(sql, args)

    async def one(self, sql, args=()):
        return await self._store._one(sql, args)


class Store:
    def __init__(self, path: str | Path, bus: Bus | None = None, *, default_group: str | None = None):
        self.default_group = default_group
        self.path = str(path)
        self.bus = bus if bus is not None else Bus()
        self.db: aiosqlite.Connection | None = None
        self._locks: dict[str, ConversationLock] = {}
        self._db_lock = asyncio.Lock()

    async def open(self):
        if self.db is not None:
            return self
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA foreign_keys=ON")
        await self.db.executescript(SCHEMA)
        # Conversations from before groups existed belong to the default group (NULL).
        columns = [r["name"] for r in await self._all("PRAGMA table_info(conversations)")]
        if "group_key" not in columns:
            await self.db.execute("ALTER TABLE conversations ADD COLUMN group_key TEXT")
        if self.default_group:
            # Conversations from before groups existed belong to the group that was the default then.
            await self.db.execute("UPDATE conversations SET group_key=? WHERE group_key IS NULL", (self.default_group,))
        await self.db.commit()
        return self

    async def close(self):
        if self.db is not None:
            await self.db.close()
            self.db = None

    async def __aenter__(self):
        return await self.open()

    async def __aexit__(self, *args):
        await self.close()

    def lock(self, conv_id: str) -> ConversationLock:
        return self._locks.setdefault(conv_id, ConversationLock())

    # ---- public access for other tables (agents, groups): no private members needed ----------
    @asynccontextmanager
    async def transaction(self):
        """All-or-nothing writes: `async with store.transaction() as tx: await tx.execute(...)`."""
        async with self._transaction():
            yield _Tx(self)

    async def query_one(self, sql, args=()):
        async with self._db_lock:
            return await self._one(sql, args)

    async def query_all(self, sql, args=()):
        async with self._db_lock:
            return await self._all(sql, args)

    async def ensure_schema(self, script: str, *, rebuild_if_missing: dict[str, str] | None = None):
        """Create tables. rebuild_if_missing: {table: column} drops a table that lacks that column first."""
        async with self._db_lock:
            for table, column in (rebuild_if_missing or {}).items():
                columns = [r["name"] for r in await self._all(f"PRAGMA table_info({table})")]
                if columns and column not in columns:
                    await self.db.execute(f"DROP TABLE {table}")
            await self.db.executescript(script)
            await self.db.commit()

    async def _one(self, sql, args=()):
        async with self.db.execute(sql, args) as cur:
            return await cur.fetchone()

    async def _all(self, sql, args=()):
        async with self.db.execute(sql, args) as cur:
            return await cur.fetchall()

    @asynccontextmanager
    async def _transaction(self, on_commit=None):
        async with self._db_lock:
            try:
                await self.db.execute("BEGIN")
                yield
            except BaseException:
                await self.db.rollback()
                raise
            # aiosqlite executes on a worker thread: cancelling commit does not
            # stop it. Wait for its outcome and publish even if cancelled, so a
            # committed event can never disappear from the live stream.
            commit = asyncio.create_task(self.db.commit())
            cancelled = False
            try:
                await asyncio.shield(commit)
            except asyncio.CancelledError:
                cancelled = True
                await commit
            except BaseException:
                await self.db.rollback()
                raise
            if on_commit is not None:
                on_commit()
            if cancelled:
                raise asyncio.CancelledError

    async def _next_seq(self, conv_id):
        row = await self._one("SELECT last_seq FROM conversations WHERE id=?", (conv_id,))
        if row is None:
            raise ValueError(f"Unknown conversation: {conv_id}")
        seq = row["last_seq"] + 1
        await self.db.execute("UPDATE conversations SET last_seq=? WHERE id=?", (seq, conv_id))
        return seq

    async def _event(self, conv_id, seq, type, data):
        event = {"seq": seq, "type": type, "data": copy.deepcopy(data), "ts": now()}
        await self.db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (conv_id, seq, type, json.dumps(event["data"], ensure_ascii=False), event["ts"]))
        return event

    async def create_conversation(self, title=None, group=None):
        result = {"id": uuid4().hex, "title": title, "created_at": now(), "group": group}
        async with self._transaction():
            await self.db.execute("INSERT INTO conversations(id,title,created_at,group_key) VALUES(?,?,?,?)",
                                  tuple(result.values()))
        return result

    async def list_conversations(self):
        async with self._db_lock:
            rows = await self._all("SELECT * FROM conversations ORDER BY created_at DESC, rowid DESC")
        return [_conversation(row) for row in rows]

    async def get_conversation(self, id):
        async with self._db_lock:
            row = await self._one("SELECT * FROM conversations WHERE id=?", (id,))
        return _conversation(row) if row else None

    async def add_message(self, conv_id, *, author, turn, text="", status="streaming", reply_to=None, effort=None):
        if status not in ("streaming", "done", "stopped"):
            raise ValueError("Invalid message status")
        if isinstance(reply_to, str):
            reply_to = [x for x in reply_to.split(",") if x]
        reply_to = reply_to or []
        async with self.lock(conv_id):
            async with self._transaction(lambda: self.bus.publish(conv_id, event)):
                seq = await self._next_seq(conv_id)
                stamp = now()
                message = dict(id=uuid4().hex, author=author, turn=turn, reply_to=reply_to, text=text, status=status, effort=effort, pos=seq, created_at=stamp)
                await self.db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)", (message["id"], conv_id, seq, author, turn, ",".join(reply_to) or None, text, status, effort, stamp, stamp))
                event = await self._event(conv_id, seq, "message.created", {"message": message})
        return message

    async def _message_conv(self, message_id):
        async with self._db_lock:
            row = await self._one("SELECT conv_id FROM messages WHERE id=?", (message_id,))
        if row is None:
            raise ValueError(f"Unknown message: {message_id}")
        return row["conv_id"]

    async def _validate_streaming(self, conv_id, message_id):
        row = await self._one("SELECT status FROM messages WHERE id=? AND conv_id=?", (message_id, conv_id))
        if not row or row["status"] != "streaming":
            raise ValueError(f"Message is not streaming: {message_id}")

    async def append_text(self, message_id, text):
        conv_id = await self._message_conv(message_id)
        async with self.lock(conv_id):
            async with self._transaction(lambda: self.bus.publish(conv_id, event)):
                await self._validate_streaming(conv_id, message_id)
                seq = await self._next_seq(conv_id)
                await self.db.execute("UPDATE messages SET text=text || ?, updated_at=? WHERE id=?", (text, now(), message_id))
                event = await self._event(conv_id, seq, "message.delta", {"id": message_id, "text": text})
        return event

    async def finish_message(self, message_id, status):
        if status not in ("done", "stopped"):
            raise ValueError("Invalid final status")
        conv_id = await self._message_conv(message_id)
        async with self.lock(conv_id):
            async with self._transaction(lambda: self.bus.publish(conv_id, event)):
                await self._validate_streaming(conv_id, message_id)
                seq = await self._next_seq(conv_id)
                await self.db.execute("UPDATE messages SET status=?, updated_at=? WHERE id=?", (status, now(), message_id))
                event = await self._event(conv_id, seq, "message.done", {"id": message_id, "status": status})
        return event

    async def emit(self, conv_id, type, data):
        if type not in ("message.tool", "agent.typing", "agent.context", "file.changed"):
            raise ValueError(f"Invalid event type: {type}")
        async with self.lock(conv_id):
            async with self._transaction(lambda: self.bus.publish(conv_id, event)):
                if type == "message.tool":
                    await self._validate_streaming(conv_id, data.get("id"))
                seq = await self._next_seq(conv_id)
                event = await self._event(conv_id, seq, type, data)
        return event

    async def snapshot(self, conv_id, agents_state, active=None):
        async with self.lock(conv_id):
            async with self._db_lock:
                row = await self._one("SELECT * FROM conversations WHERE id=?", (conv_id,))
                if not row:
                    raise ValueError(f"Unknown conversation: {conv_id}")
                rows = await self._all("SELECT * FROM messages WHERE conv_id=? ORDER BY pos", (conv_id,))
                # The API supplies a callable so the state is read while the lock is held.
                agents = agents_state() if callable(agents_state) else agents_state
                # The activity flag is read under the same lock, so it agrees with the rest.
                is_active = active() if callable(active) else bool(active)
                return {"conversation": _conversation(row), "last_seq": row["last_seq"], "messages": [_message(r) for r in rows], "agents": copy.deepcopy(agents), "active": is_active}

    async def events_after(self, conv_id, seq):
        async with self._db_lock:
            rows = await self._all("SELECT seq,type,data,ts FROM events WHERE conv_id=? AND seq>? ORDER BY seq", (conv_id, seq))
        return [{"seq": r["seq"], "type": r["type"], "data": json.loads(r["data"]), "ts": r["ts"]} for r in rows]

    async def last_turn(self, conv_id):
        async with self._db_lock:
            row = await self._one("SELECT COALESCE(MAX(turn),0) AS n FROM messages WHERE conv_id=?", (conv_id,))
        return row["n"]

    async def messages(self, conv_id):
        async with self._db_lock:
            rows = await self._all("SELECT * FROM messages WHERE conv_id=? ORDER BY pos", (conv_id,))
        return [_message(r) for r in rows]

    async def add_instruction(self, conv_id, agent, text, source_message_id):
        data = dict(id=uuid4().hex, conv_id=conv_id, agent=agent, text=text, source_message_id=source_message_id, created_at=now())
        async with self.lock(conv_id):
            async with self._transaction():
                await self.db.execute("INSERT INTO instructions VALUES(?,?,?,?,?,?)", tuple(data.values()))
        return data

    async def instructions(self, conv_id):
        async with self._db_lock:
            rows = await self._all("SELECT * FROM instructions WHERE conv_id=? ORDER BY rowid", (conv_id,))
        return [dict(r) for r in rows]

    async def add_step(self, conv_id, agent, message_id, kind, data):
        async with self.lock(conv_id):
            async with self._transaction():
                cursor = await self.db.execute("INSERT INTO agent_steps(conv_id,agent,message_id,kind,data,created_at) VALUES(?,?,?,?,?,?)", (conv_id, agent, message_id, kind, json.dumps(data, ensure_ascii=False), now()))
                return cursor.lastrowid

    async def steps(self, conv_id, agent):
        async with self._db_lock:
            rows = await self._all("SELECT * FROM agent_steps WHERE conv_id=? AND agent=? ORDER BY id", (conv_id, agent))
        return [dict(r) | {"data": json.loads(r["data"])} for r in rows]

    async def add_usage(self, day, tokens):
        """Add the tokens of one day to the usage counter."""
        async with self._transaction():
            await self.db.execute(
                "INSERT INTO usage(day,tokens) VALUES(?,?) "
                "ON CONFLICT(day) DO UPDATE SET tokens=tokens+excluded.tokens",
                (day, int(tokens)))

    async def usage_tokens(self, day):
        """The tokens that were used on one day."""
        async with self._db_lock:
            row = await self._one("SELECT tokens FROM usage WHERE day=?", (day,))
        return int(row["tokens"]) if row else 0

    async def stop_streaming_on_startup(self):
        async with self._transaction():
            await self.db.execute("UPDATE messages SET status='stopped', updated_at=? WHERE status='streaming'", (now(),))
