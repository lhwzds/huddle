# Configuration

Huddle has no settings in code. Everything tunable lives in four places:

| where | what |
| --- | --- |
| `config/huddle.yaml` | models, limits, palette, language file, web search endpoint |
| `config/groups/<key>/` | preset groups: `group.md` (name, description, members, example prompts, shared context) and `agents/*.md` |
| `config/language.yaml` | intent word lists: stop words, "everyone", file requests, discussion requests, hand-offs |
| environment (`.env`) | secrets, the active model, serving and protection settings — see [`.env.example`](../.env.example) |

`${VAR}` and `${VAR:-default}` work in any string of `huddle.yaml`.

## Models

```yaml
model: ${HUDDLE_MODEL:-deepseek}        # which entry below is active
models:
  deepseek:
    provider: openai_chat               # openai_chat | openai_responses | fake
    base_url: https://api.deepseek.com
    model: deepseek-flash
    api_key_env: DEEPSEEK_API_KEY       # the environment variable that holds the key
    include_usage: true
    reasoning_passback: true            # send reasoning back with tool calls (thinking models that require it)
    params:                             # merged into the request body, per profile
      default: {thinking: {type: disabled}}
      deep: {thinking: {type: enabled}}
```

Profiles: `decide`, `utility`, `light`, `normal`, `deep`, `steer`. A profile's parameters are `params.default`
deep-merged with `params.<profile>`. Give `steer` the provider's lowest reasoning setting: it decides how fast a
redirect shows up. An agent file may set `model: <key>` to use another entry.

`openai_responses` (the `openai-plan` entry) reads its key from `api_key_file` instead, on every request, so a
short-lived token can be replaced while Huddle runs. With Docker the path is inside the backend container; mount
the file in a `docker-compose.override.yml` next to `docker-compose.yml` (picked up automatically). Keep the
token outside the checkout, so it never ends up in Git or in a Docker build context:

```yaml
services:
  backend:
    volumes:
      - ${HOME}/.config/huddle/openai-token:/run/secrets/openai-token:ro   # readable by uid 1000
```

and set `OPENAI_TOKEN_FILE=/run/secrets/openai-token` in `.env`.

## Limits

All numbers the runtime uses are under `limits:` in `huddle.yaml`, grouped as:

- **context and output**: `context_tokens`, `own_step_messages`, `max_tokens.<profile>`
- **tools**: `max_tool_steps.<effort>`, `tool_budget_grace_steps`, `max_web_calls`, `bash_timeout_s`,
  `sandbox_request_margin_s`, `tool_result_chars`, `page_result_chars`, `page_fetch_chars`
- **turns**: `max_agent_rounds`, `max_agent_messages_per_turn`, `big_group_size`, `big_group_extra`,
  `max_discussion_rounds`, `max_discussion_messages`, `max_discussion_messages_per_agent`,
  `conclusion_min_messages`, `dedupe_history_messages`
- **time**: `decide_timeout_s`, `extract_timeout_s`, `flush_interval_s`, `sse_ping_s`
- **sizes**: `message_chars`, and `fields` (members per group, name/role/lens lengths, soul/memory/shared-context
  sizes, remembered note length). The web app reads `fields` and `message_chars` through `GET /api/config`, so
  the browser and the server never disagree.

Protection for a public instance (rate limits, invite code, daily token budget, workspace cleanup) is set through
environment variables and is off unless set.

## Groups

```markdown
---
name: The Rossi family
description: Five family members planning the summer.
members: [elena, marco, rosa, leo, mia]        # files in agents/, in this order
examples:                                       # shown on an empty conversation
  - "Can we do the Italy trip in July?"
---
Shared context: what everyone in the group knows. Private facts go in each member's file.
```

An agent file has frontmatter (`name`, `role`, `color`, `lens`, optional `model`) and two sections, `## Persona`
(the soul) and `## Private knowledge` (the memory). Presets seed the database on first start; after that, groups
and members are edited in the app and stored in SQLite. "Reset this group" restores the preset. Add a preset by
adding a folder and its key to `groups:`.

Names may use any script (José, 小明, Mary Ann). Up to `limits.fields.agents_per_group` members per group.

## Language

`config/language.yaml` holds regular expressions (case-insensitive). `{name}` in `question_to` and `handoff`
stands for a member's name. The shipped file covers English and Chinese; another language is a matter of adding
entries — no code change.

## Running behind a reverse proxy

Put any HTTPS reverse proxy in front of the frontend container. The port listens on `127.0.0.1` by default;
for a proxy on another host set `HUDDLE_BIND` to an address only that proxy can reach. If the proxy serves the app under a path and
strips it, build with `HUDDLE_BASE_PATH=/that/path/` and set `HUDDLE_COOKIE_PATH` to the same value. Server-Sent
Events must not be buffered (turn off response buffering for `/api/` in your proxy). Set
`HUDDLE_TRUST_PROXY` to the number of proxies in front of port 8080 (usually `1`); each must append the
address it saw to `X-Forwarded-For`, and the outermost must set `X-Forwarded-Proto`. Per-client limits then
see the real client, and the invite cookie is marked `Secure`. Entries a client writes itself sit further left
and are ignored, but only if nothing except the proxy can connect to the port: bind it to localhost or a private
address and restrict that address with a firewall or network policy. The backend's own port is never published
(it is reached only through the frontend's nginx, which records the address it was connected from).
