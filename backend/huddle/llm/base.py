"""Provider-neutral chat and streaming types."""
from dataclasses import dataclass
from typing import AsyncIterator, Literal, Protocol
import json

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str

@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    reasoning: str | None = None  # assistant only: reasoning to pass back with its tool calls

@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict

@dataclass
class Usage:
    input_tokens: int
    output_tokens: int

@dataclass
class TextDelta:
    text: str

@dataclass
class ToolCallDone:
    call: ToolCall

@dataclass
class Done:
    finish_reason: str
    usage: Usage | None = None
    reasoning: str = ""  # the call's reasoning text, when the provider returns it

class LLM(Protocol):
    def stream(self, messages: list[ChatMessage], *, profile: str, max_tokens: int, tools: list[ToolSpec] | None = None) -> AsyncIterator[TextDelta | ToolCallDone | Done]: ...

async def complete_json(llm, messages, *, profile, max_tokens) -> dict:
    chunks = []
    async for event in llm.stream(messages, profile=profile, max_tokens=max_tokens):
        if isinstance(event, TextDelta):
            chunks.append(event.text)
    text = "".join(chunks)
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char == "{":
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    # Only attempt repair after looking for a complete object. Track strings and
    # containers so braces inside quoted content do not affect the suffix.
    for index, char in enumerate(text):
        if char != "{":
            continue
        fragment = text[index:]
        stack = []
        quoted = escaped = False
        for c in fragment:
            if quoted:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == '"':
                    quoted = False
            elif c == '"':
                quoted = True
            elif c in "{[":
                stack.append(c)
            elif c in "}]":
                if not stack or stack[-1] != ({"}": "{", "]": "["}[c]):
                    break
                stack.pop()
        else:
            # An unfinished escape cannot precede the closing quote.
            suffix = (('\\' if escaped else '') + '"' if quoted else '')
            suffix += ''.join('}' if c == '{' else ']' for c in reversed(stack))
            try:
                value = json.loads(fragment + suffix)
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                pass
    raise ValueError(f"No JSON object in model response: {text[:300]}")
