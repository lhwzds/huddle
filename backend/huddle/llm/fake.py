"""Scriptable streaming model for deterministic tests and trying the app without a key."""
import asyncio
import copy
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from .base import ChatMessage, ToolCall, ToolSpec, TextDelta, ToolCallDone, Done

@dataclass
class FakeReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    chunk: int = 4
    delay: float = 0.01
    first_delay: float = 0.0
    before_done: Callable[[], Awaitable[None]] | None = None
    reasoning: str = ""
    wanted_tools: bool = False  # as if the model had written tool markup as text

@dataclass
class FakeCall:
    agent: str | None
    profile: str
    messages: list[ChatMessage]
    tools: list[ToolSpec] | None
    max_tokens: int = 0

    @property
    def last_user(self) -> str:
        return next((m.content for m in reversed(self.messages) if m.role == "user"), "")

Rule = Callable[[FakeCall], FakeReply | None]

def when(agent=None, profile=None, contains=None):
    return lambda call: ((agent is None or call.agent == agent) and
                         (profile is None or call.profile == profile) and
                         (contains is None or contains in call.last_user))

def rule(predicate, reply):
    return lambda call: reply if predicate(call) else None

class FakeLLM:
    def __init__(self, rules: list[Rule] | None = None, default: FakeReply | None = None):
        self.rules = rules or []
        self.default = default if default is not None else FakeReply('{"speak": false}')
        self.calls: list[FakeCall] = []
        self.cancelled = 0

    async def stream(self, messages, *, profile, max_tokens, tools=None):
        system = next((m.content for m in messages if m.role == "system"), "")
        # The system prompt states the agent key explicitly; names can contain spaces or any script.
        match = re.search(r"Your agent key is ([^\s.]+)\.", system)
        call = FakeCall(match.group(1) if match else None, profile, copy.deepcopy(messages), copy.deepcopy(tools), max_tokens)
        self.calls.append(call)
        reply = next((r for fn in self.rules if (r := fn(call)) is not None), self.default)
        completed = False
        try:
            if reply.first_delay:
                await asyncio.sleep(reply.first_delay)
            if reply.chunk <= 0:
                raise ValueError("chunk must be positive")
            for i in range(0, len(reply.text), reply.chunk):
                if i and reply.delay:
                    await asyncio.sleep(reply.delay)
                yield TextDelta(reply.text[i:i + reply.chunk])
            for tool_call in reply.tool_calls:
                yield ToolCallDone(tool_call)
            if reply.before_done:
                await reply.before_done()
            completed = True
            yield Done("tool_calls" if reply.tool_calls else "stop", None, reply.reasoning,
                       wanted_tools=reply.wanted_tools)
        finally:
            if not completed:
                self.cancelled += 1
