# grill 入口与回流操作

直接输入 grill-with-docs、从受管流程分派 grill、恢复中断访谈时读取本页。工具只登记状态和决策，不运行访谈、代码、测试或评审。ask-matt 不调用这些操作。

## 入口

先保存 UTF-8 上下文 JSON，再用本次选中 develop-system 的 scripts/workflow_state.py 执行 inspect-grill。传 --entry develop-system、--project-root、--run-id（扫描占位也可，不会创建运行）、--payload；扫描限目标项目，操作只读。

上下文包含 project_root、goal、scope 字符串数组、input_version。可选 independent: true、run_id/frame_id、session_binding: {run_id, frame_id}。归属语义无法用完全相同的 goal/scope 表达时，matches: [{run_id, goal, scope, reason}] 记录代理检查候选目标与范围后的明确理由；不能凭关键词或最近使用就填入匹配证明。

- independent：按独立访谈执行，不 init。当前文档确认后列出 to-spec 和 implement、各自产物及推荐原因，停止等待明确选择。确认文档或“全部采用”不等于选择技能。
- managed：由调度器登记或复用帧，再让 grill 执行；不得直接写入结果或跳过专业父任务。
- clarify：说明具体归属或恢复缺口，等待真实答复；候选不写入。
- resume：先处理原等待/阻塞条件或过期证据，再调用既有 resume 并重新检查。paused 必须明确要求恢复；recovery_ready/resume_paused 只记录真实恢复依据，不由时间或工具默认填 true。

纯决策接口 grill_routing.decide_entry 接收上下文与只读候选；independent_finish 接收已保存的交付对象、当前版本确认和后续显式 choice。交付对象含 document、version、confirmation_reference、checklist；choice 只能在真实后续选择时填写 to-spec / implement。

## 受管帧接入

判定 managed 后，使用 attach-grill --revision <最新值> --payload <入口上下文>，按需传 --input 与 --skill-path。工具在排他锁内重新核验归属、revision、输入和技能路径，复用相同职责及版本的活动 grill 帧。唯一已登记的辅助 grill 可恢复其已记录父关系；多个活动 grill 需明确 frame_id。

补充正在进行的专业任务时，调度器在上下文中传 parent_frame_id（或绑定该专业 frame_id），再登记辅助帧。已有其他活动专业任务且未确定父关系时，工具拒绝接入，由调度器解释上下文归属；不能将它跳过或关闭。没有活动父任务时选择唯一就绪根 grill 步骤；若没有，先由调度器用 extend-plan 登记依赖合法的必要步骤，再 attach，不自动 init。

输入变化、中断、等待或阻塞沿既有 resume 重新核验，重新分派专业父任务后再登记辅助。paused 需要真实明确恢复；旧 schema 不转换。重复接入不新增帧，旧版本/旧 revision 不能覆盖新状态。

根文档变更后重新回流选路，会退役同一根步骤曾创建且已失效的路线分支，保留历史事件；已复用的外部产物仍属于原计划。旧分支有未决发现时先交相应专业技能处理，不能用选路抹去发现。

## 根访谈回流与选路

grill 的 completed 结果除统一字段外，提供 grill_delivery：document（实际文档路径或证据对象）、version、confirmed: true、confirmation_reference（真实用户答复来源）、core_questions_resolved: true、helpers_resolved: true、checklist（非空交付清单）。document 同时列入 artifacts；缺失完成依据不能返回 completed。waiting_user/blocked 仍沿用原契约。

direct_conditions 按 single_task、scope_clear、constraints_clear、verification_sufficient、single_session 五项记录 {satisfied: true/false, evidence: "专业判断依据"}。缺少任一肯定依据时，选规格及任务路线；文件数不作为判断。

调度器先用 return 接受结果，再 route-grill --revision <最新值> --event-id <已保存事件>。来源必须是当前已完成且没有未决发现的根 grill；辅助结果不执行根选路。grill_routes 记录来源事件/帧、输入和需求版本、文档证据、条件、实际技能及步骤、复用步骤。重复来源事件不重复写入。

新增必要步骤依赖根 grill 及前序技能。复用已有产物须存在已完成的对应专业帧、相同 scope 与 requirement_version、依赖完成及当前证据；没有元数据时重新执行必要阶段。依赖变化仍执行既有 resume 协议。

同版本、同范围的已登记 pending 步骤复用其标识，保留原依赖并追加根访谈和前序路线依赖；有效完成产物与待执行步骤分别核验。

## 验证边界

状态公开操作与落盘结果为主要测试入口，纯决策接口补充边界。临时项目中的模拟确认只验证协议，不代表真实用户确认。宿主没有原生完成钩子，代理仍须读取并遵守技能指令。
