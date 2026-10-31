# Spec: testing

Two layers:

1. Deterministic tests with a scripted fake model. These cover ordering,
   steering, stop, persistence and limits. They run in CI with no network.
2. An evaluation dataset run against a real model (later, `eval/`). It covers
   distinct voices, combining knowledge, honoring steers and staying quiet.

## The fake model (`huddle/llm/fake.py`)

```python
@dataclass
class FakeReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    chunk: int = 4              # characters per TextDelta
    delay: float = 0.01         # seconds between deltas
    first_delay: float = 0.0    # seconds before the first delta

Rule = Callable[[FakeCall], FakeReply | None]   # first rule returning a reply wins

@dataclass
class FakeCall:
    agent: str | None           # agent key found in the system message ("You are Mira" -> "mira"), else None
    profile: str
    messages: list[ChatMessage]
    tools: list[ToolSpec] | None
    @property
    def last_user(self) -> str  # content of the last user message

class FakeLLM:                  # implements the LLM protocol
    def __init__(self, rules: list[Rule], default: FakeReply = FakeReply('{"speak": false}')): ...
    calls: list[FakeCall]       # every call, in order, for assertions
    cancelled: int              # how many streams were closed before they finished
```

`stream` records the call, waits `first_delay`, yields `TextDelta`s of `chunk`
characters with `delay` between them, then `ToolCallDone` for each tool call,
then `Done`. Closing the generator mid-stream increments `cancelled`.

Helpers for tests: `when(agent=None, profile=None, contains=None) -> predicate` and
`rule(predicate, reply)`.

`provider: fake` in config builds a `FakeLLM` with built-in rules (each agent says one
short in-character line on decide/speak), so the app can be clicked through with no key.

## Test harness (`backend/tests/conftest.py`)

- `store` fixture: a store on a temp SQLite file.
- `app_client` fixture: the FastAPI app with a `FakeLLM` and a fake sandbox
  (in-memory dict of files, `exec` returns a canned result), via `httpx.AsyncClient`
  + `ASGITransport`.
- `collect_events(conv_id, until=predicate, timeout=5)`: subscribes to the SSE stream
  and returns parsed events.
- Use `pytest-asyncio` in auto mode. No test sleeps longer than needed; a full test
  run stays under 60 s.

## Required tests

Store and API (`tests/test_store.py`, `tests/test_api.py`):
- seq starts at 1, has no gaps, and stays unique under 200 concurrent appends from
  10 tasks.
- `message.created` sets `pos = seq`; deltas concatenate into `text`; the snapshot
  equals replaying all events from scratch.
- SSE: `after=N` replays exactly `seq > N`; `Last-Event-ID` works; events appended
  while a client is between replay and live are neither lost nor duplicated
  (append in a loop while connecting).
- startup marks `streaming` messages `stopped`.
- 404 for an unknown conversation, 422 for an empty message.

Runtime (`tests/test_runtime.py`), all with `FakeLLM`:
- **parallel answers**: two agents decide to speak and stream at the same time;
  their deltas interleave in the event log; each message's `pos` is fixed at creation.
- **silence**: an agent with `speak: false` creates no message and no typing event
  after the decision.
- **dedupe**: two agents with the same point → the utility call drops one; an agent
  addressed by name is never dropped.
- **steer mid-stream**: while Mira streams a long reply, the user sends "in Chinese
  please". Then:
  - Mira's stream is closed (`cancelled == 1`);
  - the next call for Mira has profile `steer`, its last assistant entry equals her
    stored partial text exactly, and its last user entry contains the steer text;
  - the continuation's deltas go to the **same** message id;
  - the final text = the partial text + the continuation.
- **steer by name**: Mira and Theo both stream; "Theo, shorter" → only Theo's stream
  is cancelled; Mira's run makes no new call; Theo continues in the same message.
- **steer at the very end**: the steer is delivered after the last delta but before
  `message.done` (hook in FakeLLM) → the run continues instead of finishing.
- **steer during a tool call**: a `bash` tool call is running in the fake sandbox
  (blocks on an event); a steer kills it (`kill` called), and the next model call
  sees an "interrupted" tool result and the steer.
- **stop**: during streaming of three agents, `/stop` → every stream cancelled,
  every message `stopped`, typing false for all, and no events for those messages after
  their `message.done`.
- **stop word**: "stop" as a message behaves like `/stop`; "Mira, stop" stops only Mira.
- **standing instruction**: the extractor returns `{"agent": null, "text": "Answer in
  Chinese"}` → it is stored; the system message of every later call (any agent) contains
  it; after building a new `Orchestrator` on the same database (a "restart") it is still
  there.
- **agent-to-agent**: Juno's message addresses Theo → in round 1 Theo decides and answers
  with `reply_to: ["juno"]`; when nobody wants to speak the turn ends; with agents that
  always want to speak, the turn ends at `max_agent_rounds` / `max_agent_messages_per_turn`.
- **new user message supersedes rounds**: a user message while round 1 is deciding
  cancels that turn task; the new turn starts.
- **burst**: 20 user messages within 200 ms while agents stream → every message is
  stored once, `pos` order equals arrival order, no exception, all runs end.
- **effort**: decide returns `light` → the run's call has profile `light`, no tools,
  and `max_tokens == limits.max_tokens.light`.
- **privacy**: Mira's private knowledge never appears in any message sent to Theo's or
  Juno's calls.
- **reload**: after a full multi-agent turn with a steer, the snapshot messages (order,
  text, status) equal the state rebuilt by applying all SSE events from seq 0.
