---
title: One lock per conversation
order: 80
evidence:
  - backend/huddle/store.py:55-90 #d85181d64df0
  - backend/huddle/store.py:362-364 #efbad6d34353
---

Every decision that must agree with the log (creating a message, draining an inbox before `done`, a stop, a snapshot) runs under the conversation's `ConversationLock`. It is re-entrant within one task, so a run can hold it while calling `Store` methods that take it again, but never shared with child tasks. If the process dies mid-answer, startup marks every `streaming` message `stopped`; nothing is reordered or rewritten.
