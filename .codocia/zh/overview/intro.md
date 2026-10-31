---
title: 
source: #54624e266399
---

Huddle 是一个网页群聊：一个人和一组 AI 智能体。每个智能体都有灵魂（它是谁）和私有记忆；几个智能体同时流式作答，彼此对话，只会重复别人时保持沉默，还能在写作途中被改变方向，而且仍是同一条消息。

运行时由两个对象承担：每个会话的 `Orchestrator` 决定谁发言（[[how-it-works]]），每条可见消息对应一个 `AgentRun`，负责写出它并吸收引导（[[steering]]）。[架构概览](../../architecture.md) 有图示和设计决策，[配置](../../configuration.md) 说明每一项设置，[SECURITY.md](../../../SECURITY.md) 说明沙箱防护什么；代码布局见 [[structure]]。Huddle 采用 Apache-2.0 许可；[CONTRIBUTING.md](../../../CONTRIBUTING.md) 说明各类改动应放在哪里。
