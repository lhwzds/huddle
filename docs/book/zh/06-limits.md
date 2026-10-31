---
title: 限制与已知缺口
---

# 限制与已知缺口

<sub>[English](../06-limits.md) · **中文**</sub>

Huddle 的范围取舍是有意的，在代码中可以看到：

- **单进程。** 每个 `Orchestrator` 把它的会话及正在进行的回答保存在内存中；启动时 `stop_streaming_on_startup` 把仍处于 `streaming` 的消息全部标记为 `stopped`，所以重启会结束正在进行的回答。
- **单用户，无账户。** `has_access` 只核对一个邀请码（或者不设）；通过的人能看到所有会话。
- **一个沙箱容器。** 每个会话在同一根目录下有自己的工作区目录，但命令以同一个用户运行，所以目录只是分隔，不是安全边界。
- **共享群组。** `AgentRegistry` 为每个实例只保存一套群组；编辑对所有人生效。

[SECURITY.md](../../../SECURITY.md) 列出了安全方面的限制。下面补充从此提交的代码中可以看出的缺口。

<sub>来源: [orchestrator.py:35-37](../../../backend/huddle/orchestrator.py#L35-L37) · [main.py:64-66](../../../backend/huddle/main.py#L64-L66) · [store.py:362-364](../../../backend/huddle/store.py#L362-L364) · [guards.py:205-210](../../../backend/huddle/guards.py#L205-L210) · [server.py:261-269](../../../sandbox/server.py#L261-L269) · +1</sub>

## 代码中可见的缺口

- **运行失败时只会道歉。** 若运行抛出异常（例如提供方在流中途失败），`_say_failed` 会在已发布的文本后追加一句固定的 `RUN_FAILED`；原因只留在服务器日志里。
- **后台调用可能丢失工作。** 指令提取超时很短且吞掉失败，所以常驻指令可能丢失，而引导本身仍已生效。decide 或去重调用失败时会退回到简单规则。
- **工具性调用使用默认模型。** 去重和提取忽略按智能体指定的模型。
- **只有一个数据库连接。** `Store` 用一把锁串行化所有会话的每次写入，对小型实例足够，但不适合大量用户。
- **长对话会遗忘。** 上下文裁剪先丢最旧的对话；受保护的续写内容仍可能超出上限。
- **DNS 重绑定只对页面读取堵住**：`fetch_url` 只连接刚检查过的地址（[群组、记忆与工具](04-groups-and-tools.md)）；`web_search` 用普通客户端访问固定的搜索地址。

<sub>来源: [agent.py:279-299](../../../backend/huddle/agent.py#L279-L299) · [orchestrator.py:218-242](../../../backend/huddle/orchestrator.py#L218-L242) · [orchestrator.py:280-299](../../../backend/huddle/orchestrator.py#L280-L299) · [store.py:103-111](../../../backend/huddle/store.py#L103-L111) · [context.py:124-132](../../../backend/huddle/context.py#L124-L132) · +2</sub>
