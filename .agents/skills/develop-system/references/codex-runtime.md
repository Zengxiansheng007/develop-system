# Codex统一执行协议 v3

本协议的帧/状态/强制回流仅在显式 develop-system 流程内启用。流程外技能返回调用方。ask-matt 纯导航，不建帧、不分派、不写状态。

## 加载与专业执行

1. 用户显式路径优先，其次会话技能目录；否则查项目 .agents/skills、.codex/skills、用户 .agents/skills、.codex/skills。读取实际 SKILL.md，记录名称、绝对路径、hash，资源相对技能目录解析。
2. 同名只选一个版本，保留 agents/openai.yaml 调用策略。用户已启动受管流程后可以在范围内显式读取所选技能；正文的 Skill 工具调用映射为实际加载，不假定存在同名宿主工具。
3. 总路由把目标、范围、规格、验收项编号、输入版本、父子帧标识、职责、授权与本协议绝对路径传给专业技能。嵌套技能也登记帧；专业参考载入并应用后以辅助结果回流。
4. 执行由所选专业技能承担。无子代理条件时同一代理先切换到专业帧，做完回到调度角色。总路由只管理流程，不代做业务实现、测试、集成、评审或验收；缺失技能阻塞，禁止 inline 代做。

## 状态与委派

状态保存在目标项目 .develop-system/runs/<run-id>/state.json，不同目标分别记录。schema v3、controller_policy: dispatch-only。只有总路由角色写状态，工作代理返回独立结果；排他锁、revision 与原子替换避免覆盖。

- 真实委派须有用户或适用技能的明确指令、工具、槽位与独立任务；仅编辑这些文件不启动代理。并行修改需不相交文件或已授权隔离工作区。
- 有 collaboration 工具时直接调用 spawn_agent/send_message/wait_agent，不能放进 functions.exec。总路由统一分派；专业协调技能提供工作请求，工作代理不递归扩大团队。
- 条件不足时专业角色顺序完成，execution 如实为 sequential；有实际代理才用 delegated。不得创建用户可见新 chat 冒充子代理，不虚构 Git worktree。
- 分派说明携带 run/frame/parent、技能路径、输入、职责、授权和结果位置。后台服务采用当前平台规则，不让前台长进程阻塞调度。

## 结果与专业结论

每次执行结束先回流 develop-system：

```json
{
  "event_id": "unique-result-id",
  "run_id": "current-run",
  "frame_id": "current-frame",
  "parent_frame_id": null,
  "skill_id": "code-review",
  "input_version": "implementation-v1",
  "mode": "skill",
  "execution": "sequential",
  "status": "completed",
  "return_to": "develop-system",
  "completion_checked": true,
  "artifacts": [],
  "checks": [],
  "findings": [],
  "blockers": [],
  "acceptance_checks": [],
  "resolved_findings": [],
  "next_hint": null
}
```

status 为 completed/failed/blocked/waiting_user/skipped/cancelled。completion_checked 是**执行技能**检查本帧职责后的声明，不是调度器验收。completed 表示职责完成，评审完成仍可能有未解决发现。artifacts 是实际文件路径/含 path 对象；checks 记录实际命令、结果及证据，未执行不声称通过。

acceptance_checks 由本技能对有能力验证的 run 验收项返回，格式：`{"id":"1","status":"passed","completion_checked":true,"kind":"technical","evidence":["真实报告路径"]}`。人工项 kind 为 human，同时须 human_confirmed: true 与非空 user_reference（真实答复/操作来源）；生成向导不构成用户确认。失败、未验证和待确认项不能作为关闭依据。

resolved_findings 为专业技能确认已处理的旧 frame_id 数组；结论附真实证据。调度器的 assess 操作只登记这个来源，不自行生成处理结论。

## 回流与衔接

1. 总路由核对 run/frame/parent/input_version/mode、事件去重、文件存在和 hash；这些是协议与新鲜度检查，不能证明业务正确。
2. 保存专业结果。必要子帧未结束或有未决发现时不能完成父帧。辅助返回后恢复父技能；根帧返回后选择下一必要工作。
3. 有明确缺陷分派相应专业技能，关闭发现须引用其已返回的 resolved_findings。缺少专业验收结论时分派对应验证/评审技能，不能补写成功断言。
4. 全部必要工作、发现和验收项满足后，complete 只引用 source_frame_id 对应事件内的验收结论。人工项等真实用户答复；纯行政关闭不执行业务验收。
5. 同技能/输入/阻塞两次无进展停止重复。上游变化使依赖结果失效，重新分派专业技能；hash 一致不证明运行环境健康。

## 宿主边界

授权范围不自动包含部署、远程写入或消息。`/skill` 文本是技能意图，不是 shell 命令，不断言宿主原生菜单注册。会话压缩由宿主处理，不用 /clear 假装新上下文；不建立后台唤醒。

Windows 使用 PowerShell 原生命令，人工向导匹配实际 Bash/WSL/PowerShell 环境。打开文件不表示执行或验证完成。

状态工具与迁移详见 [状态操作](state-operations.md)，扩展见 [扩展接口](extensions.md)。旧 schema v1/v2 拒绝自动续跑：对应专业技能先核验旧证据，在 v3 新 run 中返回来源明确的新结论。

## 命令检查证据

回流包含命令检查时，checks 格式与判定规则见 [机器检查记录](check-records.md)。
