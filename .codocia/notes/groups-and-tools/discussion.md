---
title: Free discussion
order: 40
evidence:
  - backend/huddle/orchestrator.py:404-413 #331a0d1147a2
  - backend/huddle/orchestrator.py:426-430 #93ba9b842002
  - backend/huddle/orchestrator.py:478-500 #c60514433ca1
  - backend/huddle/prompts.py:97-102 #cc1c7d8c9cb0
  - backend/huddle/prompts.py:235-244 #054bd5ecee1c
---

An explicit "discuss/debate" request (word list in `config/language.yaml`), or a hard question that two or more agents want to take on with `deep` effort, turns the turn into a discussion. Rounds and message caps are raised, every idle agent may react a limited number of times, and the decide prompt switches to "react to what the others just said, or stay silent". Discussion messages are kept chat-length and tool-free unless a file was requested.

When the discussion had at least `conclusion_min_messages` messages from at least two agents, `_conclude` gives the first speaker one more run with a directive to write the group's conclusion: the recommendation, the reasons with key numbers, and who still disagrees.
