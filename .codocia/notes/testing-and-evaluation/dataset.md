---
title: The evaluation dataset
order: 20
evidence:
  - eval/cases/steer.yaml:1-30 #abe94b4f09c7
  - eval/huddle_eval/core.py:14-20 #190121c038da
  - eval/huddle_eval/core.py:106-136 #f1d43f3b49d1
---

`eval/cases/*.yaml` holds 36 scripted conversations across all three groups. A case lists steps (`say`, `wait` for an agent to reach some characters or for idle, `stop`, `reload`, `burst`), deterministic `checks`, and plain-language `judge` statements. Categories cover steering, stopping, silence, exchanges between agents, combining knowledge, adversarial input (prompt injection, hostile shell commands) and the larger groups.
