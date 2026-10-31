---
title: Effort decides tool access
order: 60
evidence:
  - config/huddle.yaml:56-59 #cdc2f78cb555
  - backend/huddle/agent.py:416-421 #1db2d44aeef2
  - backend/huddle/agent.py:371-376 #5fb6b41e6b04
  - backend/huddle/prompts.py:6-10 #3c51f6f2f235
---

Effort sets a message's length guidance, token cap and tool budget; only `deep` has a sandbox tool budget (`max_tool_steps`). Once a run has used tools it keeps being offered them for `tool_budget_grace_steps` more steps, but those calls return "budget used up": withdrawing tools mid-run made some models write raw tool markup as text. After the grace steps the tools are withdrawn, which ends a model that keeps calling. Web calls have their own cap per message. After each batch the run compares the workspace listing before and after and emits `file.changed`, which refreshes the Files panel.
