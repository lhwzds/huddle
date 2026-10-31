---
title: One ordered event log
order: 10
evidence:
  - backend/huddle/store.py:180-295 #3ba9de416f2c
  - backend/huddle/bus.py #ff727110cb6c
---

Every visible change goes through `Store`: `add_message`, `append_text`, `finish_message` and `emit`. Each takes the conversation's lock, bumps `conversations.last_seq`, writes the table change and an `events` row in one SQLite transaction, and publishes to the in-process `Bus` only after commit. `_transaction` shields the commit, so a cancelled task still publishes what was committed: an event in the database is never missing from the live stream.

A message's `pos` is the `seq` of its `message.created` event, so its place in the chat is fixed when it is created. Deltas and `message.tool` are refused for a message that is no longer `streaming`.
