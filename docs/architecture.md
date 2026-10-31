# Huddle architecture

Huddle is a group chat between one person and a group of AI agents. A **group** is a set of
agents plus shared context; each agent has a soul (voice, temperament) and a memory (private
facts nobody else in the group sees). Agents answer at the same time, talk to each other, stay
quiet when they would only repeat someone, and can be interrupted mid-sentence: the answer they
are writing changes right there. Three groups ship as presets (3, 5 and 10 agents); any group can
be edited or extended in the app.

## Running it

```mermaid
flowchart LR
  B[Browser] -- "HTTPS (any reverse proxy,<br/>optionally under a base path)" --> F
  subgraph compose["docker compose"]
    F[frontend<br/>nginx, static build]
    A[backend<br/>FastAPI + SQLite]
    S[sandbox<br/>no network]
  end
  F -- "/api/*, SSE unbuffered" --> A
  A -- "internal network only" --> S
  A -- "HTTPS" --> M[(Model API<br/>any configured provider)]
  A -- "HTTPS, SSRF-guarded" --> W[(Web pages<br/>for research)]
```

`docker compose up` runs everything. In production put any HTTPS reverse proxy in front; the app supports a
base path (`HUDDLE_BASE_PATH`), trusted forwarding headers (`HUDDLE_TRUST_PROXY`), an invite code, rate limits
and a daily token budget — see [configuration](configuration.md).

## Components

```mermaid
flowchart TB
  subgraph api["HTTP + SSE (api.py, guards.py)"]
    R[routes: groups, agents,<br/>conversations, messages, stop, files]
    G[invite cookie · rate limits ·<br/>daily token budget]
  end
  subgraph runtime["Runtime"]
    O[Orchestrator<br/>per conversation:<br/>targets, steer, stop, turn driver]
    D[decide · dedupe ·<br/>effort routing · rounds]
    RUN[AgentRun ×N<br/>stream ⟷ inbox race]
    CTX[Context builder<br/>per agent]
    T[Tools: bash, files,<br/>web_search, fetch_url, remember]
  end
  REG[AgentRegistry<br/>groups + agents]
  ST[(Store · SQLite<br/>events · messages · instructions ·<br/>agent_steps · groups · agents)]
  BUS[Event bus]
  LLM[LLM providers<br/>openai_chat · openai_responses · fake]
  SBX[Sandbox service]

  R --> O
  O --> D --> RUN
  RUN --> CTX
  RUN --> LLM
  RUN --> T
  T --> SBX
  O --> REG
  CTX --> REG
  RUN -- "created · delta · done" --> ST
  REG --> ST
  ST -- "publish in seq order" --> BUS --> R
```

## One user message, end to end

```mermaid
flowchart TB
  U([user message]) --> W[store it · turn += 1]
  W --> STOP{stop words?}
  STOP -- yes --> X[cancel targeted runs]
  STOP -- no --> STEER{agents writing?}
  STEER -- "yes: named, or all" --> IN[steer their inbox:<br/>same message continues]
  STEER --> EX[extract standing instructions<br/>in the background]
  STEER --> R0[round 0: every idle member decides<br/>speak? · point · effort]
  R0 --> RULES[rules in code:<br/>at least one reply · file request → deep ·<br/>light question → one answer ·<br/>2+ speakers with deep or 'discuss' → discussion]
  RULES --> HOLD[several speakers: start held,<br/>dedupe drops repeats, release the rest]
  HOLD --> SPEAK[stream in parallel]
  SPEAK --> NEXT{next round}
  NEXT -- "discussion" --> DISC[anyone may react · 2 each ·<br/>up to 4 rounds] --> CONC[first speaker writes the conclusion]
  NEXT -- "2+ shared facts" --> COMB[one combine round:<br/>join a fact of theirs with one of yours]
  NEXT -- "otherwise" --> ADDR[only agents addressed or handed off to]
  COMB --> ADDR
  ADDR --> END([turn ends when nobody has anything new])
```

## Steering inside an answer

```mermaid
sequenceDiagram
  participant U as Person
  participant O as Orchestrator
  participant T as Theo (AgentRun)
  participant M as Model API
  participant S as Store
  T->>M: stream (profile normal)
  M-->>T: deltas
  T->>S: message.delta …
  U->>O: "Theo, one sentence max."
  O->>T: inbox ← message id
  T->>T: flush buffered text, close the HTTP stream
  T->>M: same message: shown text as the assistant turn,<br/>then a steer note (profile steer, lowest reasoning)
  M-->>T: continuation
  T->>S: "… " seam + new deltas, same message id
  T->>S: final inbox check under the lock → message.done
```

## Design decisions

**One event log is the truth.** Every visible change is an event with a per-conversation `seq`,
written with its table change in one transaction under one lock and published in that order. The
browser loads a snapshot consistent with a `seq` and applies later events, detecting gaps. A
reload replays the same log, so order is defended, not hoped for. A message's position is fixed
when its first word appears.

**Steering is our own loop.** Each agent message is an `AgentRun` racing the model stream against
an inbox. A steer closes the HTTP stream, keeps exactly the text the reader saw, and asks the model
to continue that same message with the steer applied, at the provider's lowest reasoning setting
(about a second to the first redirected word). Between tool calls a steer lands at the next model
call and a running command is killed. A final inbox check under the lock before `message.done`
means a late steer is never ignored.

**Groups make the runtime scenario-free.** The orchestrator reads the members of the
conversation's group from the registry on every turn; prompts contain no preset names or facts.
Scenario content lives only in `config/groups/` (seeds) and in the database (edits).

**Instructions persist; identity is protected.** Every user message also goes through an extractor
that stores durable directives; they sit at the end of every later system prompt (just before the identity
rule) and are repeated at
the end of each call. A directive can change language, length or format, never who an agent is.

**Who speaks.** Silence is the default. Each agent first decides from its own context whether it
has something new and how much effort the question deserves; rules in code bound the outcome
(a reply for every message except a stop or one that only redirects agents already writing, one answer for light questions, deep effort for file requests, a cap
per turn that grows with big groups). Agents state their own facts as they are; after several have
shared facts, one combine round lets anyone join a fact of theirs with one of their own.

**Private context per agent.** Soul, memory, the group's shared context, the shared transcript and
only its own tool history. An agent can append to its memory with `remember`.

**Tools.** `bash` and files run in a separate container with no network, a non-root user, a
read-only root, and CPU, memory, process and time limits (fork bombs are cleaned up). Web research
runs in the backend with address checks on every hop, and every source is saved as a note in the
workspace.

**Any model by configuration.** `config/huddle.yaml` lists providers with per-profile parameters
(`decide`, `steer`, `light`, `normal`, `deep`, `utility`). Provider quirks stay in `huddle/llm/`
(e.g. DeepSeek's reasoning passback and its native tool markup).

## Specs

- `docs/specs/contracts.md`: config, database, events, HTTP API, sandbox API.
- `docs/specs/runtime.md`: LLM interface, context, agent run, steering, orchestration, prompts.
- `docs/specs/sandbox.md`, `docs/specs/frontend.md`, `docs/specs/testing.md`, `docs/specs/eval.md`.
