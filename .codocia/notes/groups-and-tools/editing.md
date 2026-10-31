---
title: Editing souls and memories
order: 20
evidence:
  - backend/huddle/agents.py:126-193 #728782d17030
  - backend/huddle/agents.py:239-244 #48e5bdabca35
  - backend/huddle/api.py:92-160 #5c70452ed08a
  - config/huddle.yaml:82-84 #81ba9c217dcb
---

The app edits members through `/api/groups/{group}/agents` and the group's shared context through `PUT /api/groups/{group}`. `AgentRegistry` validates names (unique in the group, any script), colours and text lengths, caps the group size, and derives a stable key from the name. The limits are `limits.fields` in `config/huddle.yaml`; the web app reads the same numbers through `GET /api/config`, so browser and server never disagree. Reset re-seeds a preset from its files. Edits are global: every conversation of that group sees them on its next call, because context is rebuilt per call ([[how-it-works]]).
