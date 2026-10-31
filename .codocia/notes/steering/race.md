---
title: Racing the stream against the inbox
order: 20
evidence:
  - backend/huddle/agent.py:206-270 #99d2a7d5f041
---

`_stream` keeps two tasks alive: the next model event and `inbox.get()`. Text is buffered and flushed as `message.delta` in small time-based batches (`flush_interval_s`). When a steer wins the race, a delta that already arrived is still consumed and flushed, never dropped, and the `finally` block closes the generator, which closes the provider's HTTP stream. The reader keeps exactly what was published, and the next call starts from it.
