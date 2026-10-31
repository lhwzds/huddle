---
title: Steering mid-answer
---

# Steering mid-answer

<sub>**English** · [中文](zh/03-steering.md)</sub>

A steer is a user message that arrives while an agent is writing. Huddle does not stop the agent and start a new reply: the same message continues from exactly what the reader has seen, with the request applied. The loop is Huddle's own (no agent framework), because it has to race a model stream against an inbox and keep the event log consistent. The [architecture overview](../architecture.md) has the sequence diagram.

<sub>Sources: [agent.py:65-97](../../backend/huddle/agent.py#L65-L97) · [AGENTS.md:5-6](../../AGENTS.md#L5-L6) · [CONTRIBUTING.md:3-4](../../CONTRIBUTING.md#L3-L4)</sub>

## The run loop

`AgentRun.run` repeats one step. Under the conversation lock it drains the inbox, loads a fresh state and builds the context; if steers are pending, that call uses the `steer` profile and includes them. It then streams one model response. Afterwards:

- steers arrived → loop again at once (a continuation);
- held → wait for release or a steer;
- tool calls → run them, then loop (the tool budget is on [Groups, memory and tools](04-groups-and-tools.md));
- no tools offered, but the model tried one (`_wanted_tools`) → once, write the same message again at `deep` effort, keeping its steer;
- otherwise → under the lock, drain the inbox once more and only if it is empty mark the message `done`.

`finished` is set before that lock is released, and `steer()` refuses a finished run. A steer is therefore either taken by this run or refused and handled as a new reply; it is never lost between the two. `_text` drops blank lines at the start of a message, so an empty reply leaves no empty message.

<sub>Sources: [agent.py:384-424](../../backend/huddle/agent.py#L384-L424) · [agent.py:435-466](../../backend/huddle/agent.py#L435-L466) · [agent.py:177-180](../../backend/huddle/agent.py#L177-L180) · [agent.py:130-134](../../backend/huddle/agent.py#L130-L134)</sub>

## Racing the stream against the inbox

`_stream` keeps two tasks alive: the next model event and `inbox.get()`. Text is buffered and flushed as `message.delta` in small time-based batches (`flush_interval_s`). When a steer wins the race, a delta that already arrived is still consumed and flushed, never dropped, and the `finally` block closes the generator, which closes the provider's HTTP stream. The reader keeps exactly what was published, and the next call starts from it.

<sub>Sources: [agent.py:206-270](../../backend/huddle/agent.py#L206-L270)</sub>

## Continuing the same message

For a continuation, `build_context` moves the agent's own partial message to the end as an `assistant` entry and adds a `steer_note`: what the user wrote, the last characters already shown, and the rule to carry on from there without repeating, restarting or acknowledging the interruption. The `steer` profile asks for the provider's lowest reasoning setting, which is what keeps the first redirected word fast.

New text joins the old with `seam`: "… " when the cut fell mid-word, a space after punctuation. A leading ellipsis from the model is stripped so the seam never doubles. Everything goes to the same message id, so the chat shows one message that changes direction.

<sub>Sources: [context.py:97-106](../../backend/huddle/context.py#L97-L106) · [prompts.py:22-35](../../backend/huddle/prompts.py#L22-L35) · [prompts.py:164-182](../../backend/huddle/prompts.py#L164-L182) · [agent.py:181-189](../../backend/huddle/agent.py#L181-L189)</sub>

## Steers during tool work

Between model calls a steer simply lands in the next call. While a command runs, `_tools` races the tool task against the inbox; a steer kills the workspace's commands through `sandbox.kill` and answers the call with an "interrupted" result, as it does for any remaining calls in the batch. A held run waiting for dedupe also listens to its inbox, so a steer is not lost while the run is invisible.

<sub>Sources: [agent.py:318-382](../../backend/huddle/agent.py#L318-L382) · [agent.py:114-128](../../backend/huddle/agent.py#L114-L128)</sub>

## Who is steered

A message that names agents steers only those runs; the others keep writing untouched. A message that names nobody steers every running agent. A run that refuses (it just finished) makes that agent a candidate for a new reply instead. When the message only steered writers, the new turn is a *pure steer*: idle agents still decide, but nobody is forced to answer it.

<sub>Sources: [orchestrator.py:189-213](../../backend/huddle/orchestrator.py#L189-L213)</sub>

## Stopping

A message that is only a stop phrase (optionally with names; the phrases are in `config/language.yaml`), or the Stop button, ends work instead of steering. Under the lock `_stop_locked` cancels the targeted runs and every instruction extraction, marks their streaming messages `stopped` and clears typing; the cancelled tasks are awaited only after the lock is released, so a newer turn's output is never swept up. A cancelled run discards its unflushed buffer: a stop adds no text. An unnamed follow-up right after a stop ("ok, the short version") is addressed to the stopped agent.

<sub>Sources: [orchestrator.py:502-550](../../backend/huddle/orchestrator.py#L502-L550) · [orchestrator.py:330-340](../../backend/huddle/orchestrator.py#L330-L340) · [agent.py:467-471](../../backend/huddle/agent.py#L467-L471) · [language.yaml:5-6](../../config/language.yaml#L5-L6)</sub>

## Standing instructions

"From now on, answer in Chinese" must affect the steered message and every later one. Each user message also goes to a background extractor that stores durable directives, for everyone or one agent. Extractions are chained in arrival order, so a later directive overrides an earlier one even in a burst; the next turn waits up to `extract_timeout_s` for the previous extraction. The steer itself already applies the request to running messages.

Directives sit near the end of every system prompt, just before the identity rule, and return as a reminder at the end of each call. That rule is the one limit: a directive may change language, length, format or focus, never who the agent is.

<sub>Sources: [orchestrator.py:201-207](../../backend/huddle/orchestrator.py#L201-L207) · [orchestrator.py:218-242](../../backend/huddle/orchestrator.py#L218-L242) · [orchestrator.py:361-366](../../backend/huddle/orchestrator.py#L361-L366) · [prompts.py:78-86](../../backend/huddle/prompts.py#L78-L86)</sub>

## One lock per conversation

Every decision that must agree with the log (creating a message, draining an inbox before `done`, a stop, a snapshot) runs under the conversation's `ConversationLock`. It is re-entrant within one task, so a run can hold it while calling `Store` methods that take it again, but never shared with child tasks. If the process dies mid-answer, startup marks every `streaming` message `stopped`; nothing is reordered or rewritten.

<sub>Sources: [store.py:55-90](../../backend/huddle/store.py#L55-L90) · [store.py:362-364](../../backend/huddle/store.py#L362-L364)</sub>
