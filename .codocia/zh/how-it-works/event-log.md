---
title: 唯一的有序事件日志
source: #e65574aa931e
---

每个可见变化都经过 `Store`：`add_message`、`append_text`、`finish_message` 和 `emit`。它们都会获取会话锁，递增 `conversations.last_seq`，在一个 SQLite 事务中写入表的变更和一行 `events`，并且只在提交之后才发布到进程内的 `Bus`。`_transaction` 保护提交不被取消，所以即使任务被取消，已提交的内容仍会发布：数据库里的事件绝不会在实时流中缺失。

消息的 `pos` 就是其 `message.created` 事件的 `seq`，因此它在聊天中的位置在创建时就已固定。对于不再处于 `streaming` 的消息，增量和 `message.tool` 都会被拒绝。
