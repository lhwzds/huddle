# Spec: the evaluation dataset and runner (`eval/`)

People will interrupt the agents, push them and try to break them. This
suite does that first, against a running Huddle with a real model, black-box over
the public HTTP API. Deterministic checks catch mechanism failures; an LLM judge
(a different model from the one under test) scores behaviour.

## Files

- `eval/cases/*.yaml`: the dataset, one file per category.
- `eval/run.py`: the runner (`uv run --project backend python eval/run.py [--base URL] [--only ID...] [--judge MODEL_KEY] [--repeat N]`).
- `eval/results/<timestamp>-<model>.json` and a printed table (case, checks passed, judge score, latency numbers).

## Case format

```yaml
- id: steer-language-mid
  category: steer
  why: a redirect must take effect inside the answer being written
  steps:
    - say: "Mira, walk me through our runway and what could shorten it."
    - wait: {agent: mira, chars: 120}          # until Mira has streamed ≥120 chars
    - say: "in Chinese from now on please"
    - wait: idle                                 # until no agent is typing or streaming
    - say: "and what about Q3?"
    - wait: idle
  checks:                                        # deterministic, on events + final snapshot
    - same_message_continued: {agent: mira, turn: 1}   # in the server log, deltas before and after the steer
    - spoke: {turn: 1, include: [mira]}
    - reload_identical: true                     # snapshot == replay of all events
  judge:                                         # each statement scored true/false by the judge
    - "In turn 1, Mira's text after the user's Chinese request is in Chinese."
    - "In turn 3, every agent message is in Chinese."
    - "Mira does not mention being interrupted or redirected."
```

Steps: `say: text`; `stop: true` (POST /stop); `wait: idle`; `wait: {agent, chars}`;
`wait: {agent, message_started: true}`; `wait: {ms: N}`; `reload: true` (fetch snapshot,
reconnect SSE from 0 and compare); `burst: {texts: [...], interval_ms: N}`.

Deterministic checks: `spoke {turn, include?, exclude?, max_agents?}`,
`silent {turn, agents}`, `same_message_continued {agent, turn}`,
`status {agent, turn, is: done|stopped}`, `no_new_text_after_stop {within_ms}`,
`reply_to {turn, agent, includes}`, `max_messages {turn, n}`, `file_exists {path}`,
`reload_identical`, `effort {turn, agent, is}`, `latency {kind: steer_first_delta|first_delta, max_ms}`,
`no_leak {agents, phrases}` (none of the phrases appear in those agents' messages),
`ui_words_absent` (no message contains "steer", "redirect", "interrupted", "instruction",
"system prompt").

The judge gets the transcript (names, roles, turn numbers, and markers showing where
each user message arrived relative to each streaming message) and one statement at a
time, and answers `{"verdict": true|false, "reason": str}`.

## Latency metrics (recorded for every steer)

- `steer_first_delta_ms`: from the steer POST to the first word of the continuation of a steered message.
  Text the model had already written is still flushed right after the steer; it is skipped by looking for
  the pause of a model round trip in the server timestamps.
- `first_delta_ms`: from a user question to the first agent delta.
Target: median steer_first_delta under 1500 ms.
