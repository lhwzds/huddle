"""One agent, one visible message, with in-place continuations on user input."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass

from . import prompts
from .config import AgentSpec
from .context import build_context, load_state
from .llm import Done, TextDelta, ToolCallDone
from .prompts import seam
from .tools import REMEMBER_SPEC, TOOL_SPECS, ToolSettings, run_tool

log = logging.getLogger(__name__)
WEB_TOOLS = ("web_search", "fetch_url")


def idle_state(limit: int) -> dict:
    """An agent's public state before it has done anything in a conversation."""
    return {"typing": False, "context": {"used": 0, "limit": limit}}


@dataclass
class RunContext:
    conv_id: str
    cfg: dict
    store: object
    llm: object
    sandbox: object
    roster: list[AgentSpec]
    state: dict  # public per-agent typing and context use, guarded by store.lock
    registry: object = None  # AgentRegistry, for the remember tool
    group: str | None = None  # the conversation's group
    can_remember: bool = False  # the person asked for something to be remembered in this turn

    @property
    def lock(self):
        return self.store.lock(self.conv_id)

    async def typing(self, agent, value):
        async with self.lock:
            entry = self.state.setdefault(agent, idle_state(self.cfg["limits"]["context_tokens"]))
            if entry["typing"] != value:
                entry["typing"] = value
                await self.store.emit(self.conv_id, "agent.typing", {"agent": agent, "typing": value})

    async def context(self, agent, built):
        async with self.lock:
            use = {"used": built.used, "limit": built.limit}
            self.state.setdefault(agent, idle_state(built.limit))["context"] = use
            await self.store.emit(self.conv_id, "agent.context", {"agent": agent, **use})


async def _cancel_task(task):
    if task is None:
        return None
    if not task.done():
        task.cancel()
    result = await asyncio.gather(task, return_exceptions=True)
    return result[0]


class AgentRun:
    def __init__(self, ctx: RunContext, agent: AgentSpec, turn: int, effort: str, reply_to: list[str], *, held=False,
                 directive=None):
        self.ctx, self.agent, self.turn = ctx, agent, turn
        self.directive = directive  # extra instruction for this message, e.g. writing the conclusion
        self.effort, self.reply_to = effort, reply_to
        self.inbox = asyncio.Queue()
        self.message_id = None
        self.task = None
        self.finished = False
        self._closing = False
        self._started = False
        self._cancelled = False
        self._buffer = []
        self._pending_steps = []
        self._remembered = False
        self._offered = set()  # names of the tools offered in the latest model call
        self._wanted_tools = False  # the latest model call tried to use a tool it was not given
        self._escalated = False
        self._web_calls = 0
        self._flush_s = ctx.cfg["limits"]["flush_interval_s"]
        self._over_budget = False
        self._last_reasoning = ""
        self._pending_tools = []
        self._last_flush = 0.0
        self._tool_running = False
        self._typing = False
        self._held = held
        self._release_event = asyncio.Event()
        self._held_context = None
        self._seam_visible = None   # text already shown, waiting for a seam
        self._after_tool = False    # the next text follows a tool call
        self._last_char = ""        # last character of the text produced so far

    def release(self):
        self._release_event.set()

    async def _publish(self):
        if not self._held:
            return
        self._held = False
        await self.ctx.typing(self.agent.key, True)
        self._typing = True
        if self._held_context:
            await self.ctx.context(self.agent.key, self._held_context)
        if self._buffer:
            await self._create()
            await self._flush()

    async def _wait_release(self):
        # A completed model or a pending tool must still accept steers.
        release = asyncio.create_task(self._release_event.wait())
        incoming = asyncio.create_task(self.inbox.get())
        try:
            await asyncio.wait({release, incoming}, return_when=asyncio.FIRST_COMPLETED)
            received = await _cancel_task(incoming)
            incoming = None
            pending = ([received] if isinstance(received, str) else []) + self._drain()
            if release.done():
                await self._publish()
            return pending
        finally:
            await _cancel_task(release)
            await _cancel_task(incoming)

    def steer(self, user_message_id: str) -> bool:
        if self.finished or self._closing or self._cancelled:
            return False
        self.inbox.put_nowait(user_message_id)
        return True

    def mark_stopped(self):
        """The orchestrator stopped this run and already cleared its typing state."""
        self.finished = True
        self._typing = False

    def cancel(self):
        if self._cancelled or self.finished:
            return
        self._cancelled = True
        # A task cancelled before its first instruction cannot execute cleanup.
        if self._started and self.task and not self.task.done():
            self.task.cancel()

    def _visible(self, by_id):
        """The text the reader has already seen for this message."""
        shown = by_id.get(self.message_id, "") if self.message_id else ""
        return shown + "".join(self._buffer)

    def _drain(self):
        pending = []
        while not self.inbox.empty():
            pending.append(self.inbox.get_nowait())
        return pending

    async def _create(self):
        if self.message_id is None:
            async with self.ctx.lock:
                message = await self.ctx.store.add_message(
                    self.ctx.conv_id, author=self.agent.key, turn=self.turn,
                    reply_to=self.reply_to, effort=self.effort)
                self.message_id = message["id"]
                for kind, data in self._pending_steps:
                    await self.ctx.store.add_step(self.ctx.conv_id, self.agent.key, self.message_id, kind, data)
                self._pending_steps.clear()
                for tool, summary in self._pending_tools:
                    await self.ctx.store.emit(self.ctx.conv_id, "message.tool",
                                              {"id": self.message_id, "tool": tool, "summary": summary})
                self._pending_tools.clear()
                await self.ctx.typing(self.agent.key, False)
                self._typing = False

    async def _text(self, text):
        if self.message_id is None and not self._buffer and self._seam_visible is None:
            # A message starts with its first real word, never with blank lines.
            text = text.lstrip()
        if text:
            if self._seam_visible is not None:
                # A steered continuation must not run into the text already shown. If the model opens
                # with its own ellipsis, ours already marks the break.
                joint = seam(self._seam_visible)
                if joint.strip():
                    text = text.lstrip(" .…")
                text = joint + text
                self._seam_visible = None
            elif self._after_tool and self._last_char and not self._last_char.isspace():
                # Text written after a tool call starts a new paragraph after what was said before it.
                text = "\n\n" + text.lstrip()
            self._after_tool = False
            self._last_char = text[-1]
            if not self._held:
                await self._create()
            self._buffer.append(text)

    async def _flush(self):
        if self._buffer and not self._held:
            text = "".join(self._buffer)
            self._buffer.clear()
            await self.ctx.store.append_text(self.message_id, text)
        self._last_flush = asyncio.get_running_loop().time()

    async def _stream(self, built, profile, tools):
        """Race *tasks*, never cancel a received delta just because input also won.

        Keep the pending anext alive across flush deadlines. If both tasks are
        ready, consume the model event first, then flush and apply the input.
        """
        stream = self.ctx.llm.stream(built.messages, profile=profile,
                                     max_tokens=self.ctx.cfg["limits"]["max_tokens"][profile], tools=tools)
        next_event = asyncio.create_task(anext(stream))
        incoming = asyncio.create_task(self.inbox.get())
        release = asyncio.create_task(self._release_event.wait()) if self._held else None
        calls, pending = [], []
        ended = False
        try:
            while True:
                timeout = max(0, self._flush_s - (asyncio.get_running_loop().time() - self._last_flush)) if self._buffer and not self._held else None
                waiting = {next_event, incoming}
                if release:
                    waiting.add(release)
                await asyncio.wait(waiting, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                if release and release.done():
                    await self._publish()
                    release = None
                if next_event.done():
                    try:
                        event = next_event.result()
                    except StopAsyncIteration:
                        ended = True
                    else:
                        if isinstance(event, TextDelta):
                            await self._text(event.text)
                        elif isinstance(event, ToolCallDone):
                            if tools:
                                calls.append(event.call)
                            else:
                                log.warning("Ignored an unoffered tool call from %s", self.agent.key)
                        elif isinstance(event, Done):
                            self._last_reasoning = event.reasoning or ""
                            self._wanted_tools = bool(getattr(event, "wanted_tools", False))
                            ended = True
                    next_event = None
                if incoming.done() or ended:
                    # Cancelling a pending Queue.get cannot consume an item; if
                    # it already completed, preserve the returned message id.
                    received = await _cancel_task(incoming)
                    incoming = None
                    if isinstance(received, str):
                        pending.append(received)
                    pending.extend(self._drain())
                    if pending and next_event is not None:
                        received = await _cancel_task(next_event)
                        next_event = None
                        if isinstance(received, TextDelta):
                            await self._text(received.text)
                    await self._flush()
                    return calls, pending
                if self._buffer and not self._held and asyncio.get_running_loop().time() - self._last_flush >= self._flush_s:
                    await self._flush()
                if next_event is None:
                    next_event = asyncio.create_task(anext(stream))
        finally:
            await _cancel_task(next_event)
            await _cancel_task(incoming)
            await _cancel_task(release)
            await stream.aclose()

    async def _files(self):
        try:
            return {f["path"]: f for f in await self.ctx.sandbox.files(self.ctx.conv_id)}
        except Exception:
            log.warning("Cannot list sandbox files", exc_info=True)
            return {}

    async def _say_failed(self):
        """A model or tool failure must not leave a silent or cut-off answer: say so, briefly."""
        if self._held or self._cancelled:
            return
        try:
            if self._buffer:
                await self._flush()
            note = prompts.RUN_FAILED
            if self.message_id:
                shown = "".join(m["text"] for m in await self.ctx.store.messages(self.ctx.conv_id)
                                if m["id"] == self.message_id)
                note = (seam(shown) if shown else "") + note
                await self.ctx.store.append_text(self.message_id, note)
            else:
                async with self.ctx.lock:
                    message = await self.ctx.store.add_message(
                        self.ctx.conv_id, author=self.agent.key, turn=self.turn, text=note,
                        reply_to=self.reply_to, effort=self.effort)
                    self.message_id = message["id"]
        except Exception:
            log.warning("Could not report the failed run of %s", self.agent.key, exc_info=True)

    async def _step(self, kind, data):
        if self.message_id:
            await self.ctx.store.add_step(self.ctx.conv_id, self.agent.key, self.message_id, kind, data)
        else:
            self._pending_steps.append((kind, data))

    async def _remember(self, call):
        self._remembered = True
        try:
            note = json.loads(call.arguments).get("note", "")
        except (ValueError, AttributeError):
            note = ""
        if not isinstance(note, str) or not note.strip():
            return prompts.tool_error("note must be non-empty text"), "remember"
        result = await self.ctx.registry.remember(self.ctx.group, self.agent.key, note)
        return result, prompts.remembered(note)

    async def _tools(self, calls, over_budget=False):
        self._over_budget = over_budget
        store, conv_id = self.ctx.store, self.ctx.conv_id
        uses_sandbox = any(call.name != "remember" for call in calls)
        before = await self._files() if uses_sandbox else {}
        for index, call in enumerate(calls):
            data = asdict(call)
            if index == 0 and self._last_reasoning:
                data["reasoning"] = self._last_reasoning
            await self._step("tool_call", data)
        pending = self._drain()
        for call in calls:
            if pending:
                result, summary = prompts.INTERRUPTED, call.name
            elif call.name not in self._offered or (call.name == "remember" and not self.ctx.can_remember):
                # A model may return a call for a tool it was not given; it is answered, never run.
                # Memory is shared by every conversation, so it is also checked here, not only when offering.
                log.warning("Refused an unoffered %s call from %s", call.name, self.agent.key)
                result, summary = prompts.tool_error("that tool is not available here"), call.name
            elif call.name == "remember":
                result, summary = await self._remember(call)
            elif self._over_budget:
                result, summary = prompts.TOOL_BUDGET_USED, call.name
            elif call.name in WEB_TOOLS and self._web_calls >= self.ctx.cfg["limits"]["max_web_calls"]:
                result, summary = prompts.WEB_BUDGET_USED, call.name
            else:
                if call.name in WEB_TOOLS:
                    self._web_calls += 1
                work = asyncio.create_task(run_tool(self.ctx.sandbox, conv_id, call,
                                                    ToolSettings.from_cfg(self.ctx.cfg)))
                incoming = asyncio.create_task(self.inbox.get())
                self._tool_running = True
                completed = False
                try:
                    await asyncio.wait({work, incoming}, return_when=asyncio.FIRST_COMPLETED)
                    received = await _cancel_task(incoming)
                    incoming = None
                    if isinstance(received, str):
                        pending.append(received)
                    pending.extend(self._drain())
                    if pending:
                        await self.ctx.sandbox.kill(conv_id)
                        await _cancel_task(work)
                        result, summary = prompts.INTERRUPTED, call.name
                    else:
                        result, summary = work.result()
                    completed = True
                finally:
                    await _cancel_task(incoming)
                    await _cancel_task(work)
                    # Keep the flag true on cancellation so run's cleanup kills
                    # the command, not only its local HTTP request.
                    if completed:
                        self._tool_running = False
            await self._step("tool_result", {"tool_call_id": call.id, "content": result})
            if self.message_id:
                await store.emit(conv_id, "message.tool", {"id": self.message_id, "tool": call.name, "summary": summary})
            else:
                self._pending_tools.append((call.name, summary))
        after = await self._files() if uses_sandbox else {}
        for path, f in after.items():
            old = before.get(path)
            if old is None or (old.get("size"), old.get("mtime")) != (f.get("size"), f.get("mtime")):
                await store.emit(conv_id, "file.changed", {"path": path, "size": f["size"], "agent": self.agent.key})
        return pending

    async def run(self) -> str | None:
        self._started = True
        self.task = asyncio.current_task()
        pending, steps = [], 0
        try:
            if self._cancelled:
                raise asyncio.CancelledError
            if not self._held:
                await self.ctx.typing(self.agent.key, True)
                self._typing = True
            carried = []  # steers of a call that is written again at deep effort
            while True:
                # Steers received between calls must affect the *next* call.
                async with self.ctx.lock:
                    pending.extend(self._drain())
                    state = await load_state(self.ctx.store, self.ctx.conv_id, self.agent,
                                             self.ctx.roster, self.ctx.cfg["limits"], self.effort)
                    by_id = {m["id"]: m["text"] for m in state.messages}
                    own_id = self.message_id
                    if (self._held and self._buffer) or self._pending_steps:
                        own_id = "held-partial"
                        state.messages.append({"id": own_id, "author": self.agent.key,
                                               "text": "".join(self._buffer), "status": "streaming",
                                               "pos": max((m["pos"] for m in state.messages), default=0) + 1})
                        state.steps.extend({"agent": self.agent.key, "message_id": own_id,
                                            "kind": kind, "data": data}
                                           for kind, data in self._pending_steps)
                    steers = [mid for mid in pending or carried if mid in by_id]
                    built = build_context(self.agent, state, purpose="speak", own_message_id=own_id,
                                          steer=[by_id[mid] for mid in steers] or None,
                                          directive=self.directive)
                    profile = "steer" if pending else self.effort
                    carried = []
                    if profile == "steer":
                        # The continuation follows the text the reader already saw.
                        self._seam_visible = self._visible(by_id)
                    pending = []
                    if self._held:
                        self._held_context = built
                    else:
                        await self.ctx.context(self.agent.key, built)
                budget = self.ctx.cfg["limits"]["max_tool_steps"][self.effort]
                # Once a run has used tools, keep offering the same tools even past its budget (the calls
                # then get "budget used up"): withdrawing them mid-run makes some models write raw tool
                # markup as text. A hard ceiling still ends a model that keeps calling.
                grace = self.ctx.cfg["limits"]["tool_budget_grace_steps"]
                tools = list(TOOL_SPECS) if steps < budget or (budget and steps < budget + grace) else []
                if self.ctx.registry is not None and self.ctx.can_remember and not self._remembered:
                    tools.append(REMEMBER_SPEC)
                tools = tools or None
                self._offered = {tool.name for tool in tools or ()}
                calls, pending = await self._stream(built, profile, tools)
                if pending:
                    continue
                if self._held:
                    pending = await self._wait_release()
                    if pending:
                        continue
                if calls:
                    pending = await self._tools(calls, over_budget=steps >= budget)
                    steps += 1
                    self._after_tool = True
                    continue
                if self._wanted_tools and not budget and not self._escalated:
                    # The model answered by trying to use a tool this effort does not offer (e.g. "improve
                    # the file you wrote"). Its markup was dropped, so it would end empty-handed: the same
                    # message is written again once at deep effort, which has the tools, keeping the steer
                    # it was answering. What was already shown stays; the rest starts a new paragraph.
                    # (The message keeps the effort it started with.)
                    self._escalated = True
                    self.effort = "deep"
                    carried = steers
                    self._after_tool = True
                    continue
                async with self.ctx.lock:
                    pending = self._drain()
                    if pending:
                        continue
                    self._closing = True
                    if self.message_id:
                        await self.ctx.store.finish_message(self.message_id, "done")
                    self.finished = True  # steer() observes this before the lock is released.
                    return self.message_id
        except asyncio.CancelledError:
            self._cancelled = True
            # Deliberately discard unflushed text: hard stop must not add output.
            self._buffer.clear()
            raise
        except Exception:
            log.exception("Agent run failed for %s", self.agent.key)
            await self._say_failed()
        finally:
            if self._tool_running:
                try:
                    await self.ctx.sandbox.kill(self.ctx.conv_id)
                except Exception:
                    log.warning("Cannot kill sandbox command", exc_info=True)
            async with self.ctx.lock:
                if not self.finished and self.message_id:
                    await self.ctx.store.finish_message(self.message_id, "stopped" if self._cancelled else "done")
                self.finished = True
                if self._typing:
                    await self.ctx.typing(self.agent.key, False)
                    self._typing = False
        return self.message_id
