---
title: Stopping
order: 60
evidence:
  - backend/huddle/orchestrator.py:502-550 #7715479dea4b
  - backend/huddle/orchestrator.py:330-340 #8ed8b5e819e2
  - backend/huddle/agent.py:447-451 #6ff7453f756c
  - config/language.yaml:5-6 #f5acc464ffef
---

A message that is only a stop phrase (optionally with names; the phrases are in `config/language.yaml`), or the Stop button, ends work instead of steering. Under the lock `_stop_locked` cancels the targeted runs and every instruction extraction, marks their streaming messages `stopped` and clears typing; the cancelled tasks are awaited only after the lock is released, so a newer turn's output is never swept up. A cancelled run discards its unflushed buffer: a stop adds no text. An unnamed follow-up right after a stop ("ok, the short version") is addressed to the stopped agent.
