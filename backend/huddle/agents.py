"""Groups and their members, seeded from config/groups/<key>/ and then stored and edited in SQLite.

A group is a set of agents plus shared context (what everyone in the group knows). Every conversation
belongs to one group. An agent's soul (persona) and memory (private knowledge, plus what it was asked
to remember) are markdown the person can edit.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .config import AgentSpec, default_group, load_groups

# A name starts with a letter in any script and may contain letters, digits, spaces, apostrophes,
# hyphens and dots (José, 小明, Mary Ann, O'Neil). Its length limit comes from config.
_NAME = re.compile(r"^[^\W\d_][\w' .-]*$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_FIELDS = ("name", "role", "color", "lens", "soul", "memory")

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups(
  key TEXT PRIMARY KEY, position INTEGER NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL,
  common TEXT NOT NULL, preset INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agents(
  group_key TEXT NOT NULL, key TEXT NOT NULL, position INTEGER NOT NULL, name TEXT NOT NULL,
  role TEXT NOT NULL, color TEXT NOT NULL, lens TEXT NOT NULL, soul TEXT NOT NULL, memory TEXT NOT NULL,
  model TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(group_key, key));
"""


class TooManyAgents(Exception):
    pass


class UnknownGroup(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def public(agent: AgentSpec) -> dict:
    """The editable view of an agent for the API."""
    return {"key": agent.key, "name": agent.name, "role": agent.role, "color": agent.color,
            "lens": agent.lens, "soul": agent.persona, "memory": agent.knowledge}


class AgentRegistry:
    def __init__(self, store, cfg):
        self.store, self.cfg = store, cfg
        self.fields = cfg["limits"]["fields"]
        self.palette = cfg["palette"]
        self.default = default_group(cfg)
        self._presets = {g.key: g for g in load_groups(cfg)}
        # Usable before load(): tests and early requests see the configured presets.
        self._groups = {k: {"key": k, "name": g.name, "description": g.description, "common": g.common,
                            "preset": True, "examples": g.examples} for k, g in self._presets.items()}
        self._agents = {k: list(g.members) for k, g in self._presets.items()}
        self.version = 0

    # ---- reading -------------------------------------------------------------------------------
    def group_key(self, key: str | None) -> str:
        key = key or self.default
        if key not in self._groups:
            raise UnknownGroup(key)
        return key

    def groups(self) -> list[dict]:
        return [dict(g, agent_count=len(self._agents.get(k, []))) for k, g in self._groups.items()]

    def group(self, key: str | None = None) -> dict:
        key = self.group_key(key)
        return dict(self._groups[key], agent_count=len(self._agents.get(key, [])))

    def all(self, group: str | None = None) -> list[AgentSpec]:
        return list(self._agents.get(self.group_key(group), []))

    def get(self, group: str | None, key: str) -> AgentSpec | None:
        return next((a for a in self.all(group) if a.key == key), None)

    # ---- storage -------------------------------------------------------------------------------
    async def load(self):
        # Agents from before groups existed (no group_key) are seeds and early edits; start them over.
        await self.store.ensure_schema(SCHEMA, rebuild_if_missing={"agents": "group_key"})
        stored = {r["key"] for r in await self.store.query_all("SELECT key FROM groups")}
        for position, key in enumerate(self._presets):
            if key not in stored:
                await self._seed(key, position)
        await self._read()

    async def _seed(self, key: str, position: int | None = None):
        preset = self._presets[key]
        async with self.store.transaction() as tx:
            if position is None:
                row = await tx.one("SELECT position FROM groups WHERE key=?", (key,))
                position = row["position"] if row else len(self._presets)
            await tx.execute("DELETE FROM agents WHERE group_key=?", (key,))
            await tx.execute("DELETE FROM groups WHERE key=?", (key,))
            await tx.execute("INSERT INTO groups VALUES(?,?,?,?,?,?,?,?)",
                             (key, position, preset.name, preset.description, preset.common, 1, _now(), _now()))
            for i, a in enumerate(preset.members):
                await tx.execute("INSERT INTO agents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (key, a.key, i, a.name, a.role, a.color, a.lens, a.persona, a.knowledge, a.model,
                                  _now(), _now()))

    async def _read(self):
        groups = await self.store.query_all("SELECT * FROM groups ORDER BY position, created_at")
        rows = await self.store.query_all("SELECT * FROM agents ORDER BY group_key, position, created_at")
        self._groups = {g["key"]: {"key": g["key"], "name": g["name"], "description": g["description"],
                                   "common": g["common"], "preset": bool(g["preset"]),
                                   # Example prompts belong to the preset's config, not to the database.
                                   "examples": self._presets[g["key"]].examples if g["key"] in self._presets else []}
                        for g in groups}
        agents: dict[str, list[AgentSpec]] = {k: [] for k in self._groups}
        for r in rows:
            if r["group_key"] in agents:
                agents[r["group_key"]].append(AgentSpec(
                    key=r["key"], name=r["name"], role=r["role"], color=r["color"], lens=r["lens"],
                    model=r["model"], persona=r["soul"], knowledge=r["memory"],
                    common=self._groups[r["group_key"]]["common"]))
        self._agents = agents
        self.version += 1

    # ---- editing -------------------------------------------------------------------------------
    def _validate(self, group: str, fields: dict, *, current: AgentSpec | None) -> dict:
        clean = {}
        for field in _FIELDS:
            if field not in fields or fields[field] is None:
                continue
            value = fields[field]
            if not isinstance(value, str):
                raise ValueError(f"{field} must be text")
            clean[field] = value.strip() if field not in ("soul", "memory") else value.rstrip()
        limits = self.fields
        if "name" in clean:
            name = clean["name"]
            if not (1 <= len(name) <= limits["name"]) or not _NAME.match(name):
                raise ValueError(f"Name: 1–{limits['name']} characters, starting with a letter "
                                 "(letters, digits, spaces, apostrophes, hyphens, dots).")
            taken = {n.lower() for a in self.all(group) if a is not current for n in (a.name, a.key)}
            if name.lower() in taken:
                raise ValueError(f"There is already someone called {name} in this group.")
        for field in ("role", "lens"):
            if field in clean and len(clean[field]) > limits[field]:
                raise ValueError(f"{field.capitalize()}: at most {limits[field]} characters.")
        if "color" in clean and not _COLOR.match(clean["color"]):
            raise ValueError("Color: a hex value like #2563eb.")
        for field in ("soul", "memory"):
            if field in clean and len(clean[field]) > limits[field]:
                raise ValueError(f"{field.capitalize()}: at most {limits[field]:,} characters.")
        return clean

    def _key_for(self, group: str, name: str) -> str:
        base = re.sub(r"[^\w]+", "-", name.lower()).strip("-_") or "member"
        key, n = base, 2
        while self.get(group, key):
            key, n = f"{base}-{n}", n + 1
        return key

    async def create(self, group: str | None, fields: dict) -> AgentSpec:
        group = self.group_key(group)
        members = self.all(group)
        if len(members) >= self.fields["agents_per_group"]:
            raise TooManyAgents()
        if not (fields.get("name") or "").strip():
            raise ValueError("Every member needs a name.")
        clean = self._validate(group, fields, current=None)
        if not clean.get("soul", "").strip():
            raise ValueError("Write a few lines of Soul: who they are and how they talk.")
        key = self._key_for(group, clean["name"])
        color = clean.get("color") or self.palette[len(members) % len(self.palette)]
        async with self.store.transaction() as tx:
            row = await tx.one("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM agents WHERE group_key=?", (group,))
            await tx.execute("INSERT INTO agents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                             (group, key, row["p"], clean["name"], clean.get("role", ""), color, clean.get("lens", ""),
                              clean.get("soul", ""), clean.get("memory", ""), None, _now(), _now()))
        await self._read()
        return self.get(group, key)

    async def update(self, group: str | None, key: str, fields: dict) -> AgentSpec | None:
        group = self.group_key(group)
        current = self.get(group, key)
        if current is None:
            return None
        clean = self._validate(group, fields, current=current)
        if clean:
            sets = ", ".join(f"{field}=?" for field in clean)
            async with self.store.transaction() as tx:
                await tx.execute(f"UPDATE agents SET {sets}, updated_at=? WHERE group_key=? AND key=?",
                                 (*clean.values(), _now(), group, key))
            await self._read()
        return self.get(group, key)

    async def update_group(self, group: str | None, fields: dict) -> dict:
        group = self.group_key(group)
        clean = {}
        for field in ("name", "description", "common"):
            value = fields.get(field)
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(f"{field} must be text")
            value = value.strip() if field != "common" else value.rstrip()
            limit = self.fields[{"name": "group_name", "description": "group_description",
                                 "common": "group_common"}[field]]
            if field == "name" and not (1 <= len(value) <= limit):
                raise ValueError(f"Name: 1–{limit} characters.")
            if field != "name" and len(value) > limit:
                label = "Shared context" if field == "common" else "Description"
                raise ValueError(f"{label}: at most {limit:,} characters.")
            clean[field] = value
        if clean:
            sets = ", ".join(f"{field}=?" for field in clean)
            async with self.store.transaction() as tx:
                await tx.execute(f"UPDATE groups SET {sets}, updated_at=? WHERE key=?", (*clean.values(), _now(), group))
            await self._read()
        return self.group(group)

    async def remember(self, group: str | None, key: str, note: str) -> str:
        """Append a note to an agent's memory; returns the text for the model."""
        agent = self.get(group, key)
        note = " ".join((note or "").split())
        if agent is None or not note:
            return "nothing to remember"
        cap = self.fields["remember_note"]
        if len(note) > cap:
            note = note[:cap - 3] + "..."
        heading = "## Remembered"
        memory = agent.knowledge.rstrip()
        if heading not in memory:
            memory += f"\n\n{heading}"
        memory += f"\n- {note}"
        if len(memory) > self.fields["memory"]:
            return "memory is full; nothing was saved"
        await self.update(group, key, {"memory": memory})
        return "saved to your memory"

    async def reset(self, group: str | None = None):
        group = self.group_key(group)
        if group not in self._presets:
            raise UnknownGroup(group)
        await self._seed(group)
        await self._read()
