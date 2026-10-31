---
title: The run loop
order: 10
evidence:
  - backend/huddle/agent.py:384-424 #b9392e265659
  - backend/huddle/agent.py:435-466 #b620897c1a5b
  - backend/huddle/agent.py:177-180 #32c1bbfd3a9c
  - backend/huddle/agent.py:130-134 #078c41bd0e1f
---

`AgentRun.run` repeats one step. Under the conversation lock it drains the inbox, loads a fresh state and builds the context; if steers are pending, that call uses the `steer` profile and includes them. It then streams one model response. Afterwards:

- steers arrived → loop again at once (a continuation);
- held → wait for release or a steer;
- tool calls → run them, then loop (the tool budget is on [[groups-and-tools]]);
- no tools offered, but the model tried one (`_wanted_tools`) → once, write the same message again at `deep` effort, keeping its steer;
- otherwise → under the lock, drain the inbox once more and only if it is empty mark the message `done`.

`finished` is set before that lock is released, and `steer()` refuses a finished run. A steer is therefore either taken by this run or refused and handled as a new reply; it is never lost between the two. `_text` drops blank lines at the start of a message, so an empty reply leaves no empty message.
