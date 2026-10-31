---
title: 一轮对话如何运作
---

# 一轮对话如何运作

<sub>[English](../02-how-it-works.md) · **中文**</sub>

[架构概览](../../architecture.md) 画出了一条用户消息的完整流程。本页沿着代码走：事件如何排序、消息到达时发生什么、智能体如何决定发言，以及一轮为何会自行结束。消息内部的引导见 [回答途中的引导](03-steering.md)；群组、讨论和工具见 [群组、记忆与工具](04-groups-and-tools.md)。

<sub>来源: [orchestrator.py:355-385](../../../backend/huddle/orchestrator.py#L355-L385)</sub>

## 唯一的有序事件日志

每个可见变化都经过 `Store`：`add_message`、`append_text`、`finish_message` 和 `emit`。它们都会获取会话锁，递增 `conversations.last_seq`，在一个 SQLite 事务中写入表的变更和一行 `events`，并且只在提交之后才发布到进程内的 `Bus`。`_transaction` 保护提交不被取消，所以即使任务被取消，已提交的内容仍会发布：数据库里的事件绝不会在实时流中缺失。

消息的 `pos` 就是其 `message.created` 事件的 `seq`，因此它在聊天中的位置在创建时就已固定。对于不再处于 `streaming` 的消息，增量和 `message.tool` 都会被拒绝。

<sub>来源: [store.py:180-295](../../../backend/huddle/store.py#L180-L295) · [bus.py](../../../backend/huddle/bus.py)</sub>

## 快照、重放与缺口

浏览器先加载 `GET /api/conversations/{id}`：消息、`last_seq`、智能体状态和一个 `active` 标志，全部在同一把锁下读取，因此描述的是同一时刻。随后用 `?after=last_seq` 打开 SSE 流。服务器先订阅总线，*再*读取日志，重放已存事件后转发实时事件，并跳过任何不大于其游标的 `seq`，所以两者重叠也无妨。

客户端只接受恰好为 `lastSeq + 1` 的事件。出现缺口时，`connect` 关闭流，`App` 重新加载快照；网络出错时则从游标处重连。因此重新加载后看到的顺序总与实时视图一致。

<sub>来源: [store.py:297-313](../../../backend/huddle/store.py#L297-L313) · [api.py:173-189](../../../backend/huddle/api.py#L173-L189) · [api.py:249-280](../../../backend/huddle/api.py#L249-L280) · [state.ts:32-35](../../../frontend/src/state.ts#L32-L35) · [api.ts:123-166](../../../frontend/src/api.ts#L123-L166) · +1</sub>

## 消息到达时

`on_user_message` 在会话锁下完成全部记账：轮次加一，存储消息，并算出它的 `targets`（消息中点名的智能体；若是对全体说话则为所有人；词表在 `config/language.yaml` 中）。接着二选一：

- 纯停止短语会取消目标运行（见 [回答途中的引导](03-steering.md)）。
- 否则，被点名的运行中智能体（无人被点名时则是全部运行中的智能体）会在收件箱里收到这条消息。指令提取在后台启动，上一轮的驱动任务被取消，新的 `_turn` 任务以空闲智能体为候选开始。

取消旧驱动绝不会取消正在写的智能体：运行通过 `asyncio.shield` 等待，所以新消息改变的是接下来谁发言，而不是已经在写的内容。

<sub>来源: [orchestrator.py:168-216](../../../backend/huddle/orchestrator.py#L168-L216) · [orchestrator.py:457-461](../../../backend/huddle/orchestrator.py#L457-L461) · [language.yaml:5-22](../../../config/language.yaml#L5-L22)</sub>

## 决定谁发言

每一回合，每个候选者都从*自己的*上下文发起一次小的 `decide` 调用（因此私有记忆会影响决定），返回 JSON：`speak`、`effort`、`addresses` 和简短的 `point`。提示词把沉默设为默认：只有带来新的事实、数字、风险或提议时才发言。`complete_json` 容忍 JSON 周围的文字，并能修复被截断的对象；失败或格式错误的决定退回到"仅在被点名时发言"。

随后代码在第 0 回合约束模型的选择：若无人想发言，就选出一位（被点名者、有观点者，或上一个发言者），所以每个新问题都有回复（只引导了正在写的智能体的消息是例外，见 [回答途中的引导](03-steering.md)）；请求文件会把写作者提升到 `deep`；对未点名问题的多个 `light` 回答合并为一个；有些消息会开启自由讨论（[群组、记忆与工具](04-groups-and-tools.md)）。

<sub>来源: [orchestrator.py:249-278](../../../backend/huddle/orchestrator.py#L249-L278) · [orchestrator.py:385-403](../../../backend/huddle/orchestrator.py#L385-L403) · [orchestrator.py:414-425](../../../backend/huddle/orchestrator.py#L414-L425) · [prompts.py:105-130](../../../backend/huddle/prompts.py#L105-L130) · [base.py:48-97](../../../backend/huddle/llm/base.py#L48-L97)</sub>

## 并行开始，暂扣输出

当两个或以上智能体想发言时，它们的运行立即开始，但处于*暂扣*状态：文本积累在运行的缓冲区里，不创建消息。与此同时，`_dedupe` 通过一次工具性调用判断哪些提议的观点彼此重复，或与最近的对话重复。用户点名的智能体从不被去掉，并且至少保留一位发言者。然后 `_resolve_held` 放行保留的运行，它们创建消息并刷新缓冲区；其余的被取消，永远不会出现。

这样生成与去重重叠进行，繁忙的一轮不会因为保持安静而多付延迟。消息的位置在发布时固定，而不是在智能体开始思考时。

<sub>来源: [orchestrator.py:431-455](../../../backend/huddle/orchestrator.py#L431-L455) · [orchestrator.py:280-323](../../../backend/huddle/orchestrator.py#L280-L323) · [agent.py:97-126](../../../backend/huddle/agent.py#L97-L126) · [agent.py:158-192](../../../backend/huddle/agent.py#L158-L192)</sub>

## 后续回合与一轮的结束

一回合结束后，下一批候选者取决于实际写出的内容。`_message_targets` 只读取已完成的文本：`@Name`、同一句中名字后跟问号，或移交（"that's Theo's call"）；这些模式在 `config/language.yaml` 中。`decide` 返回的 `addresses` 字段被忽略，因为模型会在其中列出所有人，每条消息都变成了邀请。

在讨论或合并回合之外，只有被点名的空闲智能体才是候选者。当无人发言、达到回合上限或触及每轮消息上限时，循环停止；`_turn_cap` 为大群组放宽上限，让每个人都能发言一次。这就是一轮会自行结束、而不会变成无尽往来的原因。

<sub>来源: [orchestrator.py:325-328](../../../backend/huddle/orchestrator.py#L325-L328) · [orchestrator.py:342-353](../../../backend/huddle/orchestrator.py#L342-L353) · [orchestrator.py:367-380](../../../backend/huddle/orchestrator.py#L367-L380) · [orchestrator.py:462-469](../../../backend/huddle/orchestrator.py#L462-L469) · [language.yaml:48-56](../../../config/language.yaml#L48-L56) · +1</sub>

## 每个智能体看到什么

`build_context` 为每次调用重新构建提示词。系统消息包含智能体的灵魂、记忆、群组共享上下文、公开名单和常驻指令。之后是对话记录：智能体自己的消息作为 `assistant`，用户和同伴的消息作为带标签的 `user` 条目（仍在输入的同伴会被标注）。工具调用和结果只出现在智能体自己最近的消息中（`own_step_messages`）；谁也看不到别的智能体的工具历史或记忆。

为了放进 `context_tokens`，会从最旧开始丢弃整段对话块，因此工具结果永远不会与其调用分离。正在续写的消息和引导说明受保护。附加指令和常驻指令提醒附加在最后一个 user 条目上，是模型最后读到的内容。

<sub>来源: [context.py:73-132](../../../backend/huddle/context.py#L73-L132) · [prompts.py:49-86](../../../backend/huddle/prompts.py#L49-L86)</sub>
