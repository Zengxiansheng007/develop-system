---
name: implement
description: "Implement a piece of work based on a spec or set of tickets."
---

## Codex执行与回流约定

加载、子代理及结果格式遵循 [统一执行协议](../develop-system/references/codex-runtime.md)。在显式 develop-system 流程内，本技能负责自己的专业工作与完成判断；所有结束状态先返回 develop-system 登记，再由它恢复父技能或调度下一工作。总路由只核对协议、版本和依赖，不代做实现、测试、评审或验收。流程外按本技能独立执行并返回调用方。

正文的 Skill 工具及 `/skill` 写法，在 Codex 中按真实路径读取对应 SKILL.md。嵌套专业工作登记子帧，结果先回流总路由。委派有授权和可用工具时使用真实子代理，否则切换到专业技能顺序执行；仅修改技能文件不启动子代理。分支、提交、远程工单和 PR 操作遵守用户授权与实际仓库条件，不假定已获发布或外部写入授权。

本技能负责单个任务或明确规格的实现及相关验证；tdd 和 code-review 为子技能。测试/评审返回总路由后恢复 implement；范围内缺陷完成修复和重新验证后再结束。当前请求允许时按既有提交约定处理，没有仓库时报告产物，不虚构提交。

Implement the work described by the user in the spec or tickets.

Use /tdd where possible, at pre-agreed seams.

Run typechecking regularly, single test files regularly, and the full test suite once at the end.

Once done, use /code-review to review the work.

Commit your work to the current branch.
