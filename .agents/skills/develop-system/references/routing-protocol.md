# 路由与回流协议

字段及职责以 [统一执行协议](codex-runtime.md) 为准。状态 schema v3，注册表 schema v2，均声明 controller_policy: dispatch-only。

## 分派

帧携带 run_id、frame_id、parent_frame_id、skill_id、真实 skill_path/hash、mode: skill、绝对项目目录、scope、输入版本、验收项编号、完成判据、授权和 return_to: develop-system。分派不是已经执行；读技能并切换专业角色或实际代理执行后才有结果。

计划中的 depends_on 表示任务依赖，注册表 requires 表示能力级依赖。default_flows 是按需裁剪的模板，辅助技能由工作缺口触发；已经有效完成的同版本测试/评审不重复运行。

## 返回处理

1. 核对归属、版本、结构和事件冲突，保存专业技能结果与证据引用。
2. 子帧先回流再恢复父技能，必要兄弟子帧未完成则收集结果；父任务未完成不能推进主阶段。
3. 根帧更新步骤，根据专业结论路由到下一项。next_hint 是建议，不绕过范围或依赖。
4. 发现需要修复时追加范围内专业步骤，resolves_frames 列出目标；实际解决结论必须由专业技能 resolved_findings 返回。assess 只登记来源。
5. 必要工作齐全后引用已返回的 acceptance_checks 登记关闭。总路由不运行测试、看代码评审或重新判断业务验收。

## 恢复与停止

- 上游输入、产物或验证证据变化，使后续依赖和验收失效；分派专业技能重做，不复用旧版本结论。
- 同技能、输入、阻塞两次无新证据则停止；新证据可显式恢复。
- 必要帧不能跳过；非必要子帧可带理由跳过。范围外建议不追加必做清单。
- 缺失技能阻塞，无法用 inline 或调度器自己完成替代。
- waiting_user 保存真实未答问题/未完成人工操作；blocked 保存依赖/环境限制；paused/cancelled 只依据用户明确要求。
- schema v1/v2 不自动迁移，专业技能核验后在 v3 新 run 登记结论并注明旧来源。
