---
name: implement-spec
description: "Implement the result of /to-spec and /to-tickets in code."
---

## Codex执行与回流约定

加载、子代理及结果格式遵循 [统一执行协议](../develop-system/references/codex-runtime.md)。在显式 develop-system 流程内，本技能负责自己的专业工作与完成判断；所有结束状态先返回 develop-system 登记，再由它恢复父技能或调度下一工作。总路由只核对协议、版本和依赖，不代做实现、测试、评审或验收。流程外按本技能独立执行并返回调用方。

正文的 Skill 工具及 `/skill` 写法，在 Codex 中按真实路径读取对应 SKILL.md。嵌套专业工作登记子帧，结果先回流总路由。委派有授权和可用工具时使用真实子代理，否则切换到专业技能顺序执行；仅修改技能文件不启动子代理。分支、提交、远程工单和 PR 操作遵守用户授权与实际仓库条件，不假定已获发布或外部写入授权。

本技能负责规格任务图、就绪任务和集成的专业判断，并向 develop-system 提交 implement/tdd/code-review 子帧请求。总路由统一创建帧、分派真实代理和合并状态；实现、集成及集成验证在专业执行角色完成。有授权和隔离条件时并行，无工具/槽位/隔离条件时逐项 implement，准确报告顺序执行。禁止重置用户改动；创建隔离工作区前确认实际 Git 状态。

You have been provided a spec. This spec should have tickets associated with it, describing how to implement the spec.

The issue tracker should have been provided to you. If not, tell the user to run `/setup-matt-pocock-skills`.

The goal is the entire spec implemented on a single **integration branch**, with every ticket resolved the way the issue tracker closes work.

The tickets are not a list of steps. They are a **task graph** with blocking relationships between them. This means there is always a **frontier** of tickets which are ready to be grabbed.

Communication to and from subagents should be sparse. Communicate primarily through **context pointers**: to the spec, tickets, research notes, and previous commits. Don't duplicate information already available via pointers.

**Implementer subagents** should be run in the background where possible for maximum concurrency.

## Steps

1. Read the spec and tickets to understand the task graph.

2. (optional) Use an **exploration subagent** to conduct any exploration required by the tickets - relevant codebase files or external documentation. Ensure the exploration subagent can save files - it should save its markdown notes in a directory outside the repo, accessible by all future subagents. This lets **implementer subagents** focus on implementation rather than exploration.

3. Create the integration branch. If the issue tracker closes work through PRs, or the user asks for one, open a draft PR after the first merge in step 5 (a branch with no commits ahead of main can't open one), marked as closing the spec and tickets.

4. Use **implementer subagents** to implement each ticket, each in its own worktree on its own branch. Each implementer subagent:
   - confirms its worktree is based on the integration branch before starting, and resets onto it if not;
   - calls the Skill tool with `tdd` to build the ticket;
   - merges the integration branch tip into its own branch before reporting done

5. Once an **implementer subagent** completes, merge its work to the integration branch with a **merger subagent**.

6. If this changes the **frontier** of available tickets, kick off more **implementer subagents** to work on the new tickets. This allows for maximum concurrency.

7. Once all tickets are complete, call the Skill tool with `code-review` on the integration branch. Fix all issues raised by the code review in a single **implementer subagent**.

8. If a draft PR exists, mark it ready for review. Otherwise, resolve each ticket the way the issue tracker closes work, and report the integration branch.

9. Clean up all **implementer subagent** worktrees.
