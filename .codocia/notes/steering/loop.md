---
title: The run loop
order: 10
evidence:
  - backend/huddle/agent.py:378-415 #d4be0cfd5636
  - backend/huddle/agent.py:426-446 #0c428c9d3b5a
  - backend/huddle/agent.py:128-132 #078c41bd0e1f
---

`AgentRun.run` repeats one step. Under the conversation lock it drains the inbox, loads a fresh state and builds the context; if steers are pending, that call uses the `steer` profile and includes them. It then streams one model response. Afterwards:

- steers arrived → loop again at once (a continuation);
- held → wait for release or a steer;
- tool calls → run them, then loop;
- otherwise → under the lock, drain the inbox once more and only if it is empty mark the message `done`.

`finished` is set before that lock is released, and `steer()` refuses a finished run. A steer is therefore either taken by this run or refused and handled as a new reply; it is never lost between the two.
