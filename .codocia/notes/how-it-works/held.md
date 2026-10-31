---
title: Parallel start, held output
order: 50
evidence:
  - backend/huddle/orchestrator.py:431-455 #a1588a2871c4
  - backend/huddle/orchestrator.py:280-323 #7b72348f702e
  - backend/huddle/agent.py:97-126 #282dda87b2b0
  - backend/huddle/agent.py:158-192 #61cc9bea2ed4
---

When two or more agents want to speak, their runs start immediately but *held*: text accumulates in the run's buffer and no message is created. Meanwhile `_dedupe` asks a utility call which proposed points repeat each other or the recent transcript. Agents the user addressed are never dropped and at least one speaker is kept. `_resolve_held` then releases the kept runs, which create their message and flush the buffer, and cancels the rest, which never become visible.

This overlaps generation with deduplication, so a busy turn pays no extra latency for staying quiet. A message's position is fixed when it is published, not when the agent started thinking.
