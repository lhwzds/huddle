---
title: 
order: 0
evidence:
  - backend/huddle/orchestrator.py:30-44 #82079e6fc079
  - backend/huddle/agent.py:65-97 #9d8bbb702f65
  - LICENSE:1-3 #6694879b7d86
---

Huddle is a web group chat between one person and a group of AI agents. Each agent has a soul (who it is) and a private memory; several stream answers at once, talk to each other, stay quiet when they would only repeat someone, and can be redirected while they write, in the same message.

Two objects carry the runtime: the per-conversation `Orchestrator` decides who speaks ([[how-it-works]]), and one `AgentRun` per visible message writes it and absorbs steers ([[steering]]). The [architecture overview](../architecture.md) has the diagrams and design decisions, [configuration](../configuration.md) every setting and [SECURITY.md](../../SECURITY.md) what the sandbox defends; [[structure]] shows the code layout. Huddle is Apache-2.0 licensed; [CONTRIBUTING.md](../../CONTRIBUTING.md) says where changes go.
