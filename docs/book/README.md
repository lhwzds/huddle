---
title: Huddle
---

# Huddle

<sub>**English** · [中文](zh/README.md)</sub>

This book is for someone evaluating Huddle: what it does, how one message flows through the runtime, how mid-answer steering stays consistent, and where it stops. It builds on the [architecture overview](../architecture.md), [configuration](../configuration.md), [SECURITY.md](../../SECURITY.md) and the [specs](../specs/) instead of repeating them.

## Contents

1. [Overview](01-overview.md) — Running it · Switching models · Provider quirks stay in adapters · Guards for a public instance
2. [How a turn works](02-how-it-works.md) — One ordered event log · Snapshot, replay and gaps · When a message arrives · Deciding who speaks · Parallel start, held output · Follow-up rounds and the end of a turn · What each agent sees
3. [Steering mid-answer](03-steering.md) — The run loop · Racing the stream against the inbox · Continuing the same message · Steers during tool work · Who is steered · Stopping · Standing instructions · One lock per conversation
4. [Groups, memory and tools](04-groups-and-tools.md) — Groups are data · Editing souls and memories · The remember tool · Free discussion · The combine round · Effort decides tool access · Web research · The sandbox
5. [Testing and evaluation](05-testing-and-evaluation.md) — Offline tests · The evaluation dataset · How the runner scores · Latency metrics
6. [Limits and known gaps](06-limits.md) — Gaps visible in the code

## Appendix

- [Code structure](structure.md) — generated from the code: areas, dependencies, pages

<sub>Generated from huddle @ `7a9ef26a4a`</sub>
