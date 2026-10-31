# Spec: shared contracts

This file is the contract between the backend, the frontend, the sandbox and
the tests. Every other spec refers to it. If code and this file disagree, the
code is wrong.

## Repository layout

```
config/huddle.yaml          models, active model, preset groups, limits, palette
config/language.yaml        intent word lists (stop, everyone, file request, discussion, hand-off)
config/groups/<key>/        a preset group: group.md + agents/<name>.md
backend/                    Python 3.12, FastAPI, uv project, package `huddle`
  huddle/config.py          load config/huddle.yaml and the preset groups
  huddle/agents.py          AgentRegistry: groups and members in SQLite, editing, remember
  huddle/language.py        intent matching from config/language.yaml
  huddle/store.py           SQLite store, the single writer, event log
  huddle/bus.py             in-memory pub/sub of committed events
  huddle/llm/               provider interface; openai_chat, openai_responses and fake providers
  huddle/tools.py           sandbox client + tool specs for agents
  huddle/web.py             web_search and fetch_url (address-checked)
  huddle/guards.py          invite code, rate limits, caps; metering.py: daily token budget
  huddle/context.py         per-agent context building and token accounting
  huddle/agent.py           AgentRun: one agent writing one message, with steering
  huddle/orchestrator.py    conversation control: decide, rounds, steering, stop
  huddle/prompts.py         every model-facing text
  huddle/api.py             HTTP + SSE routes
  huddle/main.py            app factory, `python -m huddle`
  tests/
frontend/                   Vite + React + TypeScript
sandbox/                    stdlib-only Python HTTP service that runs bash in workspaces
eval/                       evaluation dataset and runner
docker-compose.yml
```

## Configuration (`config/huddle.yaml`)

```yaml
model: ${HUDDLE_MODEL:-deepseek}     # key into `models`; env var interpolation ${VAR:-default}
models:
  minimax:
    provider: openai_chat            # openai_chat | openai_responses | fake
    base_url: https://api.minimax.io/v1
    model: MiniMax-M3.1-Flash
    api_key_env: MINIMAX_API_KEY     # name of the env var holding the key; never the key itself
    include_usage: false             # send stream_options.include_usage
    params:                          # merged into the request body, per profile
      default: {reasoning_effort: minimal}
      deep: {reasoning_effort: low}
  deepseek:
    provider: openai_chat
    base_url: https://api.deepseek.com
    model: deepseek-flash
    api_key_env: DEEPSEEK_API_KEY
    include_usage: true
    params:
      default: {thinking: {type: disabled}}
      deep: {thinking: {type: enabled}}
  fake:
    provider: fake
groups: [startup, family, launch]    # loads config/groups/<key>/ in this order
default_group: startup
limits:                              # every number the runtime uses; see docs/configuration.md
  context_tokens: 24000
  max_tokens: {light: 200, normal: 500, deep: 8000, steer: 500, decide: 500, utility: 400}
  max_tool_steps: {light: 0, normal: 0, deep: 8}
  max_agent_rounds: 2
  max_agent_messages_per_turn: 8
  # ... rounds, discussion, timing and size limits, and `fields` (editable field sizes)
palette: [...]                       # avatar colors for new members
language: ${HUDDLE_LANGUAGE_FILE:-language.yaml}
web: {search_url: ...}
sandbox_url: ${SANDBOX_URL:-http://sandbox:8100}
database: ${HUDDLE_DB:-/data/huddle.db}
```

- Profiles are `decide`, `utility`, `light`, `normal`, `deep`, `steer`. The request
  body params for a profile are `params.default` deep-merged with `params.<profile>`.
- Swapping the model is only `HUDDLE_MODEL=<key>` (or editing `model:`). An agent
  file may also set `model: <key>` to use a different entry for that agent.
- `openai_chat` reads its key from the environment variable `api_key_env`; `openai_responses`
  reads it on every request from `api_key_file`, falling back to `api_key_env` when the file is unset,
  unreadable or empty.
- Env interpolation supports `${VAR}` and `${VAR:-default}` in any string value.

## Preset groups (`config/groups/<key>/`)

`group.md`: frontmatter `name`, `description`, `members` (agent file stems, in order) and `examples`
(prompts shown on an empty conversation); the body is the group's shared context, which every member
gets (no private figures). Members are `agents/<name>.md`:

```markdown
---
name: Mira
role: CFO
color: "#d97706"
lens: money, risk, runway          # one line shown on the People card
model: deepseek                    # optional override
---
## Persona
...how she talks and thinks...

## Private knowledge
...facts only she knows...
```

`config.load_groups()` returns `GroupSpec(key, name, description, common, members, examples)`; each member
is `AgentSpec(key, name, role, color, lens, model, persona, knowledge, common)`. `key` is the file stem
(`mira`), `name` the display name (`Mira`); the sections are split on the `## Persona` and
`## Private knowledge` headings.

Presets only **seed** the `groups` and `agents` tables when a group key is not stored yet (persona →
`soul`, private knowledge → `memory`). After that, groups and members are read from and edited in the
database (`huddle/agents.py`); sizes come from `limits.fields`. Every conversation belongs to one group;
a group's members are shared by all of its conversations. Resetting a group re-seeds it from its preset.

## Database (SQLite, WAL, `foreign_keys=ON`)

```sql
CREATE TABLE conversations(
  id TEXT PRIMARY KEY, title TEXT, created_at TEXT NOT NULL,
  last_seq INTEGER NOT NULL DEFAULT 0,
  group_key TEXT);                   -- the group the conversation talks to
CREATE TABLE events(
  conv_id TEXT NOT NULL REFERENCES conversations(id), seq INTEGER NOT NULL,
  type TEXT NOT NULL, data TEXT NOT NULL, ts TEXT NOT NULL,
  PRIMARY KEY(conv_id, seq));
CREATE TABLE messages(
  id TEXT PRIMARY KEY, conv_id TEXT NOT NULL REFERENCES conversations(id),
  pos INTEGER NOT NULL,              -- seq of its message.created event: the display order
  author TEXT NOT NULL,              -- 'user' or an agent key
  turn INTEGER NOT NULL,             -- user messages start turns 1, 2, 3...; agent messages carry the turn they answer
  reply_to TEXT,                     -- agent keys this message addresses, comma separated, or NULL
  text TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,              -- 'streaming' | 'done' | 'stopped'
  effort TEXT,                       -- light | normal | deep, agent messages only
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX messages_conv_pos ON messages(conv_id, pos);
CREATE TABLE instructions(           -- standing instructions; never shown in the UI
  id TEXT PRIMARY KEY, conv_id TEXT NOT NULL REFERENCES conversations(id),
  agent TEXT,                        -- agent key, or NULL for everyone
  text TEXT NOT NULL, source_message_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE groups(                 -- preset or edited groups
  key TEXT PRIMARY KEY, position INTEGER NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL,
  common TEXT NOT NULL, preset INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE agents(                 -- a group's members, editable at runtime
  group_key TEXT NOT NULL, key TEXT NOT NULL, position INTEGER NOT NULL, name TEXT NOT NULL,
  role TEXT NOT NULL, color TEXT NOT NULL, lens TEXT NOT NULL, soul TEXT NOT NULL, memory TEXT NOT NULL,
  model TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(group_key, key));
CREATE TABLE agent_steps(            -- an agent's private tool history, part of its own context only
  id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id TEXT NOT NULL, agent TEXT NOT NULL,
  message_id TEXT NOT NULL, kind TEXT NOT NULL,   -- 'tool_call' | 'tool_result'
  data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE usage(day TEXT PRIMARY KEY, tokens INTEGER NOT NULL DEFAULT 0);  -- daily token budget
```

IDs are `uuid4().hex`. Times are UTC ISO 8601 with milliseconds and `Z`.

## Events

Every change visible to the browser is an event with a per-conversation `seq`
(1, 2, 3, … with no gaps). The store assigns `seq` and applies the matching
table change **in one transaction, under one per-conversation lock**, then
publishes the event to the bus before releasing the lock. So `seq` order is
commit order is publish order.

| type | data | table effect |
| --- | --- | --- |
| `message.created` | `{"message": Message}` | insert into messages, `pos = seq` |
| `message.delta` | `{"id": str, "text": str}` | `messages.text += text` |
| `message.tool` | `{"id": str, "tool": str, "summary": str}` | none (display only) |
| `message.done` | `{"id": str, "status": "done"\|"stopped"}` | set status |
| `agent.typing` | `{"agent": str, "typing": bool}` | none |
| `agent.context` | `{"agent": str, "used": int, "limit": int}` | none |
| `file.changed` | `{"path": str, "size": int, "agent": str}` | none |

`Message` JSON: `{"id","author","turn","reply_to":[str],"text","status","effort","pos","created_at"}`
(`reply_to` is a list in JSON, `[]` when empty).

Event JSON on the wire: `{"seq": int, "type": str, "data": {...}, "ts": str}`.

Rules:
- `message.delta`, `message.tool` and `message.done` only refer to a message whose
  `message.created` has a smaller `seq`.
- After `message.done` for a message, no other event refers to it.
- The concatenation of a message's deltas (plus its initial `text`) equals its
  stored `text`. User messages are created with full text and `status: done`
  in the same event; no separate `message.done` follows.
- On startup the store marks every `streaming` message `stopped` (a restart ends
  in-flight answers) without emitting events; the snapshot shows the final state.

## HTTP API (prefix `/api`)

| method & path | body | response |
| --- | --- | --- |
| `GET /api/health` | | `{"ok": true, "model", "budget_left"?}` |
| `POST /api/access` | `{"code"}` | `204` and the invite cookie; 401; 429 after repeated wrong codes |
| `GET /api/config` | | `{"default_group", "limits": {field sizes, "message"}, "palette"}` |
| `GET /api/groups` | | `[Group]`, `Group = {"key","name","description","common","preset","examples","agent_count"}` |
| `PUT /api/groups/{group}` | any of `{"name","description","common"}` | `200 Group`; 404; 422 |
| `POST /api/groups/{group}/reset` | | `200 Group` (re-seeded from its preset) |
| `GET /api/groups/{group}/agents` | | `[Agent]`, `Agent = {"key","name","role","color","lens","soul","memory"}` |
| `POST /api/groups/{group}/agents` | `{"name","role"?,"color"?,"lens"?,"soul","memory"?}` | `201 Agent`; 422 `{"detail"}` on invalid fields; 409 `{"detail":"too many agents"}` above `limits.fields.agents_per_group` |
| `PUT /api/groups/{group}/agents/{key}` | any of the Agent fields except `key` | `200 Agent`; 404; 422 |
| `GET /api/conversations` | | `[{"id","title","created_at","group"}]` newest first |
| `POST /api/conversations` | `{"title"?: str, "group"?: str}` | `201 {"id","title","created_at","group"}` (default group when omitted) |
| `GET /api/conversations/{id}` | | snapshot, below |
| `GET /api/conversations/{id}/events?after=N` | | SSE stream, below |
| `POST /api/conversations/{id}/messages` | `{"text": str}` (1–`limits.message_chars` chars after strip) | `202 {"message": Message}`; 429 `{"detail"}` when a limit applies |
| `POST /api/conversations/{id}/stop` | | `202 {}` |
| `GET /api/conversations/{id}/files` | | `[{"path","size","mtime"}]` |
| `GET /api/conversations/{id}/files/{path:path}` | | `{"path","content","binary":bool}` |

Snapshot:
```json
{"conversation": {"id","title","created_at","group"},
 "group": {"key","name"},
 "last_seq": 41,
 "messages": [Message, ...],            // ordered by pos
 "agents": [{"key","name","role","color","lens",
             "typing": bool, "context": {"used": int, "limit": int}}],
 "active": false}
```
`last_seq`, `messages` and `typing` are read in one consistent read (same lock as
writes), so applying events with `seq > last_seq` on top of the snapshot is exact.
`active` is true while the conversation still has work in flight (its turn driver
is running, an agent run is alive, or an instruction extraction is running) and
false otherwise; it is read under the same lock as the rest of the snapshot.

SSE (`text/event-stream`):
- Start point: `after` query parameter, else the `Last-Event-ID` header, else 0.
- Each event: `id: <seq>\nevent: <type>\ndata: <event JSON>\n\n`.
- The server subscribes to the bus first, then replays stored events with
  `seq > after` from the database, then forwards live events skipping any
  `seq` it has already sent. No gaps, no duplicates.
- A comment line `: ping` every `limits.sse_ping_s` seconds. Response headers include
  `Cache-Control: no-cache` and `X-Accel-Buffering: no`.

Errors: unknown conversation or group → 404 `{"detail": ...}`; bad body → 422. With an invite code set,
every route except `/api/health` and `/api/access` needs the cookie (401 otherwise).

## Sandbox service (internal network only)

| method & path | body / query | response |
| --- | --- | --- |
| `POST /exec` | `{"workspace","cmd","timeout_s"}` | `{"exit_code","stdout","stderr","timed_out","truncated"}` |
| `POST /kill` | `{"workspace"}` | `{"killed": int}` |
| `GET /files?workspace=` | | `[{"path","size","mtime"}]` |
| `GET /file?workspace=&path=` | | `{"path","content","binary"}` |
| `PUT /file` | `{"workspace","path","content"}` | `{"path","size"}` |
| `GET /health` | | `{"ok": true}` |

`workspace` is the conversation id. See `docs/specs/sandbox.md`.
