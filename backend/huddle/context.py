"""Per-agent, privacy-preserving context snapshots and deterministic token budgeting."""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from . import prompts
from .config import AgentSpec
from .llm import ChatMessage, ToolCall


@dataclass
class Built:
    messages: list[ChatMessage]
    used: int
    limit: int


@dataclass
class ContextState:
    roster: list[AgentSpec]
    messages: list[dict]
    instructions: list[dict]
    steps: list[dict]
    limits: dict
    effort: str = "normal"


async def load_state(store, conv_id, agent, roster, limits, effort="normal") -> ContextState:
    # The same lock used by writes makes text and private tool steps one snapshot.
    async with store.lock(conv_id):
        return ContextState(roster, await store.messages(conv_id),
                            await store.instructions(conv_id), await store.steps(conv_id, agent.key),
                            limits, effort)


def estimate_tokens(text: str) -> int:
    ascii_chars = sum(ord(c) < 128 for c in text)
    return ceil(ascii_chars / 4) + len(text) - ascii_chars


def _cost(messages):
    return sum(estimate_tokens(m.content) + sum(estimate_tokens(c.name + c.arguments) for c in m.tool_calls or [])
               for m in messages)


def _merge_users(messages):
    merged = []
    for m in messages:
        if merged and m.role == merged[-1].role == "user":
            merged[-1] = ChatMessage("user", merged[-1].content + "\n\n" + m.content)
        else:
            merged.append(m)
    return merged


def _tool_entries(steps):
    """Keep a batch of tool calls together before its results (OpenAI wire order)."""
    entries = []
    for step in steps:
        data = step["data"]
        if step["kind"] == "tool_call":
            call = ToolCall(data["id"], data["name"], data["arguments"])
            if entries and entries[-1].role == "assistant" and entries[-1].tool_calls:
                entries[-1].tool_calls.append(call)
            else:
                entries.append(ChatMessage("assistant", tool_calls=[call], reasoning=data.get("reasoning") or None))
        elif step["kind"] == "tool_result":
            entries.append(ChatMessage("tool", data["content"], tool_call_id=data["tool_call_id"]))
    return entries


def build_context(agent, state: ContextState, *, purpose, own_message_id=None, steer=None,
                  directive=None) -> Built:
    roster = {a.key: a for a in state.roster}
    transcript = sorted(state.messages, key=lambda m: m["pos"])
    own_recent = [m["id"] for m in transcript if m["author"] == agent.key][-state.limits["own_step_messages"]:]
    system = ChatMessage("system", prompts.system(agent, state.roster, state.instructions, state.effort,
                                                  web_calls=state.limits.get("max_web_calls")))
    # Trim whole transcript-message blocks so a tool result can never be orphaned.
    blocks: list[tuple[list[ChatMessage], bool]] = []
    partial = ""
    current = None
    for message in transcript:
        author = message["author"]
        entries = []
        if author == agent.key:
            if message["id"] in own_recent or message["id"] == own_message_id:
                entries.extend(_tool_entries([s for s in state.steps
                                              if s["agent"] == agent.key and s["message_id"] == message["id"]]))
            entries.append(ChatMessage("assistant", message["text"]))
        elif author == "user":
            entries.append(ChatMessage("user", prompts.transcript_user(message["text"])))
        elif author in roster:
            entries.append(ChatMessage("user", prompts.transcript_peer(
                roster[author], message["text"], message["status"] == "streaming")))
        if message["id"] == own_message_id:
            partial = message["text"]
            current = (entries, True)
        elif entries:
            blocks.append((entries, False))
    if current:
        blocks.append(current)  # Its pos stays fixed in storage, but it is the continuation prefix.
    if steer:
        texts = [s["text"] if isinstance(s, dict) else s for s in ([steer] if isinstance(steer, str) else steer)]
        blocks.append(([ChatMessage("user", prompts.steer_note(texts, partial))], True))

    # A directive (e.g. "write the group's conclusion") and the standing-instruction reminder
    # ride on the final user entry, so they are the last thing the model reads.
    reminder = "\n\n".join(x for x in (directive, prompts.instruction_reminder(state.instructions, agent.key)) if x)

    def flattened():
        messages = [system] + _merge_users([m for entries, _ in blocks for m in entries])
        if reminder:
            # Copy the entry rather than modifying a transcript block during budgeting.
            index = next((i for i in range(len(messages) - 1, 0, -1)
                          if messages[i].role == "user"), None)
            if index is None:
                messages.append(ChatMessage("user", reminder))
            else:
                messages[index] = ChatMessage("user", messages[index].content + "\n\n" + reminder)
        return messages

    limit = state.limits["context_tokens"]
    messages = flattened()
    while _cost(messages) > limit:
        oldest = next((i for i, (_, protected) in enumerate(blocks) if not protected), None)
        if oldest is None:
            break  # Protected content may itself exceed the budget; never corrupt a continuation.
        blocks.pop(oldest)
        messages = flattened()
    return Built(messages, _cost(messages), limit)
