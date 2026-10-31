---
title: 切换模型
source: #a46668e39c9f
---

模型由配置选择：`HUDDLE_MODEL` 选中 `config/huddle.yaml` 中 `models:` 下的一项（默认 `deepseek`），`make_llm` 构建对应的提供方（`openai_chat` 适用于任何 Chat Completions API，另有 `openai_responses` 和 `fake`）。代码里不写死任何模型。

每次调用都带一个 *profile*：`decide`、`steer`、`utility`，或努力等级 `light`、`normal`、`deep`。`params_for` 把该项的 `default` 参数与此 profile 的参数深度合并；正是靠这一个设置，引导调用用最低推理档，深度工作用更高档。智能体可以指定自己的模型；`Orchestrator._llm` 为每个模型键缓存一个客户端。各字段说明见[模型](../../configuration.md#models)。
