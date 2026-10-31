---
title: 
order: 0
evidence:
  - backend/huddle/orchestrator.py:35-37 #ed94e25d42be
  - backend/huddle/main.py:64-66 #71422b9d2db1
  - backend/huddle/store.py:362-364 #efbad6d34353
  - backend/huddle/guards.py:205-210 #ba79822b49f8
  - sandbox/server.py:261-269 #5eb503c8a554
  - backend/huddle/agents.py:69-77 #04d881b8f93b
---

Huddle's scope cuts are deliberate and visible in the code:

- **One process.** Each `Orchestrator` keeps its conversations and their running answers in memory; at startup `stop_streaming_on_startup` marks every message still `streaming` as `stopped`, so a restart ends answers in progress.
- **One user, no accounts.** `has_access` checks a single invite code (or none); whoever passes it sees every conversation.
- **One sandbox container.** Each conversation gets its own workspace directory under one root, but commands run as the same user, so the directory is a separation, not a security boundary.
- **Shared groups.** `AgentRegistry` holds one set of groups per instance; an edit applies to everyone.

[SECURITY.md](../../SECURITY.md) lists the security limits. The notes below add gaps that follow from the code at this commit.
