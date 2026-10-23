# 状态操作

Python 3.10+ 工具只管理协议和持久状态。总路由写状态，专业技能执行及产生业务结论。仅显式 develop-system 流程使用；ask-matt 会被拒绝且不创建目录。

## 初始化与分派

工具绝对路径使用本次选中的 scripts/workflow_state.py。所有输入 JSON 为 UTF-8。

```powershell
$workflowTool=Join-Path $env:USERPROFILE '.codex\skills\develop-system\scripts\workflow_state.py'
python -B $workflowTool init --entry develop-system --project-root 'C:\project' --run-id checkout --goal '实现约定行为' --plan 'C:\project\plan.json' --acceptance 'C:\project\acceptance.json'
python -B $workflowTool show --entry develop-system --project-root 'C:\project' --run-id checkout
python -B $workflowTool next --entry develop-system --project-root 'C:\project' --run-id checkout
```

计划为 `[{"step_id":"implement-checkout","skill_id":"implement","depends_on":[],"required":true}]`，验收输入为非空条件字符串数组，初始化后获得编号 id。计划依赖与专业技能验收项一起传给执行者。

需人工确认或指定专业责任时，验收输入改为对象：`{"criterion":"用户确认业务效果","kind":"human","allowed_skill_ids":["wizard"]}`。字符串默认 technical；明确的人工项必须使用 human，不能被 technical 结果替代。技术需求评审可指定 allowed_skill_ids: ["code-review"]；设计/调研项指定对应专业技能。缺省空数组允许适当专业技能返回结论，具体能力边界由职责协议约束。

修改传刚读取的 --revision。init 不覆盖，锁冲突停止写入；不自动删除未知残留锁。子代理返回独立结果文件，只有总路由合并。

dispatch 使用 --skill-id、--step-id（子帧改用 --parent-frame-id）、--input-version，按需 --input / --skill-path。真实技能文件缺失失败；不产生 inline 帧。帧信息返回后由专业技能实际执行，所有层级先回流再恢复父任务。

发现技能缺失后用 `block --skill-id <技能> --step-id <就绪步骤> --reason <缺失路径/依赖>` 保存真实阻塞；辅助缺失用 --parent-frame-id。恢复前补齐依赖，不能靠 block 假装执行或验收已经完成。

return --payload <结果文件> 的结构见 [统一执行协议](codex-runtime.md)。artifacts 保存真实文件/hash，checks 如实记录验证，completion_checked 来自执行技能。重复事件幂等，同 id 冲突、旧版本和错误归属拒绝。父帧不能早于必要子帧完成，根步骤完成不等于整个 run 完成。

## 发现与专业验收来源

修复用 extend-plan --payload <新增步骤数组> --reason <范围内原因>；步骤 resolves_frames 指定待处理帧。修复/复核技能在结果 resolved_findings 中返回相应 frame_id 及证据。

`assess --frame-id <待处理帧> --source-frame-id <专业结论帧> --resolution <专业结论摘要> --evidence <该结论的真实文件>` 只登记来源。来源帧须已完成、无未决发现，且其事件明确列出目标 frame_id；证据必须属于该事件。总路由不能凭自己撰写的文件关闭发现。

业务验收在专业结果 acceptance_checks 返回；每项有 id、status: "passed"、completion_checked、kind、真实 evidence。人工项 kind: human 还须 human_confirmed 与实际 user_reference。接受事件时保存来源，不能在关闭时重新添加或篡改结论。

`complete --payload <引用数组>` 输入只需 `[{"id":"1","source_frame_id":"已回流专业帧"}]`。工具核对每项引用是否对应该事件的 passed 结论、帧及依赖已完成、证据和来源输入当前有效。缺失、失败、过期、人工未确认或调度器生成的验收断言拒绝。文件/hash 不代替专业判断。

## 恢复与迁移

- resume 检查技能、输入、产物、发现和验收证据 hash；变化只使相关帧/依赖及有对应来源的验收失效。有效结果和在途帧保留，未决发现不靠恢复抹去。系统限制按[专业恢复契约](scoped-state.md#系统限制与专业恢复)重新核验。
- 用户暂停期间 return 仅保存有效回传；明确继续时 resume 增加 --user-reference <真实用户指令来源>，保留有效在途帧。收件与应用结论的边界见[暂停收件](scoped-state.md#暂停收件与明确继续)。
- 已失效或中断旧帧由明确重新分派替代受影响后代，旧结果不能覆盖新帧。
- 环境健康由对应专业技能实际验证；哈希一致不证明服务存活。
- 同技能/输入/阻塞两次无进展停止重试；用户明确暂停/取消时 pause/cancel --reason，完成或取消 run 不恢复。
- 当前 T01–T10 候选创建 state v4、registry v3；新帧及回流必须携带 protocol_version: 1。必需助手与明确登记见[扩展接口](extensions.md)，工作树检查资格见[机器检查记录](check-records.md)。state v3 或 registry v2 旧快照显式采用前可读取但不能续写，缺义务字段不能当作空义务。需要评估、明确采用或恢复旧运行时，使用[迁移协议](migration-preview.md)中的 preview-migration / apply-migration / rollback-migration；清单内旧帧的原回传使用 return 留存，已回流专业结论使用[qualify-legacy](legacy-compatibility.md#专业核验)追加核验。暂停时只保存，不应用核验或关闭验收。

局部限制、就绪与完成的含义见[作用范围](scoped-state.md)。

## 工单同步前台入口

项目接入、新运行完整范围、自动节点、暂停收件和 PR 发布交接按[工单同步操作契约](issue-sync-lifecycle.md)执行；随包的[首接模板](issue-sync-project.disabled.json)保持禁用。宿主可向 `RunStore` 或 `main(argv, github=..., token_provider=...)` 注入批准的外部 Adapter/命名 resolver；CLI 不接受凭据值，也不自行搜索凭据。

`init --ticket-scopes <JSON>` 将原票全部 AC/必要闭包与新身份同时固定。`delivery-updated` 重核当前交付；completed 运行的明确补同步使用 `continue-project --user-reference <真实用户继续来源>`，两者不需要 `--revision` 且不写原执行状态：

```powershell
python -B $workflowTool delivery-updated --entry develop-system --project-root 'C:\project' --run-id checkout
python -B $workflowTool continue-project --entry develop-system --project-root 'C:\project' --run-id checkout --user-reference '<真实用户明确继续本项目任务的来源>'
```

这里的运行必须明确指定，来源占位符须替换为真实依据。paused/cancelled 控制与工单人工暂停继续生效；completed 不走 resume。缺少宿主连接时保留解释性同步待办，命令退出成功不代表远端已同步。

## 扩展与测试

register --payload <描述文件> 保存临时能力；根步骤再 extend-plan。描述使用 [扩展接口](extensions.md)，所有技能真实执行并回流，缺失阻塞。永久能力在共享注册表维护并校验同步。

已有漏登记限制时，先读取其身份和原来源授权，再使用 register --restriction-id <限制身份> --payload <描述文件>。完整步骤、暂停行为及版本拒绝条件统一见[当前运行漏登记](extensions.md#当前运行漏登记)。

`python -B -m unittest discover -s scripts -p 'test_*.py' -v` 验证状态与协议，不能证明每个业务技能已在真实项目执行，也不证明原生斜杠菜单注册。

## 命令检查证据

需要执行或登记命令验证时，按 [机器检查记录](check-records.md) 生成结果并传给 return。
