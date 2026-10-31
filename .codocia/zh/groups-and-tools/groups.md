---
title: 群组是数据
source: #562bf25ef017
---

群组是一组智能体加上每个成员都知道的共享上下文。`config/groups/` 中附带三个预设：创业公司领导团队（3 人）、一个家庭（5 人）和发布作战室（10 人）。每个预设是一个带 frontmatter 的 `group.md`，加上每个智能体一个 markdown 文件，其中 `## Persona`（灵魂）和 `## Private knowledge`（记忆）两节；格式见[群组](../../configuration.md#groups)。

首次启动时，`AgentRegistry` 把预设写入 SQLite；此后数据库是唯一来源，每次编辑都会递增 `registry.version`，使用于识别点名的名字模式缓存失效。会话存储自己的群组键，编排器每轮都读取该群组的成员。提示词不含任何预设名字或事实，所以同一套规则既适用于家庭，也适用于公司。
