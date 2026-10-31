---
title: Latency metrics
order: 40
evidence:
  - eval/huddle_eval/core.py:164-237 #2172111d1bc6
---

Two numbers per send. First word: time from the send returning to the first text of that turn's agent messages. Steer latency: time from the send's POST to the first word of the continuation in a message that was already streaming, restricted to the agent named in the send when there is one. Text the model wrote before the steer is still flushed just after it, so `continuation_arrival` takes the first delta stored after the steer that comes at least `CONTINUATION_GAP_S` (a model round trip) after both the steer and the previous delta on the server's clock; it is an estimate, since a stream that pauses that long on its own counts as the continuation. Results are written as JSON per run.
