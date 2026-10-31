---
title: When a message arrives
order: 30
evidence:
  - backend/huddle/orchestrator.py:168-216 #306d24fd00d1
  - backend/huddle/orchestrator.py:457-461 #3d2d158c0268
  - config/language.yaml:5-22 #882507c7976a
---

`on_user_message` does all of its bookkeeping under the conversation lock: increment the turn, store the message, and work out its `targets` (agents named in it, or everyone for a whole-group address; the word lists live in `config/language.yaml`). Then one of two things:

- A pure stop phrase cancels the targeted runs (see [[steering]]).
- Otherwise, running agents that are targeted (or all of them, when nobody is named) get the message in their inbox. Instruction extraction starts in the background, the previous turn driver is cancelled, and a new `_turn` task starts with the idle agents as candidates.

Cancelling the old driver never cancels writers: runs are awaited through `asyncio.shield`, so a new message changes who speaks next, not what is already being written.
