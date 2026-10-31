---
title: Standing instructions
order: 70
evidence:
  - backend/huddle/orchestrator.py:201-207 #55bc2c4e2596
  - backend/huddle/orchestrator.py:218-242 #354f32921143
  - backend/huddle/orchestrator.py:361-366 #be60a0ccbc39
  - backend/huddle/prompts.py:78-86 #87b3b67fdfa9
---

"From now on, answer in Chinese" must affect the steered message and every later one. Each user message also goes to a background extractor that stores durable directives, for everyone or one agent. Extractions are chained in arrival order, so a later directive overrides an earlier one even in a burst; the next turn waits up to `extract_timeout_s` for the previous extraction. The steer itself already applies the request to running messages.

Directives sit near the end of every system prompt, just before the identity rule, and return as a reminder at the end of each call. That rule is the one limit: a directive may change language, length, format or focus, never who the agent is.
