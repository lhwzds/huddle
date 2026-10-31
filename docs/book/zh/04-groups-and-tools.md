---
title: 群组、记忆与工具
---

# 群组、记忆与工具

<sub>[English](../04-groups-and-tools.md) · **中文**</sub>

智能体是谁、知道什么、能做什么，都是数据而不是代码。本页介绍群组及其成员、两种多回合模式（自由讨论和合并），以及工具：沙箱、网络研究和 `remember`。

<sub>来源: [huddle.yaml:48-50](../../../config/huddle.yaml#L48-L50)</sub>

## 群组是数据

群组是一组智能体加上每个成员都知道的共享上下文。`config/groups/` 中附带三个预设：创业公司领导团队（3 人）、一个家庭（5 人）和发布作战室（10 人）。每个预设是一个带 frontmatter 的 `group.md`，加上每个智能体一个 markdown 文件，其中 `## Persona`（灵魂）和 `## Private knowledge`（记忆）两节；格式见[群组](../../configuration.md#groups)。

首次启动时，`AgentRegistry` 把预设写入 SQLite；此后数据库是唯一来源，每次编辑都会递增 `registry.version`，使用于识别点名的名字模式缓存失效。会话存储自己的群组键，编排器每轮都读取该群组的成员。提示词不含任何预设名字或事实，所以同一套规则既适用于家庭，也适用于公司。

<sub>来源: [agents.py:49-123](../../../backend/huddle/agents.py#L49-L123) · [config.py:90-106](../../../backend/huddle/config.py#L90-L106) · [orchestrator.py:51-72](../../../backend/huddle/orchestrator.py#L51-L72) · [CONTRIBUTING.md:26-29](../../../CONTRIBUTING.md#L26-L29)</sub>

## 编辑灵魂与记忆

应用通过 `/api/groups/{group}/agents` 编辑成员，通过 `PUT /api/groups/{group}` 编辑群组共享上下文。`AgentRegistry` 校验名字（组内唯一，可用任何文字）、颜色和文本长度，限制群组人数，并由名字生成稳定的键。这些上限是 `config/huddle.yaml` 中的 `limits.fields`；网页应用通过 `GET /api/config` 读取同样的数字，所以浏览器和服务器从不冲突。重置会从文件重新写入预设。编辑是全局的：该群组的每个会话在下一次调用时都会看到，因为上下文每次调用都重新构建（[一轮对话如何运作](02-how-it-works.md)）。

<sub>来源: [agents.py:126-193](../../../backend/huddle/agents.py#L126-L193) · [agents.py:239-244](../../../backend/huddle/agents.py#L239-L244) · [api.py:92-160](../../../backend/huddle/api.py#L92-L160) · [huddle.yaml:82-84](../../../config/huddle.yaml#L82-L84)</sub>

## remember 工具

`remember` 往智能体自己的记忆里追加一条简短笔记，放在 `## Remembered` 下。记忆由群组的所有会话共享，所以只有当对方的消息（或送达该运行的引导消息）提到"记住"时，才提供这个工具，每条消息一次：`config/language.yaml` 里的 `remember` 模式会设置 `RunContext.can_remember`。执行时还会再检查：没有它的 `remember` 调用，或者调用了未提供的工具，都会以错误作答，绝不执行。笔记长度上限为 `remember_note` 个字符；记忆已满时会拒绝笔记，而不是截掉旧事实。

<sub>来源: [agents.py:220-237](../../../backend/huddle/agents.py#L220-L237) · [agent.py:301-310](../../../backend/huddle/agent.py#L301-L310) · [agent.py:422-425](../../../backend/huddle/agent.py#L422-L425) · [agent.py:326-329](../../../backend/huddle/agent.py#L326-L329) · [orchestrator.py:433-435](../../../backend/huddle/orchestrator.py#L433-L435) · +2</sub>

## 自由讨论

明确的"讨论/辩论"请求（词表在 `config/language.yaml` 中），或两个以上智能体想以 `deep` 努力回答的难题，会把这一轮变成讨论。回合数和消息上限提高，每个空闲智能体可以回应有限次数，decide 提示词切换为"回应别人刚说的，否则保持沉默"。除非请求了文件，讨论消息保持聊天长度且不用工具。

若讨论至少有 `conclusion_min_messages` 条消息、来自至少两个智能体，`_conclude` 会让第一个发言者再运行一次，带上撰写群组结论的指示：建议、附关键数字的理由，以及谁仍持异议。

<sub>来源: [orchestrator.py:404-413](../../../backend/huddle/orchestrator.py#L404-L413) · [orchestrator.py:426-430](../../../backend/huddle/orchestrator.py#L426-L430) · [orchestrator.py:478-500](../../../backend/huddle/orchestrator.py#L478-L500) · [prompts.py:97-102](../../../backend/huddle/prompts.py#L97-L102) · [prompts.py:235-244](../../../backend/huddle/prompts.py#L235-L244)</sub>

## 合并回合

像"要不要接这单生意？"这样的问题，需要分散在不同记忆里的事实。两部分配合起作用。系统提示词要求每个智能体直白地陈述自己的私有事实（最初的要求和截止日期，而不只是它偏好的折中方案）。然后，若第一回合中至少有两个智能体发言、且至少一个的努力高于 `light`，每个空闲智能体都获得一次合并回合：只有当能把别人刚分享的事实与自己的事实结合成一个无人说过的结论时才发言。

<sub>来源: [orchestrator.py:470-481](../../../backend/huddle/orchestrator.py#L470-L481) · [prompts.py:89-94](../../../backend/huddle/prompts.py#L89-L94) · [prompts.py:66](../../../backend/huddle/prompts.py#L66)</sub>

## 努力等级决定工具权限

努力等级决定消息的长度建议、token 上限和工具预算；只有 `deep` 有沙箱工具预算（`max_tool_steps`）。运行一旦用过工具，超出预算后仍会在 `tool_budget_grace_steps` 步内继续提供工具，但这些调用会返回"预算已用完"：中途撤掉工具会让某些模型把原始工具标记写成文本。宽限步数用完后工具被撤下，从而终止不停调用的模型。网页调用另有每条消息的上限。每批工具调用之后，运行比较前后的工作区文件列表并发出 `file.changed`，刷新文件面板。

<sub>来源: [huddle.yaml:56-59](../../../config/huddle.yaml#L56-L59) · [agent.py:416-421](../../../backend/huddle/agent.py#L416-L421) · [agent.py:371-376](../../../backend/huddle/agent.py#L371-L376) · [prompts.py:6-10](../../../backend/huddle/prompts.py#L6-L10)</sub>

## 网络研究

由于沙箱没有网络，`web_search` 和 `fetch_url` 在后端运行。只允许 http 和 https；重定向手动跟随，每一跳的主机只要有任何地址是私有、回环、链路本地、组播地址或云元数据名称就拒绝。随后 `page_client` 只连接通过该检查的地址（`_PublicOnlyBackend` 在连接时只解析一次名称），所以 DNS 重绑定到不了私有地址。大小、时间和文本长度都有上限，错误以普通句子交给模型。每个结果都会连同来源链接写入工作区的研究文件夹，方便人核对智能体读过什么。

<sub>来源: [web.py:1-9](../../../backend/huddle/web.py#L1-L9) · [web.py:140-207](../../../backend/huddle/web.py#L140-L207) · [web.py:456-507](../../../backend/huddle/web.py#L456-L507) · [web.py:533-562](../../../backend/huddle/web.py#L533-L562) · [tools.py:116-129](../../../backend/huddle/tools.py#L116-L129)</sub>

## 沙箱

`bash`、`read_file` 和 `write_file` 通过 HTTP 发往一个只用标准库、运行在独立容器中的服务：非 root 用户、只读根文件系统、没有任何 capabilities、限制 CPU、内存和进程数，且无法连接互联网。每个会话有一个工作区目录。命令以 `bash -c` 在新会话中运行，环境最小化，超时被钳制，输出有上限，每个工作区最多同时运行四条命令。终止针对整个进程组，直到它彻底消失，所以 fork 炸弹可以恢复；`sweep_detached`（`HUDDLE_SANDBOX_SWEEP=1`，由 `sandbox/Dockerfile` 设置）还会杀掉用 `setsid()` 离开自身会话的进程。空闲工作区在 TTL 之后删除。磁盘预算计入隐藏文件，命令运行期间大约每秒检查一次（尽力而为）。[SECURITY.md](../../../SECURITY.md) 说明了它不防护什么。

<sub>来源: [server.py:451-475](../../../sandbox/server.py#L451-L475) · [server.py:649-674](../../../sandbox/server.py#L649-L674) · [server.py:312-342](../../../sandbox/server.py#L312-L342) · [server.py:345-355](../../../sandbox/server.py#L345-L355) · [server.py:729-741](../../../sandbox/server.py#L729-L741) · +4</sub>
