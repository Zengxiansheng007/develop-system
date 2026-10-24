# Issue 完成同步：GitHub 能力研究

日期：2026-10-22（Asia/Shanghai）
适用背景：develop-system 0.2.0 / state v4 / registry v3；本轮仅需求访谈与文档，不启用自动化。
研究执行：matt-research，顺序执行；未委派代理。
研究问题：在不改 Matt 工程 Skill 的前提下，GitHub 能否支持按整票验收、PR 合并和对应 CI 资格自动更新并关闭 Issue，以及哪些语义需要由 develop-system 明确？

## 已证实事实

1. GitHub 的 Issue 更新接口支持 open/closed 与 completed 等关闭原因；同一接口还可更新正文。传 labels 会替换标签集合，因此不能将当前系统管理的几个标签误当成全部标签。细粒度凭据可用 Issues 写权限执行该操作；是否具备当前仓库权限仍需实际连接核验，文档不是本机授权证明。[Issue 更新接口](https://docs.github.com/en/rest/issues/issues#update-an-issue)

2. PR 接口可核对合并事实；merge_commit_sha 在合并前后含义不同，合并、squash、rebase 的结果也不同。不能把合并前的临时测试合并提交直接当作最终交付版本。[PR 接口](https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request)

3. Check Runs 可按固定提交查询，结果有状态与结论。分支名会移动，因此绑定提交的核对需要保留相应 SHA；哪些检查属于本项目必需检查仍是配置与需求决定，不由任意一条绿色检查替代。[Check Runs](https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference)

4. GitHub Actions 的 workflow_run 可以在另一个工作流结束后触发后续工作流；触发本身不表示上游成功，必须读取其 conclusion。该工作流文件需位于默认分支。后续工作流可能持有写权限，因此使用什么可信材料、是否执行外来代码必须单独约束。[工作流事件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run)

5. 关联 PR 或使用关闭关键词时，合并到默认分支可自动关闭 Issue；其它目标分支的关键词行为不同。原生自动关闭不认识 develop-system 的本地整票验收记录。[PR 与 Issue 关联](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue)

6. GitHub 建议使用事件而非频繁轮询；写请求串行处理，批量写入之间保持间隔，限流时遵守服务返回的等待要求。条件 GET 的缓存能力不构成 Issue PATCH 已支持原子比较并写入的证明。[REST 最佳实践](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)

## 当前项目事实

- 调度器 complete 操作核对必要工作、必需助手、发现、证据新鲜度与逐项专业验收来源，最后写本地 completed；这一操作本身不调用 GitHub。来源：../../.agents/skills/develop-system/scripts/workflow_state.py 的 RunStore.complete。
- 术语表已区分“专业职责完成”和“整票验收”。来源：[GLOSSARY](../../GLOSSARY.md)。
- Matt 来源的 implement-spec 正文已有按所选 tracker 解决工单的步骤；本地 Codex 适配明确远端操作仍需授权。来源：../../.agents/skills/implement-spec/SKILL.md。
- 当前五种分诊标签映射见 [triage-labels](../agents/triage-labels.md)；不能静默为所有项目复制同一套标签。

## 早期设计分析（采纳结果见访谈决定）

- 可将 GitHub 传输和关闭判断放在 develop-system 自有层，消费专业验收事实而不改 Matt 工程 Skill。
- 若要求对话结束、电脑离线后仍自动执行，GitHub Actions 是候选承载方式；事件能触发，不表示本地验收材料已自动可用。
- 关闭关键词/原生关联可能绕过本系统的额外条件；应结合最终选定的验收与 CI 规则明确处理方式，不能简单认为写 Closes 就完成本需求。
- 需分别描述本地验收、远端同步进度与远端实际状态；HTTP 成功也需核对实际写入结果。

## 2026-10-22 收尾补充研究

GitHub 官方说明，PATCH 等写方法通常不支持条件请求，除非具体接口另有说明；不能把条件 GET 或 CORS 允许 If-Match 请求头解释为 Issue 正文具有原子条件写入保证。当前未取得该正文接口的例外依据。[官方条件请求说明](https://docs.github.com/en/enterprise-cloud%40latest/rest/using-the-rest-api/best-practices-for-using-the-rest-api)

设计推断：写前重读、写后核对可帮助识别冲突，但不能单独证明任意并发编辑都不会被覆盖。这影响 Q6 自动勾选原正文的保证范围，访谈新增 Q13 取舍；用户已选 A，保留自动勾选并接受项目单写入约定，检测到变化暂停该票，不宣称强制锁。

## 访谈决定与当前状态

Q1–Q12 已明确自有层修改、关闭门槛、项目配置、仅 Codex 执行期间处理、父规格、更新字段、项目启用、合并提交 CI、补同步、人工重开暂停、仅新运行与一票一 PR。后台 Actions 仅保留为曾研究的备选事实，不属于本轮方案。

Q13=A 已由用户明确选择（重复文本按同一决定记录）。业务问题已解决，完整需求文档 1.0 已由用户于 2026-10-22 在当前对话回复“确认”。下一阶段路线尚待明确选择。此前研究的后台 Actions 备选不属于采纳方案；后续接口与配置设计不表示已经实现或启用。

本研究没有修改 GitHub、源码、Skill、工作流配置或执行台账，没有运行修复测试；没有验证任何自动关闭端到端链路。
