---
title: remember 工具
source: #0666dee12e82
---

`remember` 往智能体自己的记忆里追加一条简短笔记，放在 `## Remembered` 下。记忆由群组的所有会话共享，所以只有当对方的消息（或送达该运行的引导消息）提到"记住"时，才提供这个工具，每条消息一次：`config/language.yaml` 里的 `remember` 模式会设置 `RunContext.can_remember`。执行时还会再检查：没有它的 `remember` 调用，或者调用了未提供的工具，都会以错误作答，绝不执行。笔记长度上限为 `remember_note` 个字符；记忆已满时会拒绝笔记，而不是截掉旧事实。
