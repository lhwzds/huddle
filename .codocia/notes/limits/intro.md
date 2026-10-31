---
title: 
order: 0
evidence:
  - backend/huddle/orchestrator.py:35-36 #99cb03962630
  - backend/huddle/main.py:64-66 #71422b9d2db1
---

Huddle's scope cuts are deliberate: one process and one user, no accounts, running answers kept in memory (a restart marks them stopped), one shared sandbox container isolated by workspace directory, and groups shared by everyone who uses an instance. The [README](../../README.md#scope-cuts-and-known-gaps) lists the known gaps it measured and [SECURITY.md](../../SECURITY.md) the security limits. The notes below add gaps that follow from the code at this commit.
