---
title: Deciding who speaks
order: 40
evidence:
  - backend/huddle/orchestrator.py:249-278 #2d10cfbbe352
  - backend/huddle/orchestrator.py:385-403 #912a84077ceb
  - backend/huddle/orchestrator.py:414-425 #09c87e96e2bb
  - backend/huddle/prompts.py:105-130 #b1f1d93ffac8
  - backend/huddle/llm/base.py:49-98 #47ca1ec1dfd1
---

Each round, every candidate makes a small `decide` call from its *own* context (so private memory shapes the decision) and returns JSON: `speak`, `effort`, `addresses` and a short `point`. The prompt makes silence the default: speak only with a new fact, number, risk or proposal. `complete_json` tolerates prose around the JSON and repairs a cut-off object; a failed or malformed decision falls back to "speak only if addressed".

Code then bounds the model's choices in round 0: if nobody wants to speak, one agent is chosen (the addressed one, one with a point, or whoever spoke last), so every new question gets a reply (a message that only steered writers is the exception, see [[steering]]); a request for a file raises the writer to `deep`; several `light` answers to an unaddressed question collapse to one; and some messages open a free discussion ([[groups-and-tools]]).
