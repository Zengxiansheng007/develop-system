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

本次父任务须收到身份、来源及当前证据有效的直接辅助回流；未创建、未回流、过期、未决或其它父任务的结果均不能抵偿。无条件义务不能用 required=false 规避，条件辅助工作沿用本次 required 和跳过理由。无命令的参考应用以真实应用摘要和文档产物回流。

## 当前运行漏登记

文件已经存在、当前运行却未登记所需辅助能力时，按以下顺序处理。文件暂缺仍用原 missing_file 路径；两类限制分别核验。

1. **核授权**：从所属计划步骤的 capability_authorizations 取得已批准的辅助来源。每项包含 skill_id、绝对 skill_path、实际 sha256 和非空 authorization_reference；create/extend-plan 核对名称、内容及可调用策略并保存来源。授权引用由调用者依据真实用户证据提供，运行存储不鉴别引用背后的人类身份。漏登记处理不能即时扩大这份范围。
2. **记阻塞**：调用 block_unavailable(revision, skill_id, reason, step_id=..., category="registration_missing")；已运行父任务用 parent_frame_id。步骤须通过必需声明需要该能力，或父任务明确选中这项必要助手。返回有身份、归属、类别、原因与真实来源的局部限制，不创建执行帧；父任务选中的义务先保存，独立任务仍依原授权和容量就绪。
3. **明确修正**：以当前 revision 调用 register(revision, descriptor, restriction_id=...)，补入启用的 auxiliary 描述。修正再次核对原授权、路径、内容、策略和所选闭包；同名、禁用、已选版本或新的未授权来源均不能被替换。没有明确限制身份的普通登记不能绕过待修正项。
4. **重新判断**：修正只更新此 run 的 temporary_skills、选定来源与修正记录，解除已核验为相同授权来源的漏登记限制。其它限制、用户暂停、父义务、默认注册表和别的运行保持原状。暂停中允许这一步元数据修正，派发仍等待明确继续。
5. **真实回流**：通过同一个 preflight/dispatch 来源执行助手，返回实际应用及产物后再判断父任务完成。登记成功、文件存在或测试夹具都不等于专业职责完成。

CLI 对应 block --category registration_missing 与 register --restriction-id <限制身份>，均要求当前 --revision；其它参数见命令 --help。resume 不会用文件出现代替明确登记修正。登记/采用新来源另依授权处理，本接口不提供版本替换或旧运行自动迁移。
