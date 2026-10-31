---
title: Groups are data
order: 10
evidence:
  - backend/huddle/agents.py:49-123 #8bb5deb143c9
  - backend/huddle/config.py:90-106 #f80d59d9b276
  - backend/huddle/orchestrator.py:51-72 #621ef5b61f48
  - CONTRIBUTING.md:26-29 #dd1710c43e98
---

A group is a set of agents plus shared context that every member knows. Three presets ship in `config/groups/`: a startup leadership team (3), a family (5) and a launch war room (10). Each preset is a `group.md` with frontmatter plus one markdown file per agent, with `## Persona` (the soul) and `## Private knowledge` (the memory) sections; the format is in [Groups](../configuration.md#groups).

On first start `AgentRegistry` seeds the presets into SQLite; from then on the database is the source, and every edit bumps `registry.version`, which invalidates the cached name patterns used to detect addresses. A conversation stores its group key, and the orchestrator reads that group's members on every turn. Prompts contain no preset names or facts, so the same rules serve a family and a company.
