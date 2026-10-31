---
title: Gaps visible in the code
order: 10
evidence:
  - backend/huddle/agent.py:279-299 #63e5fbc3450e
  - backend/huddle/orchestrator.py:218-242 #354f32921143
  - backend/huddle/orchestrator.py:280-299 #8d3e8cd81d91
  - backend/huddle/store.py:103-111 #8d2d58442fcc
  - backend/huddle/context.py:124-132 #40420ee3dc1b
  - backend/huddle/web.py:172-192 #2ef27cd146f2
  - backend/huddle/web.py:288-296 #9b3b54993a4a
---

- **A failed run only apologises.** If a run raises (for example the provider fails mid-stream), `_say_failed` appends a fixed `RUN_FAILED` sentence to whatever was published; the cause stays in the server log.
- **Background calls can drop work.** Instruction extraction has a short timeout and swallows failures, so a standing directive can be lost while the steer itself still applied. A failed decide or dedupe call falls back to simple rules.
- **Utility calls use the default model.** Dedupe and extraction ignore per-agent model choices.
- **One database connection.** `Store` serialises every write behind one lock across all conversations, which is enough for a small instance, not for many users.
- **Long conversations forget.** Context trimming drops the oldest transcript first; a protected continuation may still exceed the limit.
- **DNS rebinding is closed for page reads only**: `fetch_url` connects only to an address it just checked ([[groups-and-tools]]); `web_search` uses a plain client against its fixed search URL.
