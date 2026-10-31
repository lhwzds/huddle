---
title: Who is steered
order: 50
evidence:
  - backend/huddle/orchestrator.py:189-213 #191df9cce20e
---

A message that names agents steers only those runs; the others keep writing untouched. A message that names nobody steers every running agent. A run that refuses (it just finished) makes that agent a candidate for a new reply instead. When the message only steered writers, the new turn is a *pure steer*: idle agents still decide, but nobody is forced to answer it.
