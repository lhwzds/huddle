---
title: 
order: 0
evidence:
  - backend/huddle/orchestrator.py:355-385 #6c9c0a8e56ad
---

The [architecture overview](../architecture.md) draws one user message end to end. This page follows the code: how events are ordered, what happens when a message arrives, how agents decide to speak, and why a turn ends by itself. Steering inside a message is on [[steering]]; groups, discussion and tools are on [[groups-and-tools]].
