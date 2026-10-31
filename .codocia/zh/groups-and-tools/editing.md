---
title: 编辑灵魂与记忆
source: #8924b141ec92
---

应用通过 `/api/groups/{group}/agents` 编辑成员，通过 `PUT /api/groups/{group}` 编辑群组共享上下文。`AgentRegistry` 校验名字（组内唯一，可用任何文字）、颜色和文本长度，限制群组人数，并由名字生成稳定的键。这些上限是 `config/huddle.yaml` 中的 `limits.fields`；网页应用通过 `GET /api/config` 读取同样的数字，所以浏览器和服务器从不冲突。重置会从文件重新写入预设。编辑是全局的：该群组的每个会话在下一次调用时都会看到，因为上下文每次调用都重新构建（[[how-it-works]]）。
