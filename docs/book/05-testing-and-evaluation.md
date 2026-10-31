---
title: Testing and evaluation
---

# Testing and evaluation

<sub>**English** · [中文](zh/05-testing-and-evaluation.md)</sub>

Two layers check Huddle. Unit and integration tests run offline against a scripted model; the evaluation runner drives a running instance with a real model and scores the conversation it gets back. The test commands are in [CONTRIBUTING.md](../../CONTRIBUTING.md); the test plan is in [the testing spec](../specs/testing.md), and [the eval spec](../specs/eval.md) has the dataset format and the runner command.

<sub>Sources: [AGENTS.md:5-12](../../AGENTS.md#L5-L12)</sub>

## Offline tests

Backend tests never touch the network. `FakeLLM` replies by rules keyed on agent, profile and prompt text, with configurable chunking and delays, so a test can make Theo stream slowly and send a steer mid-message. `FakeSandbox` records commands and can block one until released, which is how steering during a tool call is tested. Most runtime cases (steering, stop phrases, addressing, rounds, seams) live in `backend/tests/test_runtime.py`.

Frontend tests (Vitest) cover the event reducer, including gap detection, plus group, profile and follow-scroll helpers. Sandbox tests start the real service and run real processes. Eval tests run the checks, metrics and transcript markers on small synthetic event logs. CI runs all four suites on every push and pull request; [CONTRIBUTING.md](../../CONTRIBUTING.md) lists the same commands.

<sub>Sources: [fake.py:1-46](../../backend/huddle/llm/fake.py#L1-L46) · [conftest.py:13-38](../../backend/tests/conftest.py#L13-L38) · [test_runtime.py:277-308](../../backend/tests/test_runtime.py#L277-L308) · [state.test.ts:44-58](../../frontend/test/state.test.ts#L44-L58) · [test_server.py:15-30](../../sandbox/tests/test_server.py#L15-L30) · +2</sub>

## The evaluation dataset

`eval/cases/*.yaml` holds 36 scripted conversations across all three groups. A case lists steps (`say`, `wait` for an agent to reach some characters or for idle, `stop`, `reload`, `burst`), deterministic `checks`, and plain-language `judge` statements. Categories cover steering, stopping, silence, exchanges between agents, combining knowledge, adversarial input (prompt injection, hostile shell commands) and the larger groups.

<sub>Sources: [steer.yaml:1-30](../../eval/cases/steer.yaml#L1-L30) · [core.py:14-20](../../eval/huddle_eval/core.py#L14-L20) · [core.py:106-136](../../eval/huddle_eval/core.py#L106-L136)</sub>

## How the runner scores

`eval/run.py` is black-box: it creates a conversation over HTTP, follows the SSE stream with receive timestamps, and plays the steps. "Idle" is the server's word, not a quiet period: the snapshot's `active` flag is false, nothing is typing or streaming, and the last send is at least 1.5 s old, for two polls in a row, so a slow decide call is not mistaken for the end. At the end it reloads and freezes the events at the snapshot's `last_seq`.

Deterministic checks are the hard gate: `same_message_continued` (a message has deltas stored both before and after the later user message, in the server's `seq` order, not by client clocks), `no_new_text_after_stop`, `reload_identical` (snapshot equals a replay of the event log), privacy `no_leak` phrases, `ui_words_absent` (no talk of steering or instructions), speaker sets, effort, files and latency. The judge is a configured model (`--judge`, else the active one) that reads a transcript with "user message arrives" markers inserted at the exact character offsets, and answers each statement with a verdict and reason.

<sub>Sources: [runner.py:310-375](../../eval/huddle_eval/runner.py#L310-L375) · [runner.py:46-53](../../eval/huddle_eval/runner.py#L46-L53) · [core.py:93-105](../../eval/huddle_eval/core.py#L93-L105) · [core.py:142-160](../../eval/huddle_eval/core.py#L142-L160) · [core.py:240-270](../../eval/huddle_eval/core.py#L240-L270) · +1</sub>

## Latency metrics

Two numbers per send. First word: time from the send returning to the first text of that turn's agent messages. Steer latency: time from the send's POST to the first word of the continuation in a message that was already streaming, restricted to the agent named in the send when there is one. Text the model wrote before the steer is still flushed just after it, so `continuation_arrival` takes the first delta stored after the steer that comes at least `CONTINUATION_GAP_S` (a model round trip) after both the steer and the previous delta on the server's clock; it is an estimate, since a stream that pauses that long on its own counts as the continuation. Results are written as JSON per run.

<sub>Sources: [core.py:164-237](../../eval/huddle_eval/core.py#L164-L237)</sub>
