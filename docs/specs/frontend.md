# Spec: the web app (`frontend/`)

A group chat with three colleagues, plus a panel showing the files they make.
It must feel like a chat app. Nothing on screen reveals the machinery: no
"steering", "redirecting", "interrupted", modes, logs, tokens of internal events,
or debug panels. The contract with the backend is `docs/specs/contracts.md`.

## Stack

Vite + React 18 + TypeScript (strict). Runtime dependencies allowed:
`react`, `react-dom`, `react-router-dom`, `marked`, `dompurify`. Dev: `vite`,
`@vitejs/plugin-react`, `typescript`, `vitest`, `@types/*`. Plain CSS (one
`styles.css` with CSS variables), no UI kit. `npm run dev` proxies `/api` to
`http://localhost:8000`.

## Files

```
frontend/
  package.json        scripts: dev, build (tsc -b && vite build), test (vitest run), typecheck
  index.html, vite.config.ts, tsconfig.json
  src/main.tsx, App.tsx
  src/api.ts           typed fetch helpers + connect(convId, afterSeq, onEvent, onGap)
  src/types.ts         Message, Event, Agent, Snapshot — exactly the contract shapes
  src/state.ts         pure reducer: applySnapshot, applyEvent (no React, no fetch)
  src/components/      Chat.tsx, MessageBubble.tsx, Composer.tsx, PeoplePanel.tsx,
                       FilesPanel.tsx, FileViewer.tsx, TurnDivider.tsx
  src/styles.css
  test/state.test.ts   reducer tests
  test/mentions.test.ts
  Dockerfile, nginx.conf
```

## State and transport (`state.ts`, `api.ts`)

- `applySnapshot(snapshot) -> State` and `applyEvent(state, event) -> State`, pure.
- `applyEvent` ignores events with `seq <= state.lastSeq`. If `seq > state.lastSeq + 1`
  it returns the state unchanged with `gap: true`; the caller then reloads the
  snapshot and reconnects. Otherwise it applies the event and sets `lastSeq = seq`.
- Messages are kept sorted by `pos`. `message.created` inserts (ignoring an id that
  already exists), `message.delta` appends, `message.done` sets the status,
  `message.tool` appends `{tool, summary}` to that message's `tools` list (UI only).
- `agent.typing` / `agent.context` update the agent entry. `file.changed` bumps a
  `filesVersion` counter.
- `connect` uses `EventSource('/api/conversations/{id}/events?after=N')`. On error it
  closes, waits 1 s, and reconnects from the current `lastSeq`. Never reconnect from 0
  when a snapshot is loaded.
- Sending a message: `POST .../messages`. Do not add the message optimistically with
  a fake id; it arrives as `message.created` within milliseconds. Keep the composer
  text until the POST succeeds; on failure show a small inline "Couldn't send — retry".

## Layout

- Routes: `/` opens the newest conversation or creates one; `/c/:id` opens a
  conversation. A header with the app name ("Huddle"), a conversation title and a
  "New chat" button. A reload at `/c/:id` shows exactly the same conversation.
- Left (about 62%): the chat. Right: a panel with two tabs, **Files** and **People**.
  Under 900 px wide the panel becomes a drawer toggled from the header.
- **Messages**:
  - User messages are right-aligned bubbles.
  - Agent messages are left-aligned with a colored avatar (initial on the agent's
    `color`), the name and the role in small text, and the text rendered as markdown
    (`marked` + `DOMPurify`).
  - Tool activity (`tools`) shows as small grey monospace lines inside the bubble
    above the text, e.g. "ran `python3 runway.py`", "wrote plan.md". A file name in a
    tool line is a link that opens the file in the Files tab.
  - While a message is `streaming`, a soft blinking caret follows its text.
  - If `reply_to` is set, a small "→ Theo" before the text.
  - Nothing distinguishes a stopped message except that it ended.
- **Turns**: each user message starts a turn. A thin divider with a light label
  (`turn number · HH:MM`) sits above each user message. Agent messages of a turn
  follow it in `pos` order.
- **Typing**: under the last message, "Mira is typing…" / "Mira and Theo are typing…"
  for agents with `typing: true` whose message is not yet visible.
- **Composer** at the bottom, always enabled, including while agents write (this is
  how the user interrupts them). Enter sends, Shift+Enter is a newline. Typing `@`
  opens an autocomplete of agent names (filter by prefix, arrow keys + Enter/Tab to
  insert `@Name `). A **Stop** button (square icon) replaces nothing; it appears next
  to Send while any agent is typing or streaming; Esc does the same. Stop calls
  `POST .../stop`.
- Auto-scroll to the bottom on new content only if the user is already near the
  bottom; otherwise show a small "New messages ↓" pill.
- **People tab**: a card per agent: avatar, name, role, lens, a status dot (green
  "typing" while typing or streaming, grey otherwise) and a context bar
  `used / limit` with a label like "6.1k / 24k context".
- **Files tab**: a list of files (path, size), refreshed whenever `filesVersion`
  changes. Clicking opens `FileViewer`:
  - `.md` rendered;
  - `.csv` as a table (first 200 rows);
  - `.html` in an `<iframe sandbox srcdoc>` with no `allow-scripts`;
  - images (`binary` + png/jpg/svg) as `<img>` from a data URL;
  - everything else as preformatted text.
  A file changed while it is open is refetched.

## Look

Clean and calm: light background, system font stack, 15 px text, generous line
height, rounded bubbles, subtle shadows, the agent colors used only for avatars and
the small name line. Dark mode via `prefers-color-scheme`.

## Tests (`npm test`, vitest)

- reducer: snapshot then events in order; duplicate seq ignored; a gap sets `gap`;
  deltas append to the right message; messages stay sorted by `pos` when created out
  of id order; typing/context updates; `message.tool` lines.
- mentions: `@` autocomplete filtering and insertion (pure helper in `src/mentions.ts`).

Done means `npm ci && npm run typecheck && npm test && npm run build` pass in `frontend/`.

## Docker (`frontend/Dockerfile`, `frontend/nginx.conf`)

Multi-stage: `node:22-alpine` builds `dist/`, `nginx:alpine` serves it on port 80.
`nginx.conf`: SPA fallback to `index.html`; `location /api/` proxies to
`http://backend:8000` with `proxy_http_version 1.1`, `proxy_buffering off`,
`proxy_cache off`, `proxy_read_timeout 1h`, and `Connection ""`.
