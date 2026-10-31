---
title: Overview
---

# Overview

<sub>**English** · [中文](zh/01-overview.md)</sub>

Huddle is a web group chat between one person and a group of AI agents. Each agent has a soul (who it is) and a private memory; several stream answers at once, talk to each other, stay quiet when they would only repeat someone, and can be redirected while they write, in the same message.

Two objects carry the runtime: the per-conversation `Orchestrator` decides who speaks ([How a turn works](02-how-it-works.md)), and one `AgentRun` per visible message writes it and absorbs steers ([Steering mid-answer](03-steering.md)). The [architecture overview](../architecture.md) has the diagrams and design decisions, [configuration](../configuration.md) every setting and [SECURITY.md](../../SECURITY.md) what the sandbox defends; [Code structure](structure.md) shows the code layout. Huddle is Apache-2.0 licensed; [CONTRIBUTING.md](../../CONTRIBUTING.md) says where changes go.

<sub>Sources: [orchestrator.py:30-44](../../backend/huddle/orchestrator.py#L30-L44) · [agent.py:65-97](../../backend/huddle/agent.py#L65-L97) · [LICENSE:1-3](../../LICENSE#L1-L3)</sub>

## Running it

`docker compose up --build` starts three services: the static `frontend` on port 8080 (bound to `${HUDDLE_BIND:-127.0.0.1}`, so only this machine by default), the FastAPI `backend` with its SQLite file on a volume, and the `sandbox`. The sandbox sits only on an `internal` network: the backend can reach it, the internet cannot be reached from it. Setting `HUDDLE_MODEL=fake` runs a scripted model without any key.

`create_app` wires the store, the registry, the orchestrator and the sandbox client; at startup it refuses to run when the active model, or a model named by a stored member, has no key, marks messages left `streaming` by a previous process as stopped, and loads the groups.

To serve it to others, put any HTTPS reverse proxy in front of the frontend; `HUDDLE_BIND`, base path, unbuffered SSE and forwarded client addresses are covered in [Running behind a reverse proxy](../configuration.md#running-behind-a-reverse-proxy).

<sub>Sources: [docker-compose.yml](../../docker-compose.yml) · [main.py:28-87](../../backend/huddle/main.py#L28-L87) · [configuration.md:93-104](../../docs/configuration.md#L93-L104)</sub>

## Switching models

The model is chosen by configuration: `HUDDLE_MODEL` selects an entry under `models:` in `config/huddle.yaml` (`deepseek` by default), and `make_llm` builds its provider (`openai_chat` for any Chat Completions API, `openai_responses`, or `fake`). No code names a model.

Every call passes a *profile*: `decide`, `steer`, `utility`, or the effort levels `light`, `normal`, `deep`. `params_for` deep-merges the entry's `default` params with that profile's, which is how one setting gives steers the lowest reasoning level and deep work more. An agent may name its own model; `Orchestrator._llm` caches one client per model key. The fields are documented under [Models](../configuration.md#models).

<sub>Sources: [huddle.yaml:1-46](../../config/huddle.yaml#L1-L46) · [config.py:57-66](../../backend/huddle/config.py#L57-L66) · [__init__.py](../../backend/huddle/llm/__init__.py) · [orchestrator.py:132-136](../../backend/huddle/orchestrator.py#L132-L136)</sub>

## Provider quirks stay in adapters

The runtime sees one stream of `TextDelta`, `ToolCallDone` and `Done` events. `OpenAIChat` hides the differences: `_ThinkFilter` drops `<think>` blocks, `_MarkupFilter` cuts native tool-call markup some models write as text (and reports the cut as `wanted_tools` on `Done`, see [Steering mid-answer](03-steering.md)), and with `reasoning_passback` (set for `deepseek`) every assistant message carries its `reasoning_content`, which thinking models require once tools are involved. Transport errors, 429 and 5xx are retried only before the first event is yielded, so a retry can never duplicate visible text.

<sub>Sources: [openai_chat.py:15-41](../../backend/huddle/llm/openai_chat.py#L15-L41) · [openai_chat.py:61-88](../../backend/huddle/llm/openai_chat.py#L61-L88) · [openai_chat.py:101-186](../../backend/huddle/llm/openai_chat.py#L101-L186) · [huddle.yaml:14-23](../../config/huddle.yaml#L14-L23)</sub>

## Guards for a public instance

`guards.py` protects an instance that others can reach: an invite code exchanged for an access cookie that `AccessMiddleware` checks before any route runs, per-client rate limits, caps on messages per conversation and on busy conversations, and a daily token budget. Each guard is off unless its environment variable is set, so local runs and tests need none of them. Every model call goes through `MeteredLLM`, which records provider usage or an estimate, so the budget counts decide, dedupe and extraction calls too. Per-client limits key on `client_ip`, which counts `X-Forwarded-For` from the right: the frontend's nginx appends the address it saw and each of the `HUDDLE_TRUST_PROXY` proxies in front appends one more (`true`/`yes`/`on` means one), so entries a client wrote itself are never believed; `is_https` trusts `X-Forwarded-Proto` only behind trusted proxies. The variables are listed in [configuration](../configuration.md); what the sandbox does and does not defend is in [SECURITY.md](../../SECURITY.md).

<sub>Sources: [guards.py:1-5](../../backend/huddle/guards.py#L1-L5) · [guards.py:56-131](../../backend/huddle/guards.py#L56-L131) · [nginx.conf:13](../../frontend/nginx.conf#L13) · [guards.py:195-280](../../backend/huddle/guards.py#L195-L280) · [api.py:191-211](../../backend/huddle/api.py#L191-L211) · +1</sub>
