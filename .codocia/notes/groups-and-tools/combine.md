---
title: The combine round
order: 50
evidence:
  - backend/huddle/orchestrator.py:470-481 #4e340c6c3b05
  - backend/huddle/prompts.py:89-94 #6a65e60d0b72
  - backend/huddle/prompts.py:66 #f0913e30cb3b
---

Questions like "should we take the deal?" need facts that sit in different memories. Two pieces work together. The system prompt tells each agent to state its private facts plainly (the original ask and deadline, not only its preferred compromise). Then, after a first round in which at least two agents spoke and at least one with more than `light` effort, every idle agent gets one combine round: speak only to join a fact someone else just shared with one of its own into a conclusion nobody has stated.
