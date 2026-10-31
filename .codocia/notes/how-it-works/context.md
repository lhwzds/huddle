---
title: What each agent sees
order: 70
evidence:
  - backend/huddle/context.py:73-132 #d6e59273b005
  - backend/huddle/prompts.py:49-86 #c7efb867094a
---

`build_context` builds a fresh prompt for every call. The system message holds the agent's soul, its memory, the group's shared context, the public roster and the standing instructions. The transcript follows: the agent's own messages as `assistant`, the user's and peers' as labelled `user` entries (a peer still writing is marked as such). Tool calls and results appear only for the agent's own recent messages (`own_step_messages`); nobody sees another agent's tool history or memory.

To fit `context_tokens`, whole transcript blocks are dropped oldest first, so a tool result is never separated from its call. The message being continued and a steer note are protected. A directive and the standing-instruction reminder are appended to the last user entry, the last thing the model reads.
