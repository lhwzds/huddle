---
title: Snapshot, replay and gaps
order: 20
evidence:
  - backend/huddle/store.py:297-313 #9e1fde27c4c3
  - backend/huddle/api.py:173-189 #0c019eff1836
  - backend/huddle/api.py:249-280 #adf814acb6b2
  - frontend/src/state.ts:32-35 #b352eed1e058
  - frontend/src/api.ts:123-166 #c12526ebd2e4
  - frontend/src/App.tsx:115-149 #7f74a04c03f0
---

The browser loads `GET /api/conversations/{id}`: messages, `last_seq`, agent state and an `active` flag, all read under the same lock, so they describe one instant. It then opens the SSE stream with `?after=last_seq`. The server subscribes to the bus *before* reading the log, replays stored events, then forwards live ones, skipping any `seq` at or below its cursor, so the overlap is harmless.

The client applies an event only if it is exactly `lastSeq + 1`. On a gap, `connect` closes the stream and `App` reloads the snapshot; on a network error it reconnects from its cursor. A reload therefore always shows the same order as the live view.
