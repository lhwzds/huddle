---
title: 模型流与收件箱赛跑
source: #b210a0b39c5b
---

`_stream` 同时保持两个任务：下一个模型事件和 `inbox.get()`。文本先缓冲，再按时间小批量（`flush_interval_s`）刷新为 `message.delta`。当引导赢得赛跑时，已经到达的增量仍会被消费并刷新，绝不丢弃；`finally` 块关闭生成器，从而关闭提供方的 HTTP 流。读者保留的正是已发布的内容，下一次调用就从这里开始。
