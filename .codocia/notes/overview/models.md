---
title: Switching models
order: 20
evidence:
  - config/huddle.yaml:1-46 #29c9f8a0ad09
  - backend/huddle/config.py:57-66 #293803eb7c40
  - backend/huddle/llm/__init__.py #6f4f3487ec38
  - backend/huddle/orchestrator.py:132-136 #1d0d6706b3a7
---

The model is chosen by configuration: `HUDDLE_MODEL` selects an entry under `models:` in `config/huddle.yaml` (`deepseek` by default), and `make_llm` builds its provider (`openai_chat` for any Chat Completions API, `openai_responses`, or `fake`). No code names a model.

Every call passes a *profile*: `decide`, `steer`, `utility`, or the effort levels `light`, `normal`, `deep`. `params_for` deep-merges the entry's `default` params with that profile's, which is how one setting gives steers the lowest reasoning level and deep work more. An agent may name its own model; `Orchestrator._llm` caches one client per model key. The fields are documented under [Models](../configuration.md#models).
