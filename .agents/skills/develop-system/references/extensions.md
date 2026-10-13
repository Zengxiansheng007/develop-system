# 扩展接口

注册表 schema v3，controller_policy 固定 dispatch-only。未来能力在 registry.json 注册，或只在当前运行 temporary_skills 登记，不需要改写调度主体，也不要为未实现能力创建空目录。每项必须显式声明 required_calls；旧 v2 快照与缺字段不能解释为空义务，也不会自动升级。

## 描述字段

| 字段 | 约定 |
|---|---|
| id | 唯一小写字母/数字/连字符标识 |
| stage | stages 中的阶段；可扩展新阶段 |
| role | entry/primary/auxiliary；仅 develop-system 为 controller |
| enabled | 候选能力是否启用 |
| when | 适用场景 |
| done_when | 专业技能自己的完成判据 |
| requires | 能力级依赖的技能 id；任务依赖在计划 depends_on |
| required_calls | 本能力每次完成前必需回流的辅助技能 id 数组；空数组须明确声明，与派发前置 requires 分别判断 |
| outputs | 专业产物/结论类别 |
| return_to | develop-system |
| fallback | 固定 blocked，缺失不得由总路由代做 |

可选 path 为真实 SKILL.md（相对注册表目录或绝对路径）；缺省按协议发现。next_candidates 仅是建议，不绕过回流和范围。禁用 inline；ask-matt 不能是执行能力、别名或候选。entrypoints 保持 develop-system execute 与 ask-matt navigate 两入口。

```json
{
  "id": "api-contract-check",
  "stage": "development-testing",
  "role": "auxiliary",
  "enabled": true,
  "when": "变更影响对外API契约",
  "done_when": "相关契约已验证并返回证据及结论",
  "requires": [],
  "required_calls": [],
  "outputs": ["契约验证报告"],
  "return_to": "develop-system",
  "fallback": "blocked",
  "next_candidates": ["code-review"]
}
```

## 接入协议

分派注入 [统一执行协议](codex-runtime.md) 的实际绝对路径。专业技能承担执行/本帧验证，所有结束状态先回流；可返回 acceptance_checks 与 resolved_findings。总路由仅核对结构、版本、证据文件和依赖，不因新技能接入而取得业务验收职责。

不要求永久改写第三方正文；同目录技能可链接 ../develop-system/references/codex-runtime.md。未来专项验收技能也用同一结果接口，必须具备真实实现和专业证据，不能只创建名为验收的空技能。

register 保存临时描述，extend-plan 将其加入本 run；共享 registry 不受临时注册影响。永久接入后运行 scripts/validate_registry.py 并同步项目/全局版本。已有 run 保留启动快照，不偷偷替换配置；语义迁移按状态操作文档执行。

## 必需闭包与来源

registry.json 的 required_calls 是唯一可执行的无条件辅助声明；正文解释专业步骤并指向该字段，不另维护依赖表。next_candidates 是建议，条件修复由专业技能选择本次子帧及 required，不加入无条件图。

validate_registry.py 检查整个包的声明闭包，报告直接/间接漏登记、禁用、文件缺失、名称不符、不可辅助调用与循环。仅参与必需调用的实际来源受到闭包资格检查，不能据此声称其它未调用技能已执行或通过验收。

运行创建只核对结构和所选计划；RunStore.preflight(skill_id, skill_path=None) 只读核验当前选中调用、必需闭包及相关前置能力。它与 dispatch 使用同一真实解析，预检成功不替代授权、就绪或容量检查。缺失的无关能力不阻断独立工作。包内默认 path 固定来源；显式路径失效不回退，无路径且多位置同名则须明确选择。

dispatch 保存 selected_skills 的来源版本和父帧 completion_obligations，再创建子帧。后续选择不得覆盖已固定路径、内容或调用策略；临时登记不能覆盖已有同名/禁用项。手动入口可以显式选为根任务，禁止模型调用或不允许隐式调用的来源不能作为无条件辅助目标。

本次父任务须收到身份、来源及当前证据有效的直接辅助回流；未创建、未回流、过期、未决或其它父任务的结果均不能抵偿。无条件义务不能用 required=false 规避，条件辅助工作沿用本次 required 和跳过理由。无命令的参考应用以真实应用摘要和文档产物回流。运行漏登记的局部登记修正闭环由 #8 交付；本票不自动修正旧运行。
