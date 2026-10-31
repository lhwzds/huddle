---
title: Groups, memory and tools
---

# Groups, memory and tools

<sub>**English** · [中文](zh/04-groups-and-tools.md)</sub>

Who the agents are, what they know and what they can do is data, not code. This page covers groups and their members, the two multi-round modes (free discussion and combine), and the tools: the sandbox, web research and `remember`.

<sub>Sources: [huddle.yaml:48-50](../../config/huddle.yaml#L48-L50)</sub>

## Groups are data

A group is a set of agents plus shared context that every member knows. Three presets ship in `config/groups/`: a startup leadership team (3), a family (5) and a launch war room (10). Each preset is a `group.md` with frontmatter plus one markdown file per agent, with `## Persona` (the soul) and `## Private knowledge` (the memory) sections; the format is in [Groups](../configuration.md#groups).

On first start `AgentRegistry` seeds the presets into SQLite; from then on the database is the source, and every edit bumps `registry.version`, which invalidates the cached name patterns used to detect addresses. A conversation stores its group key, and the orchestrator reads that group's members on every turn. Prompts contain no preset names or facts, so the same rules serve a family and a company.

<sub>Sources: [agents.py:49-123](../../backend/huddle/agents.py#L49-L123) · [config.py:90-106](../../backend/huddle/config.py#L90-L106) · [orchestrator.py:51-72](../../backend/huddle/orchestrator.py#L51-L72) · [CONTRIBUTING.md:26-29](../../CONTRIBUTING.md#L26-L29)</sub>

## Editing souls and memories

The app edits members through `/api/groups/{group}/agents` and the group's shared context through `PUT /api/groups/{group}`. `AgentRegistry` validates names (unique in the group, any script), colours and text lengths, caps the group size, and derives a stable key from the name. The limits are `limits.fields` in `config/huddle.yaml`; the web app reads the same numbers through `GET /api/config`, so browser and server never disagree. Reset re-seeds a preset from its files. Edits are global: every conversation of that group sees them on its next call, because context is rebuilt per call ([How a turn works](02-how-it-works.md)).

<sub>Sources: [agents.py:126-193](../../backend/huddle/agents.py#L126-L193) · [agents.py:239-244](../../backend/huddle/agents.py#L239-L244) · [api.py:92-160](../../backend/huddle/api.py#L92-L160) · [huddle.yaml:82-84](../../config/huddle.yaml#L82-L84)</sub>

## The remember tool

`remember` appends one short note to the agent's own memory under `## Remembered`. Memory is shared by every conversation of the group, so the tool is offered, once per message, only when the person's message (or a steer that reaches the run) mentions remembering: the `remember` patterns in `config/language.yaml` set `RunContext.can_remember`. Execution checks again: a `remember` call without it, or a call for any tool that was not offered, is answered with an error and never run. Notes are capped at `remember_note` characters; a full memory refuses a note instead of truncating older facts.

<sub>Sources: [agents.py:220-237](../../backend/huddle/agents.py#L220-L237) · [agent.py:301-310](../../backend/huddle/agent.py#L301-L310) · [agent.py:422-425](../../backend/huddle/agent.py#L422-L425) · [agent.py:326-329](../../backend/huddle/agent.py#L326-L329) · [orchestrator.py:433-435](../../backend/huddle/orchestrator.py#L433-L435) · +2</sub>

## Free discussion

An explicit "discuss/debate" request (word list in `config/language.yaml`), or a hard question that two or more agents want to take on with `deep` effort, turns the turn into a discussion. Rounds and message caps are raised, every idle agent may react a limited number of times, and the decide prompt switches to "react to what the others just said, or stay silent". Discussion messages are kept chat-length and tool-free unless a file was requested.

When the discussion had at least `conclusion_min_messages` messages from at least two agents, `_conclude` gives the first speaker one more run with a directive to write the group's conclusion: the recommendation, the reasons with key numbers, and who still disagrees.

<sub>Sources: [orchestrator.py:404-413](../../backend/huddle/orchestrator.py#L404-L413) · [orchestrator.py:426-430](../../backend/huddle/orchestrator.py#L426-L430) · [orchestrator.py:478-500](../../backend/huddle/orchestrator.py#L478-L500) · [prompts.py:97-102](../../backend/huddle/prompts.py#L97-L102) · [prompts.py:235-244](../../backend/huddle/prompts.py#L235-L244)</sub>

## The combine round

Questions like "should we take the deal?" need facts that sit in different memories. Two pieces work together. The system prompt tells each agent to state its private facts plainly (the original ask and deadline, not only its preferred compromise). Then, after a first round in which at least two agents spoke and at least one with more than `light` effort, every idle agent gets one combine round: speak only to join a fact someone else just shared with one of its own into a conclusion nobody has stated.

<sub>Sources: [orchestrator.py:470-481](../../backend/huddle/orchestrator.py#L470-L481) · [prompts.py:89-94](../../backend/huddle/prompts.py#L89-L94) · [prompts.py:66](../../backend/huddle/prompts.py#L66)</sub>

## Effort decides tool access

Effort sets a message's length guidance, token cap and tool budget; only `deep` has a sandbox tool budget (`max_tool_steps`). Once a run has used tools it keeps being offered them for `tool_budget_grace_steps` more steps, but those calls return "budget used up": withdrawing tools mid-run made some models write raw tool markup as text. After the grace steps the tools are withdrawn, which ends a model that keeps calling. Web calls have their own cap per message. After each batch the run compares the workspace listing before and after and emits `file.changed`, which refreshes the Files panel.

<sub>Sources: [huddle.yaml:56-59](../../config/huddle.yaml#L56-L59) · [agent.py:416-421](../../backend/huddle/agent.py#L416-L421) · [agent.py:371-376](../../backend/huddle/agent.py#L371-L376) · [prompts.py:6-10](../../backend/huddle/prompts.py#L6-L10)</sub>

## Web research

Because the sandbox has no network, `web_search` and `fetch_url` run in the backend. Only http and https are allowed; redirects are followed by hand, and every hop's host is refused if any address is private, loopback, link-local, multicast or a cloud metadata name. `page_client` then connects only to an address that passed that check (`_PublicOnlyBackend` resolves the name once, at connection time), so DNS rebinding cannot reach a private address. Size, time and text length are capped, and errors reach the model as plain sentences. Each result is written into a research folder in the workspace with its source link, so the person can check what an agent read.

<sub>Sources: [web.py:1-9](../../backend/huddle/web.py#L1-L9) · [web.py:140-207](../../backend/huddle/web.py#L140-L207) · [web.py:456-507](../../backend/huddle/web.py#L456-L507) · [web.py:533-562](../../backend/huddle/web.py#L533-L562) · [tools.py:116-129](../../backend/huddle/tools.py#L116-L129)</sub>

## The sandbox

`bash`, `read_file` and `write_file` go over HTTP to a standard-library service in its own container: non-root, read-only root, no capabilities, CPU, memory and process limits, and no route to the internet. Each conversation has a workspace directory. A command runs with `bash -c` in a new session with a minimal environment, a clamped timeout, bounded output and at most four concurrent commands per workspace. Kills hit the whole process group until it is gone, so fork bombs are recoverable; `sweep_detached` (`HUDDLE_SANDBOX_SWEEP=1`, set in `sandbox/Dockerfile`) also kills any process that left its session with `setsid()`. Idle workspaces are deleted after a TTL. The disk budget counts hidden files and is checked about once a second while a command runs (best-effort). [SECURITY.md](../../SECURITY.md) states what this does not defend.

<sub>Sources: [server.py:451-475](../../sandbox/server.py#L451-L475) · [server.py:649-674](../../sandbox/server.py#L649-L674) · [server.py:312-342](../../sandbox/server.py#L312-L342) · [server.py:345-355](../../sandbox/server.py#L345-L355) · [server.py:729-741](../../sandbox/server.py#L729-L741) · +4</sub>
