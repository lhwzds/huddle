---
title: The remember tool
order: 30
evidence:
  - backend/huddle/agents.py:220-237 #b044e27a744a
  - backend/huddle/agent.py:301-310 #1e80201194c2
  - backend/huddle/agent.py:422-425 #ee7d84949018
  - backend/huddle/agent.py:326-329 #e6b5a315580b
  - backend/huddle/orchestrator.py:433-435 #4eef0e4b0f9a
  - config/language.yaml:34-45 #dc02d60c7aa8
  - backend/huddle/tools.py:72-73 #9c715a68a278
---

`remember` appends one short note to the agent's own memory under `## Remembered`. Memory is shared by every conversation of the group, so the tool is offered, once per message, only when the person's message (or a steer that reaches the run) mentions remembering: the `remember` patterns in `config/language.yaml` set `RunContext.can_remember`. Execution checks again: a `remember` call without it, or a call for any tool that was not offered, is answered with an error and never run. Notes are capped at `remember_note` characters; a full memory refuses a note instead of truncating older facts.
