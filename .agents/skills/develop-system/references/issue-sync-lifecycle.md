# 工单同步：前台生命周期与项目接入

本文件是 develop-system 自有接线、首接配置和 PR 交接的操作契约，随 Skill 包分发。读取后先区分当前分支：项目接入、新运行、专业回流、交付事实更新、明确继续、暂停收件或 PR 发布交接。执行权威仍是原 RunStore；同步日志只保存远端进度及来源引用。

## 默认禁用与明确接入

[首接模板](issue-sync-project.disabled.json) 是 schema 1 配置值，保持 `enabled: false`。读取、打包、打开项目和加载本 Skill 均不采用配置。真实采用记录只能由 `IssueSync.configure(value, user_reference=...)` 在明确项目授权后创建；它记录代次、配置指纹和授权引用。非空引用是调用者提供的归属依据，不是工具对用户身份的认证。

接入按以下顺序完成，每步条件满足后才继续：

1. 核对项目负责人明确批准的项目、仓库稳定身份、目标分支、全部必需 CI、连接引用、标签集合和正文单写入约定。模板中的 `ready-for-agent` 表示只移除该配置名称；其它标签保留。它不声明远端当前存在此标签。需要更改清理集合时，先取得该具体集合的项目授权。
2. 由宿主提供已批准的 `token_provider(reference)` 或显式 GitHub Adapter。`RunStore(project, run_id, github=adapter)` 与 `RunStore(project, run_id, token_provider=approved_resolver)` 只注入外部边界。默认 Adapter 使用配置的命名引用；缺 resolver、引用不符或连接不可用只留下待办。源码不搜索账号笔记、环境、凭据文件或其它连接；凭据值只在 Adapter 请求内存中存在，不能写入参数文件、配置、日志或交接材料。
3. 授权明确后，调用 `configure` 采用该配置值。只有随后真正创建的新运行固定当前代次；旧运行、重开目录、resume、bind 和修改时间都不能补出新运行资格。禁用或相关配置变化后，旧代次资格停止使用；重新启用不会接管历史运行或解除工单人工暂停。
4. 新建运行时同时声明原票全部 AC、必要工作闭包、专业输入版本和候选范围，使用 `RunStore.create(..., ticket_scopes=[...])`；CLI 对应 `init --ticket-scopes <JSON>`。随后通过 `IssueSync.bind` 明确固定仓库/Issue、一个最终 PR、正文映射和父规格标记。在已批准的配置与绑定范围内，后续合格同步无需逐票再确认。

首接身份来自 2026-10-23 的只读仓库/Actions 运行记录及 2026-10-22 的作业记录：仓库 `1416632993 / Zengxiansheng007/develop-system`，目标 `main`，workflow `383018657 / .github/workflows/check-skills.yml`，事件 `push`，展开作业 `check (windows-latest)`、`check (ubuntu-latest)`。Actions 选择器不要求 `app_id`。记录所见运行 `39598641383` 属于旧 main `cd09465e09cb50525c40fab76a35830d7cc3266b`，不是当前候选 CI 或真实启用证明。2026-10-23 作业刷新超时未产生新作业身份结论；workflow 对象端点不受连接器支持，也不等于权限拒绝。

实际启用时重新核对相关远端身份；保留独立日期与来源：[仓库](https://api.github.com/repos/Zengxiansheng007/develop-system)、[Actions 运行](https://github.com/Zengxiansheng007/develop-system/actions/runs/39598641383)。两个配置项在最终实际交付 SHA 上均须明确成功，较新相关尝试、缺项和歧义继续阻止关闭。

## 生命周期与完成条件

RunStore 的原返回形状、state v4、registry v3、结果协议 1 保持。自动节点在原本地事务保存后调用同一个 `IssueSync.sync_once`；显式继续调用已有 `continue_sync`，两者共用资格、写入和恢复主入口。

| 实际入口 | 同步动作 | 本节点完成条件 |
| --- | --- | --- |
| `RunStore.create` / CLI `init` | 固定新身份与启用代次后同步一次；未绑定时不初始化凭据 resolver | 新运行已落盘；同步结果是准确待办或真实读回事实 |
| 新的有效 `RunStore.receive` | 仅依据已接收的实际 completed 专业事件，通过原整票投影固定既有绑定当前有效来源，再同步一次 | 原票完整资格仍由原 RunStore 决定；帧完成本身不制造通过 |
| `RunStore.complete` | 原完整运行验收通过并落盘后，以同一投影核对各原票，再同步一次 | completed 终态保留；共享 PR 和子票状态不替父规格验收 |
| `RunStore.delivery_updated()` / CLI `delivery-updated` | 重读当前 PR、交付内容、合并 SHA 与必需 CI | 真实进度已保存；缺 CI 时本轮待办结束 |
| `RunStore.continue_project(user_reference=...)` / CLI `continue-project` | 明确指定项目和 run 后补同步；可用于 completed | 先核远端，只补缺少步骤；原终态、事件、证据、授权和额度字节保持 |
| `RunStore.resume(..., user_reference=...)` | 原恢复/失效规则完成后，仅在明确用户继续依据存在时补同步 | 用户暂停依原规则解除；普通无用户依据的内部 resume 不发起同步 |

自动创建/专业回流/完成仍返回原 RunStore 状态。宿主可通过 `store.last_issue_sync` 查看本次同步结果，通过 `IssueSync.status(store)` 读取持久绑定与进度；同步局部失败不改成全局开发失败。明确继续和交付通知直接返回同步结果，不消费原执行 revision。

未启用、无当前代次或未绑定时保持零远端写入。构造 RunStore、读取状态、CLI `show/next`、ask-matt 导航和打开应用均不触发同步。停止前台调用后没有同步 worker、后台定时器、持续轮询或自动下一票。每票使用既有有限 Adapter 请求/轮次约束，默认请求 10 秒、轮次 60 秒；CI 未完成只保存待办并结束，服务等待使用原持久截止时间，不重跑 CI。

## 暂停、收件与来源保护

用户暂停或取消期间不发起新的远端写入。若请求已在途，只读回核对并持久保存该次真实回执，随后检查当前控制并结束；后续标签或关闭不继续。暂停前的成功与未知操作均保留。

专业工作已在途且在用户暂停期间返回时，原 RunStore 仅保存合法回执；同步日志可保存该真实 `event_id` 的 deferred 引用，不采用关闭候选或远端写入。重新构造前台实例后，只有明确恢复控制，才以这些已保存事件和原整票投影重新核对。已应用引用保留历史状态。没有真实 deferred 事件的普通继续不刷新绑定专业来源；后续源码变化仍须真实专业重新核验，不能借补同步悄悄接受。

completed 补同步走 `continue_project`，不调用 `resume`、重建 run、迁移或重置额度。paused/cancelled 补同步返回控制待办；暂停须先按原运行协议明确恢复，取消的原目标保持终态。工单人工暂停独立于运行控制：普通继续、重新启用、连接更换和旧关闭条件不代替针对该票的明确 `IssueSync.take_over`。

## 自有 PR 发布交接

提交给发布者的本功能 PR 草稿先经过 `store.pr_handoff(body)`。只有 `status: ready` 的结果才是可用交接；传递其原样 `body`、`issue_references`、`native_auto_close: false` 和 `closing_policy`。用普通“Related to”引用绑定工单，发布者保留该限制。此方法只准备本地交接，不创建 PR、评论、合并、推送或远端关联。

绑定工单的原生 `Closes/Fixes/Resolves` 指令返回 `blocked/native_early_close_conflict`，不返回已批准草稿。相同保守检测也用于实际最终 PR 资格；它不是 GitHub 全部 Markdown 语法的实现。遇到人工已有提前关闭设置时保存冲突并交给作者处理，不静默改写 PR 或重开外部关闭的工单。远端写入由本同步门在整票专业验收、正确 PR 实际合并和对应交付提交全部必需 CI 成功后进行。Matt 的 pr、implement、implement-spec 等 24 个受保护 Skill 及来源保持原样。

## 验证与边界

本票行为通过真实临时 RunStore、明确 synthetic 专业事件、可控 GitHub 和实际 loopback HTTP 验证；这不是实际 GitHub 端到端或 Windows/Ubuntu CI 成功。模板和本操作协议可从生成 Skill 包加载，包从规范源码产生。真实项目保持默认禁用；不安装、发布、迁移或真实写入。

正文单写入约定是参与者协议。写前重读/写后核对可检测部分冲突；最后读取到 PATCH 之间仍有不可保证的并发覆盖窗口。局部锁不提供远端编辑锁、跨系统事务或 exactly-once。原日志保留已确认、失败和未知结果，关闭始终最后且需读回核对。
