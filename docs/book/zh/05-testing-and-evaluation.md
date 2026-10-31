---
title: 测试与评估
---

# 测试与评估

<sub>[English](../05-testing-and-evaluation.md) · **中文**</sub>

Huddle 有两层检查。单元和集成测试离线运行，使用脚本化模型；评估运行器用真实模型驱动一个正在运行的实例，并为得到的对话打分。两者的命令见 [README](../../../README.md)；测试计划见[测试规范](../../specs/testing.md)，数据集格式见[评估规范](../../specs/eval.md)。

<sub>来源: [AGENTS.md:5-12](../../../AGENTS.md#L5-L12)</sub>

## 离线测试

后端测试从不访问网络。`FakeLLM` 按智能体、profile 和提示词文本的规则回复，分块和延迟都可配置，所以测试可以让 Theo 慢慢流式输出，并在消息中途发送引导。`FakeSandbox` 记录命令，并能阻塞某条命令直到放行，引导工具调用的测试就是这样做的。大部分运行时用例（引导、停止短语、点名、回合、接缝）位于 `backend/tests/test_runtime.py`。

前端测试（Vitest）覆盖事件归约器（包括缺口检测），以及群组、资料和跟随滚动等辅助函数。沙箱测试启动真实服务并运行真实进程。评估测试在小型合成事件日志上运行检查、指标和对话记录标记。CI 在每次推送和拉取请求时运行全部四套测试；[CONTRIBUTING.md](../../../CONTRIBUTING.md) 列出了同样的命令。

<sub>来源: [fake.py:1-45](../../../backend/huddle/llm/fake.py#L1-L45) · [conftest.py:13-38](../../../backend/tests/conftest.py#L13-L38) · [test_runtime.py:277-308](../../../backend/tests/test_runtime.py#L277-L308) · [state.test.ts:44-58](../../../frontend/test/state.test.ts#L44-L58) · [test_server.py:15-30](../../../sandbox/tests/test_server.py#L15-L30) · +2</sub>

## 评估数据集

`eval/cases/*.yaml` 包含 36 个覆盖全部三个群组的脚本化对话。每个用例列出步骤（`say`、`wait` 等待某智能体写到若干字符或进入空闲、`stop`、`reload`、`burst`）、确定性的 `checks`，以及自然语言的 `judge` 陈述。类别涵盖引导、停止、沉默、智能体之间的往来、合并知识、对抗输入（提示词注入、恶意 shell 命令）以及更大的群组。

<sub>来源: [steer.yaml:1-30](../../../eval/cases/steer.yaml#L1-L30) · [core.py:14-20](../../../eval/huddle_eval/core.py#L14-L20) · [core.py:106-136](../../../eval/huddle_eval/core.py#L106-L136)</sub>

## 运行器如何打分

`eval/run.py` 是黑盒的：它通过 HTTP 创建会话，带接收时间戳跟随 SSE 流，并执行各个步骤。"空闲"由服务器判定，而不是一段静默期：快照的 `active` 标志为假、没有智能体在输入或流式输出，且最后一次发送已过去至少 1.5 秒，连续两次轮询都如此，所以较慢的 decide 调用不会被误判为结束。最后它重新加载，并把事件冻结在快照的 `last_seq`。

确定性检查是硬性门槛：`same_message_continued`（按服务器的 `seq` 顺序而非客户端时钟，同一条消息在后一条用户消息存储前后都有增量）、`no_new_text_after_stop`、`reload_identical`（快照等于事件日志的重放）、隐私方面的 `no_leak` 短语、`ui_words_absent`（不谈引导或指令），以及发言者集合、努力等级、文件和延迟。评审是一个配置好的模型（`--judge`，否则用当前模型），它读取在精确字符位置插入了"用户消息到达"标记的对话记录，对每条陈述给出判定和理由。

<sub>来源: [runner.py:310-375](../../../eval/huddle_eval/runner.py#L310-L375) · [runner.py:46-53](../../../eval/huddle_eval/runner.py#L46-L53) · [core.py:93-105](../../../eval/huddle_eval/core.py#L93-L105) · [core.py:142-160](../../../eval/huddle_eval/core.py#L142-L160) · [core.py:240-270](../../../eval/huddle_eval/core.py#L240-L270) · +1</sub>

## 延迟指标

每次发送有两个数字。首词：从发送返回到该轮智能体消息出现第一段文本的时间。引导延迟：从发送的 POST 到一条已在流式输出的消息中续写的第一个词的时间；若发送中点了名，则只看被点名的智能体。模型在引导之前写下的文本仍会在引导之后立即刷出，所以 `continuation_arrival` 取引导之后存储的、按服务器时钟比引导和前一个增量都晚至少 `CONTINUATION_GAP_S`（一次模型往返）的第一个增量；这是估计值，自行停顿那么久的流也会被当作续写。结果按每次运行写成 JSON。

<sub>来源: [core.py:164-237](../../../eval/huddle_eval/core.py#L164-L237)</sub>
