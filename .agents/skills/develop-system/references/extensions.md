# 扩展接口

注册表 schema v2，controller_policy 固定 dispatch-only。未来能力在 registry.json 注册，或只在当前运行 temporary_skills 登记，不需要改写调度主体，也不要为未实现能力创建空目录。

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
