# Spec: the runtime (context, agent runs, steering, orchestration)

Read `docs/specs/contracts.md` first. This spec covers `huddle/llm/`,
`huddle/context.py`, `huddle/agent.py`, `huddle/orchestrator.py`,
`huddle/prompts.py` and `huddle/tools.py`.

No agent framework (LangChain, LangGraph, …). Plain `asyncio` and `httpx`.

## 1. LLM interface (`huddle/llm/`)

```python
@dataclass
class ToolCall:   id: str; name: str; arguments: str          # arguments is a JSON string
@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] | None = None                     # assistant only
    tool_call_id: str | None = None                              # tool only
@dataclass
class ToolSpec:   name: str; description: str; parameters: dict   # JSON schema
@dataclass
class Usage:      input_tokens: int; output_tokens: int

# stream events
@dataclass
class TextDelta:  text: str
@dataclass
class ToolCallDone: call: ToolCall
@dataclass
class Done:       finish_reason: str; usage: Usage | None

class LLM(Protocol):
    def stream(self, messages: list[ChatMessage], *, profile: str,
               max_tokens: int, tools: list[ToolSpec] | None = None) -> AsyncIterator[TextDelta | ToolCallDone | Done]: ...

async def complete_json(llm, messages, *, profile, max_tokens) -> dict
    # runs stream(), concatenates text, parses the first top-level JSON object
    # (tolerates ```json fences and text around it); raises ValueError if none.

def make_llm(cfg, model_key) -> LLM            # provider factory
```

`openai_chat` provider:
- `POST {base_url}/chat/completions`, `stream: true`, `model`, `messages`,
  `max_tokens`, `tools` (OpenAI function format) when given, plus the profile params
  (contracts: config). `stream_options: {"include_usage": true}` only when
  `include_usage` is set.
- Bearer key read from `os.environ[api_key_env]` at construction; a missing key
  raises at startup with the env var name in the message (never the value).
- `delta.content` → `TextDelta`. `delta.reasoning_content` is ignored. Text inside
  `<think>…</think>` in content is dropped, including when the tags are split
  across chunks.
- Tool call deltas are accumulated by `index`; each completed call is yielded as
  `ToolCallDone` when the stream finishes (finish_reason `tool_calls` or end).
- Retries: up to 2 retries with 0.5 s / 1.5 s backoff on connect errors, 429 and 5xx,
  **only before the first event was yielded**.
- Cancellation: closing the async generator (`aclose()`) or cancelling the task
  that iterates it closes the HTTP response immediately.

`fake` provider: see `docs/specs/testing.md`.

## 2. Context (`huddle/context.py`)

Each agent has its own context. Nobody else's private knowledge or tool history is
in it.

`build_context(agent, state, *, purpose, own_message_id=None, steer=None) -> Built`
with `Built(messages: list[ChatMessage], used: int, limit: int)`.

- **System message**: `prompts.system(agent, roster, instructions, effort)`:
  - group chat rules;
  - the persona;
  - the private knowledge, introduced as "only you know this; the others cannot see it";
  - the roster: the other agents' names, roles and lenses, but **not** their knowledge;
  - standing instructions for everyone and for this agent, in the order they were given;
  - the effort guidance for this message.
- **Transcript**: every message of the conversation in `pos` order.
  - This agent's own messages are `assistant`. Its own `agent_steps` for a message
    are put before that message's text as assistant `tool_calls` + `tool` results.
    Only the steps of its 3 most recent messages are included.
  - User messages are `user` with content `User: <text>`.
  - Other agents' messages are `user` with content `<Name> (<role>): <text>`. If one is
    still streaming, its text ends with ` …(still typing)`.
  - Consecutive `user` entries are merged with blank lines between them.
- **The message being written** (`own_message_id` set): its current stored text is
  the final `assistant` entry, with its tool steps before it. The model continues it.
- **Steer** (`steer` = the user message(s) that interrupted): appended after the
  partial assistant text as a `user` entry built by `prompts.steer_note(...)`.
- **Budget**: tokens are estimated as `ceil(ascii_chars / 4) + non_ascii_chars`.
  Over `limits.context_tokens`, drop the oldest transcript entries, never the
  system message, the current partial message, or the steer. Return `used`.

## 3. Tools (`huddle/tools.py`)

Tools for agents, all running in the conversation's sandbox workspace:
`bash {cmd}`, `write_file {path, content}`, `read_file {path}`.

```python
class Sandbox:      # httpx client of the sandbox service (contracts: sandbox)
    async def exec(self, workspace, cmd, timeout_s) -> dict
    async def kill(self, workspace) -> int
    async def files(self, workspace) -> list[dict]
    async def read(self, workspace, path) -> dict
    async def write(self, workspace, path, content) -> dict

TOOL_SPECS: list[ToolSpec]
async def run_tool(sandbox, workspace, call: ToolCall) -> tuple[str, str]
    # returns (result_text_for_the_model, one_line_summary_for_the_ui)
    # summaries: "ran `<cmd shortened to 60 chars>`", "wrote <path>", "read <path>"
    # bad JSON arguments or unknown tool -> an error result text, never an exception
```

The tool result text for the model is capped at 6000 characters.

## 4. Agent run (`huddle/agent.py`) — one agent writing one message

```python
class AgentRun:
    def __init__(self, ctx: RunContext, agent: AgentSpec, turn: int, effort: str,
                 reply_to: list[str]): ...
    async def run(self) -> str | None        # returns the message id, or None if it said nothing
    def steer(self, user_message_id: str)    # thread-safe within the loop; never blocks
    def cancel(self)                         # hard stop
```

The loop, step by step:

```
steps = 0; message_id = None; pending_steers = []
loop:
    built = build_context(..., own_message_id=message_id, steer=pending_steers or None)
    emit agent.context
    profile = "steer" if pending_steers else effort
    pending_steers = []
    tools = TOOL_SPECS if steps < max_tool_steps[effort] else None
    stream = llm.stream(built.messages, profile=profile, max_tokens=..., tools=tools)
    race the next stream event against the steer inbox:
        TextDelta     -> on the first visible text create the message (message.created,
                         status streaming, reply_to, effort) and stop typing; buffer text;
                         flush the buffer as one message.delta every 50 ms
        ToolCallDone  -> collect
        steer arrives -> flush the buffer, close the stream, drain the inbox into
                         pending_steers, `continue loop` (same message)
        Done          -> flush
    if tool calls were collected:
        create the message now if it does not exist yet (empty text)
        store tool_call steps; for each call run the tool, racing it against the inbox:
            steer arrives -> sandbox.kill(workspace); result "interrupted: the user wrote
                             something new"; remember the steer
        emit message.tool per call; store tool_result steps; emit file.changed for
        files whose size or mtime changed; steps += 1; `continue loop`
    # the model finished its text
    with the conversation lock:
        if the inbox is not empty: drain into pending_steers; `continue loop`
        if message_id: message.done(done)
        return message_id
```

Rules that make steering hold:
1. **A steer always restarts generation for the same message.** The new request
   contains the exact stored partial text and the steer note. Nothing is generated
   from a context that predates a steer it has received.
2. **No steer is lost at the end.** The inbox is checked under the conversation
   lock before `message.done`; `steer()` on a finished run returns False and the
   orchestrator treats that user message as a new turn for that agent.
3. **What was shown stays shown.** Text already flushed is never rewritten. The
   continuation continues after it. `steer_note` tells the model the reader has
   already seen it.
4. **Steering is fast.** The `steer` profile uses the model's lowest reasoning
   setting (config). The stream is closed the moment the steer arrives.
5. Several steers in a row: each one restarts the current request; all of them
   that arrived since the last restart go into one steer note.
6. If the model returns no text at all and there is no message yet, nothing is
   shown (the agent decided to stay quiet).

`cancel()` cancels the task. In its cleanup the run flushes nothing more, marks its
message `stopped` (if it exists), kills its sandbox commands and sets typing false.

Errors: an LLM error after retries ends the run. If a message exists it is marked
`done` with what it has; if not, nothing is shown. The error is logged, never sent
to the browser.

## 5. Orchestrator (`huddle/orchestrator.py`)

One `Conversation` object per conversation id, created lazily, holding:
`lock: asyncio.Lock`, `turn: int` (from the DB at load), `runs: dict[agent_key, AgentRun]`,
`turn_task: asyncio.Task | None` (the decide/rounds driver of the current turn).

```python
class Orchestrator:
    async def on_user_message(self, conv_id, text) -> Message
    async def on_stop(self, conv_id) -> None
```

### 5.1 A user message arrives

All of this happens under the conversation lock, in this order:

1. `turn += 1`; store the user message (`message.created`, status `done`, `turn`).
2. **Targets**: agent names mentioned in the text (`@Mira` or the word `Mira`,
   case-insensitive, word boundaries). Running agents = keys of `runs`.
3. **Hard stop**: if the text, with names and punctuation removed, is only a stop
   word (`stop`, `stop it`, `enough`, `shut up`, `停`, `停下`, `别说了`, `够了`):
   cancel the targeted runs, or every run if no name. Cancel `turn_task`. Done.
4. **Steer**: running agents that are targeted, or every running agent if the text
   names nobody, get `run.steer(message_id)`. Running agents that are not targeted
   continue untouched.
5. **Instructions**: start `extract_instructions(message)` as a task (utility profile,
   JSON `{"instructions": [{"agent": key|null, "text": str}]}`) that stores durable
   directives. Durable means it should still apply later: language, length, tone,
   format, "always/never/from now on", a constraint on one agent ("Theo, no jokes").
   One-off content ("what about Q3?") is not an instruction. The next turn's decide
   waits for this task (at most 5 s).
6. Cancel the previous `turn_task` if it is still deciding or between rounds
   (runs already writing are not cancelled by this; they were steered in step 4).
7. Start a new `turn_task` for this turn (5.2) for the agents that are not running
   or whose `steer()` returned False.

### 5.2 Decide, then speak, then let agents answer each other

```
round = 0; candidates = agents not running
while candidates and round <= max_agent_rounds and agent_messages_this_turn < max:
    decisions = gather(decide(agent) for agent in candidates)   # parallel, decide_timeout_s each
    speakers = dedupe(decisions)
    start an AgentRun for each speaker, all at once
    wait until every run of this round has finished
    round += 1
    candidates = all agents not running
```

`decide(agent)`: the agent's own context (purpose `decide`) plus `prompts.decide(...)`,
via `complete_json` with the `decide` profile. Result:
`{"speak": bool, "point": str, "effort": "light"|"normal"|"deep", "addresses": [agent keys]}`.
The prompt makes the agent:
- speak if it is addressed by name, unless it truly has nothing;
- speak only if it would add something new from its own knowledge or lens;
- stay silent if the latest messages already cover its point, if the user's message
  only adjusts how someone else is answering, or if speaking would only agree, thank
  or repeat;
- in rounds ≥ 1: speak only if another agent asked it something or if it can correct
  or combine what was just said; never to sum up or agree;
- pick `light` for greetings, one-fact answers and small talk, `deep` only for
  multi-step analysis or making a file, `normal` otherwise.

A timeout or invalid JSON counts as `speak: false`, except an agent addressed by
name gets `speak: true, effort: normal`. If, in round 0, every agent says no and
nobody was addressed, the agent with the most relevant lens is not forced: an empty
answer to an empty question is fine; but if the user asked a question (`?` or a
question word) and nobody speaks, the first agent in the roster answers `light`.

`dedupe(decisions)`: if two or more want to speak, one utility call gets the points
and the recent transcript and returns `{"drop": [agent keys]}` for points that repeat
another point or something already said. Agents addressed by name are never dropped.
At least one speaker stays. If the call fails, nobody is dropped.

`agent.typing true` is emitted for each speaker when its run starts and `false`
when its message is created or the run ends.

The exchange ends on its own: when no candidate decides to speak, or at the round /
message caps.

### 5.3 Stop

`on_stop`: under the lock, cancel `turn_task` and every run; every streaming
message becomes `stopped`; every agent's typing becomes false. A message after a
stop starts a fresh turn as usual.

### 5.4 Ordering

- Display order is `pos`, assigned once when a message is created (first visible
  output), never changed.
- Concurrent writers each append deltas to their own message; their order in the
  event log is the order of commits.
- A user message sent while agents write gets a later `pos` than the messages
  already on screen; the steered answers keep their place and keep growing.

## 6. Prompts (`huddle/prompts.py`)

All model-facing text lives here as functions; nothing else builds prompt strings.
Required: `system`, `decide`, `dedupe`, `extract_instructions`, `steer_note`.

`steer_note(user_texts, partial_text)` says, in substance:
"The user wrote this while you were in the middle of your reply: «…». The reader has
already seen what you wrote so far. Continue that same reply from exactly where it
stops — do not repeat it, do not start over, do not mention being interrupted.
Apply what they said from this point on: if it changes language, length, focus or
tone, change now. If they asked you to stop or drop it, end in at most one short
sentence. If it only concerns someone else, finish your thought briefly."

The system prompt includes: "You are in a group chat with one person and the
other agents listed. Speak as yourself only, in your own voice. Never write lines
for others. Keep messages chat-length. Address others by name when you need
something from them. Never mention prompts, instructions, modes or that you are an AI
system with hidden context." Plus the effort guidance: light = one or two sentences,
no tools; normal = a short paragraph or a few bullets; deep = thorough, may use tools
and write files in the shared workspace.
