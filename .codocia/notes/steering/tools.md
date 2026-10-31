---
title: Steers during tool work
order: 40
evidence:
  - backend/huddle/agent.py:318-382 #15d3d707a639
  - backend/huddle/agent.py:114-128 #e5883bea390e
---

Between model calls a steer simply lands in the next call. While a command runs, `_tools` races the tool task against the inbox; a steer kills the workspace's commands through `sandbox.kill` and answers the call with an "interrupted" result, as it does for any remaining calls in the batch. A held run waiting for dedupe also listens to its inbox, so a steer is not lost while the run is invisible.
