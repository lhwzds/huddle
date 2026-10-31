---
title: How a turn works
---

# How a turn works

<sub>**English** · [中文](zh/02-how-it-works.md)</sub>

The [architecture overview](../architecture.md) draws one user message end to end. This page follows the code: how events are ordered, what happens when a message arrives, how agents decide to speak, and why a turn ends by itself. Steering inside a message is on [Steering mid-answer](03-steering.md); groups, discussion and tools are on [Groups, memory and tools](04-groups-and-tools.md).

<sub>Sources: [orchestrator.py:355-385](../../backend/huddle/orchestrator.py#L355-L385)</sub>

## One ordered event log

Every visible change goes through `Store`: `add_message`, `append_text`, `finish_message` and `emit`. Each takes the conversation's lock, bumps `conversations.last_seq`, writes the table change and an `events` row in one SQLite transaction, and publishes to the in-process `Bus` only after commit. `_transaction` shields the commit, so a cancelled task still publishes what was committed: an event in the database is never missing from the live stream.

A message's `pos` is the `seq` of its `message.created` event, so its place in the chat is fixed when it is created. Deltas and `message.tool` are refused for a message that is no longer `streaming`.

<sub>Sources: [store.py:180-295](../../backend/huddle/store.py#L180-L295) · [bus.py](../../backend/huddle/bus.py)</sub>

## Snapshot, replay and gaps

The browser loads `GET /api/conversations/{id}`: messages, `last_seq`, agent state and an `active` flag, all read under the same lock, so they describe one instant. It then opens the SSE stream with `?after=last_seq`. The server subscribes to the bus *before* reading the log, replays stored events, then forwards live ones, skipping any `seq` at or below its cursor, so the overlap is harmless.

The client applies an event only if it is exactly `lastSeq + 1`. On a gap, `connect` closes the stream and `App` reloads the snapshot; on a network error it reconnects from its cursor. A reload therefore always shows the same order as the live view.

<sub>Sources: [store.py:297-313](../../backend/huddle/store.py#L297-L313) · [api.py:173-189](../../backend/huddle/api.py#L173-L189) · [api.py:249-280](../../backend/huddle/api.py#L249-L280) · [state.ts:32-35](../../frontend/src/state.ts#L32-L35) · [api.ts:123-166](../../frontend/src/api.ts#L123-L166) · +1</sub>

## When a message arrives

`on_user_message` does all of its bookkeeping under the conversation lock: increment the turn, store the message, and work out its `targets` (agents named in it, or everyone for a whole-group address; the word lists live in `config/language.yaml`). Then one of two things:

- A pure stop phrase cancels the targeted runs (see [Steering mid-answer](03-steering.md)).
- Otherwise, running agents that are targeted (or all of them, when nobody is named) get the message in their inbox. Instruction extraction starts in the background, the previous turn driver is cancelled, and a new `_turn` task starts with the idle agents as candidates.

Cancelling the old driver never cancels writers: runs are awaited through `asyncio.shield`, so a new message changes who speaks next, not what is already being written.

<sub>Sources: [orchestrator.py:168-216](../../backend/huddle/orchestrator.py#L168-L216) · [orchestrator.py:457-461](../../backend/huddle/orchestrator.py#L457-L461) · [language.yaml:5-22](../../config/language.yaml#L5-L22)</sub>

## Deciding who speaks

Each round, every candidate makes a small `decide` call from its *own* context (so private memory shapes the decision) and returns JSON: `speak`, `effort`, `addresses` and a short `point`. The prompt makes silence the default: speak only with a new fact, number, risk or proposal. `complete_json` tolerates prose around the JSON and repairs a cut-off object; a failed or malformed decision falls back to "speak only if addressed".

Code then bounds the model's choices in round 0: if nobody wants to speak, one agent is chosen (the addressed one, one with a point, or whoever spoke last), so every new question gets a reply (a message that only steered writers is the exception, see [Steering mid-answer](03-steering.md)); a request for a file raises the writer to `deep`; several `light` answers to an unaddressed question collapse to one; and some messages open a free discussion ([Groups, memory and tools](04-groups-and-tools.md)).

<sub>Sources: [orchestrator.py:249-278](../../backend/huddle/orchestrator.py#L249-L278) · [orchestrator.py:385-403](../../backend/huddle/orchestrator.py#L385-L403) · [orchestrator.py:414-425](../../backend/huddle/orchestrator.py#L414-L425) · [prompts.py:105-130](../../backend/huddle/prompts.py#L105-L130) · [base.py:49-98](../../backend/huddle/llm/base.py#L49-L98)</sub>

## Parallel start, held output

When two or more agents want to speak, their runs start immediately but *held*: text accumulates in the run's buffer and no message is created. Meanwhile `_dedupe` asks a utility call which proposed points repeat each other or the recent transcript. Agents the user addressed are never dropped and at least one speaker is kept. `_resolve_held` then releases the kept runs, which create their message and flush the buffer, and cancels the rest, which never become visible.

This overlaps generation with deduplication, so a busy turn pays no extra latency for staying quiet. A message's position is fixed when it is published, not when the agent started thinking.

<sub>Sources: [orchestrator.py:431-455](../../backend/huddle/orchestrator.py#L431-L455) · [orchestrator.py:280-323](../../backend/huddle/orchestrator.py#L280-L323) · [agent.py:99-128](../../backend/huddle/agent.py#L99-L128) · [agent.py:160-197](../../backend/huddle/agent.py#L160-L197)</sub>

## Follow-up rounds and the end of a turn

After a round finishes, the next candidates depend on what was actually written. `_message_targets` reads only the finished text: an `@Name`, a name followed by a question in the same sentence, or a hand-off ("that's Theo's call"); the patterns are in `config/language.yaml`. The `addresses` field from `decide` is ignored, because models listed everyone there and every message became an invitation.

Outside a discussion or combine round, only addressed idle agents are candidates. The loop stops when nobody speaks, the round limit is reached, or the per-turn message cap is hit; `_turn_cap` grows the cap for large groups so everyone can speak once. That is why a turn ends on its own instead of turning into an endless exchange.

<sub>Sources: [orchestrator.py:325-328](../../backend/huddle/orchestrator.py#L325-L328) · [orchestrator.py:342-353](../../backend/huddle/orchestrator.py#L342-L353) · [orchestrator.py:367-380](../../backend/huddle/orchestrator.py#L367-L380) · [orchestrator.py:462-469](../../backend/huddle/orchestrator.py#L462-L469) · [language.yaml:48-56](../../config/language.yaml#L48-L56) · +1</sub>

## What each agent sees

`build_context` builds a fresh prompt for every call. The system message holds the agent's soul, its memory, the group's shared context, the public roster and the standing instructions. The transcript follows: the agent's own messages as `assistant`, the user's and peers' as labelled `user` entries (a peer still writing is marked as such). Tool calls and results appear only for the agent's own recent messages (`own_step_messages`); nobody sees another agent's tool history or memory.

To fit `context_tokens`, whole transcript blocks are dropped oldest first, so a tool result is never separated from its call. The message being continued and a steer note are protected. A directive and the standing-instruction reminder are appended to the last user entry, the last thing the model reads.

<sub>Sources: [context.py:73-132](../../backend/huddle/context.py#L73-L132) · [prompts.py:49-86](../../backend/huddle/prompts.py#L49-L86)</sub>
