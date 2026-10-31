---
title: Limits and known gaps
---

# Limits and known gaps

<sub>**English** · [中文](zh/06-limits.md)</sub>

Huddle's scope cuts are deliberate: one process and one user, no accounts, running answers kept in memory (a restart marks them stopped), one shared sandbox container isolated by workspace directory, and groups shared by everyone who uses an instance. The [README](../../README.md#scope-cuts-and-known-gaps) lists the known gaps it measured and [SECURITY.md](../../SECURITY.md) the security limits. The notes below add gaps that follow from the code at this commit.

<sub>Sources: [orchestrator.py:35-36](../../backend/huddle/orchestrator.py#L35-L36) · [main.py:64-66](../../backend/huddle/main.py#L64-L66)</sub>

## Gaps visible in the code

- **A failed run only apologises.** If a run raises (for example the provider fails mid-stream), `_say_failed` appends a fixed `RUN_FAILED` sentence to whatever was published; the cause stays in the server log.
- **Background calls can drop work.** Instruction extraction has a short timeout and swallows failures, so a standing directive can be lost while the steer itself still applied. A failed decide or dedupe call falls back to simple rules.
- **Utility calls use the default model.** Dedupe and extraction ignore per-agent model choices.
- **One database connection.** `Store` serialises every write behind one lock across all conversations, which is enough for a small instance, not for many users.
- **Long conversations forget.** Context trimming drops the oldest transcript first; a protected continuation may still exceed the limit.
- **DNS rebinding is closed for page reads only**: `fetch_url` connects only to an address it just checked ([Groups, memory and tools](04-groups-and-tools.md)); `web_search` uses a plain client against its fixed search URL.

<sub>Sources: [agent.py:273-293](../../backend/huddle/agent.py#L273-L293) · [orchestrator.py:218-242](../../backend/huddle/orchestrator.py#L218-L242) · [orchestrator.py:280-299](../../backend/huddle/orchestrator.py#L280-L299) · [store.py:103-111](../../backend/huddle/store.py#L103-L111) · [context.py:124-132](../../backend/huddle/context.py#L124-L132) · +2</sub>
