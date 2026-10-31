---
title: 
order: 0
evidence:
  - backend/huddle/agent.py:64-93 #b69e09c9a95e
  - AGENTS.md:5-6 #8caffe780bab
  - CONTRIBUTING.md:3-4 #ce4f045981f6
---

A steer is a user message that arrives while an agent is writing. Huddle does not stop the agent and start a new reply: the same message continues from exactly what the reader has seen, with the request applied. The loop is Huddle's own (no agent framework), because it has to race a model stream against an inbox and keep the event log consistent. The [architecture overview](../architecture.md) has the sequence diagram.
