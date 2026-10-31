---
title: 回答途中的引导
---

# 回答途中的引导

<sub>[English](../03-steering.md) · **中文**</sub>

引导是在智能体写作期间到达的用户消息。Huddle 不会停下智能体再开始新回复：同一条消息从读者已看到的位置精确续写，并应用新的要求。这个循环是 Huddle 自己的（不用智能体框架），因为它必须让模型流与收件箱赛跑，并保持事件日志一致。[架构概览](../../architecture.md) 有时序图。

<sub>来源: [agent.py:65-97](../../../backend/huddle/agent.py#L65-L97) · [AGENTS.md:5-6](../../../AGENTS.md#L5-L6) · [CONTRIBUTING.md:3-4](../../../CONTRIBUTING.md#L3-L4)</sub>

## 运行循环

`AgentRun.run` 重复同一个步骤。在会话锁下，它清空收件箱，加载新状态并构建上下文；若有待处理的引导，这次调用使用 `steer` profile 并包含它们。然后流式获取一次模型响应。之后：

- 有引导到达 → 立即再循环（续写）；
- 处于暂扣 → 等待放行或引导；
- 有工具调用 → 执行后再循环（工具预算见 [群组、记忆与工具](04-groups-and-tools.md)）；
- 没有提供工具，但模型试图调用一个（`_wanted_tools`）→ 以 `deep` 努力等级把同一条消息重写一次，保留它所回应的引导；
- 否则 → 在锁下再清空一次收件箱，只有为空时才把消息标记为 `done`。

`finished` 在释放这把锁之前设置，而 `steer()` 会拒绝已完成的运行。因此一个引导要么被这次运行接收，要么被拒绝并作为新回复处理；绝不会在两者之间丢失。`_text` 会去掉消息开头的空行，所以空回复不会留下空消息。

<sub>来源: [agent.py:384-424](../../../backend/huddle/agent.py#L384-L424) · [agent.py:435-466](../../../backend/huddle/agent.py#L435-L466) · [agent.py:177-180](../../../backend/huddle/agent.py#L177-L180) · [agent.py:130-134](../../../backend/huddle/agent.py#L130-L134)</sub>

## 模型流与收件箱赛跑

`_stream` 同时保持两个任务：下一个模型事件和 `inbox.get()`。文本先缓冲，再按时间小批量（`flush_interval_s`）刷新为 `message.delta`。当引导赢得赛跑时，已经到达的增量仍会被消费并刷新，绝不丢弃；`finally` 块关闭生成器，从而关闭提供方的 HTTP 流。读者保留的正是已发布的内容，下一次调用就从这里开始。

<sub>来源: [agent.py:206-270](../../../backend/huddle/agent.py#L206-L270)</sub>

## 续写同一条消息

续写时，`build_context` 把智能体自己的部分消息移到末尾作为 `assistant` 条目，并加上 `steer_note`：用户写了什么、已显示的最后几个字符，以及从那里接着写、不重复、不重来、不提及被打断的规则。`steer` profile 要求提供方的最低推理档，这正是第一个转向后的词出得快的原因。

新文本通过 `seam` 与旧文本衔接：断在词中间时用 "… "，标点之后用一个空格。模型开头自带的省略号会被去掉，接缝不会重复。所有内容都写入同一个消息 id，所以聊天里显示的是一条改变了方向的消息。

<sub>来源: [context.py:97-106](../../../backend/huddle/context.py#L97-L106) · [prompts.py:22-35](../../../backend/huddle/prompts.py#L22-L35) · [prompts.py:164-182](../../../backend/huddle/prompts.py#L164-L182) · [agent.py:181-189](../../../backend/huddle/agent.py#L181-L189)</sub>

## 工具执行期间的引导

在两次模型调用之间，引导直接进入下一次调用。命令运行时，`_tools` 让工具任务与收件箱赛跑；引导会通过 `sandbox.kill` 杀掉该工作区的命令，并以"interrupted"结果答复这次调用，批次中剩余的调用也一样。等待去重的暂扣运行同样监听收件箱，所以运行尚不可见时引导也不会丢失。

<sub>来源: [agent.py:318-382](../../../backend/huddle/agent.py#L318-L382) · [agent.py:114-128](../../../backend/huddle/agent.py#L114-L128)</sub>

## 谁被引导

点名智能体的消息只引导这些运行，其他智能体照常写作。不点名的消息引导所有运行中的智能体。拒绝引导的运行（刚刚完成）会让该智能体成为新回复的候选者。若消息只引导了正在写的智能体，新的一轮就是*纯引导*：空闲智能体仍会做决定，但没有人被强制回答。

<sub>来源: [orchestrator.py:189-213](../../../backend/huddle/orchestrator.py#L189-L213)</sub>

## 停止

只包含停止短语（可带名字；短语在 `config/language.yaml` 中）的消息，或 Stop 按钮，会结束工作而不是引导。在锁下，`_stop_locked` 取消目标运行和所有指令提取，把它们的流式消息标记为 `stopped` 并清除输入状态；被取消的任务只在释放锁之后才等待，因此不会误收更新一轮的输出。被取消的运行丢弃未刷新的缓冲：停止不会再添加文本。停止后紧接着的未点名后续消息（"ok, the short version"）发给被停止的智能体。

<sub>来源: [orchestrator.py:502-550](../../../backend/huddle/orchestrator.py#L502-L550) · [orchestrator.py:330-340](../../../backend/huddle/orchestrator.py#L330-L340) · [agent.py:467-471](../../../backend/huddle/agent.py#L467-L471) · [language.yaml:5-6](../../../config/language.yaml#L5-L6)</sub>

## 常驻指令

"从现在起用中文回答"必须影响被引导的消息和之后的每一条。每条用户消息还会交给后台提取器，它存储持久指令，作用于所有人或某一个智能体。提取按到达顺序串联，所以即使连发多条，后来的指令也会覆盖先前的；下一轮最多等待 `extract_timeout_s` 让上一次提取完成。引导本身已经把要求应用到正在写的消息上。

指令位于每个系统提示词的末尾附近，紧挨在身份规则之前，并在每次调用末尾作为提醒再出现一次。这条规则就是唯一的限制：指令可以改变语言、长度、格式或重点，但绝不能改变智能体是谁。

<sub>来源: [orchestrator.py:201-207](../../../backend/huddle/orchestrator.py#L201-L207) · [orchestrator.py:218-242](../../../backend/huddle/orchestrator.py#L218-L242) · [orchestrator.py:361-366](../../../backend/huddle/orchestrator.py#L361-L366) · [prompts.py:78-86](../../../backend/huddle/prompts.py#L78-L86)</sub>

## 每个会话一把锁

凡是必须与日志一致的决定（创建消息、在 `done` 之前清空收件箱、停止、快照）都在会话的 `ConversationLock` 下运行。它在同一任务内可重入，所以运行可以持有它并调用会再次获取它的 `Store` 方法，但从不与子任务共享。若进程在回答途中退出，启动时会把所有 `streaming` 消息标记为 `stopped`；没有任何内容被重排或改写。

<sub>来源: [store.py:55-90](../../../backend/huddle/store.py#L55-L90) · [store.py:362-364](../../../backend/huddle/store.py#L362-L364)</sub>
