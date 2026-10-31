---
title: Continuing the same message
order: 30
evidence:
  - backend/huddle/context.py:97-106 #3be2ac36e64f
  - backend/huddle/prompts.py:22-35 #711677ac24e2
  - backend/huddle/prompts.py:164-182 #f466283e9229
  - backend/huddle/agent.py:181-189 #f9af9d24c727
---

For a continuation, `build_context` moves the agent's own partial message to the end as an `assistant` entry and adds a `steer_note`: what the user wrote, the last characters already shown, and the rule to carry on from there without repeating, restarting or acknowledging the interruption. The `steer` profile asks for the provider's lowest reasoning setting, which is what keeps the first redirected word fast.

New text joins the old with `seam`: "… " when the cut fell mid-word, a space after punctuation. A leading ellipsis from the model is stripped so the seam never doubles. Everything goes to the same message id, so the chat shows one message that changes direction.
