# 旧运行的只读迁移预览（T08）

需要判断 v3 运行如何采用候选版本时，先生成预览，核对绑定与待核验清单。当前入口只返回说明；采用、回滚和旧结果兼容分别由 #10/#11 交付。

1. 选择实际候选的 `scripts/workflow_state.py`。调用 `RunStore(project, run_id).preview_migration(target_registry=...)`，或 CLI `preview-migration --entry develop-system --project-root <原项目> --run-id <原身份> [--registry <目标登记表>]`。默认目标登记表属于所加载候选。完成条件是返回源绑定与目标清单，不能以同名全局 Skill 替代。
2. 核对 `source` 的实际路径、run、revision、原始字节 SHA-256 和原契约版本；`target` 固定目标 state/registry/result/check 版本及实际文件清单/hash。原状态读取前后变化则拒绝。预览不创建锁、目录、帧或事件，也不修改原状态。
3. 阅读 `inventory.snapshot` 的完整原状态及目标、计划、帧/AC、授权、额度、事件、在途和证据位置视图。扩展字段、原接受状态、null 与历史计数原样保留；不存在的授权或额度仍待核验。清单不是另一份执行账本。
4. 阅读 `historical` 和 `mapping`。原 Skill、资源、引擎和检查只与原记录的绝对路径/hash 比较；`matches_recorded_bytes` 只说明当前字节匹配。没有原 hash、缺失或变更均不能补成历史版本；检查保留原记录、执行者及引用，复用资格始终待核验。原有 `engine_binding` / 帧 `engine_binding` 与 `resources` 若存在则展示，缺少时明确列缺口，不能事后写回旧状态制造历史依据。
5. 逐项处理 `pending` 后再讨论显式采用。缺失的帧协议、必需义务、工作树与环境核验不默认为通过。局部映射需要原步骤/父帧归属或明确范围；无法解释的整体停止保留未知限制，用户暂停和完成/取消终态保持原含义。预览中的映射仅供审阅，不能作为恢复或派发授权。

## 目标清单边界

清单涵盖所加载 develop-system 包、所选登记表，以及登记到相邻候选 Skill 包的正文与资源。目录别名或越出其包的资源拒绝；外置单文件来源只绑定该文件，其资源列待核验；缺失来源不会回退全局版本。清单不认证技能名称/专业结论、不证明旧运行与目标版本兼容，也不证明环境健康。后续采用前应重新核对准确清单与所有受影响绑定。

## 版本与验证边界

新运行仍创建 state v4 / registry v3 / result 1，检查记录使用 v2。v3 未显式迁移只能读取和预览，新语义写入拒绝；v1/v2 不自动续跑。历史 v3 引擎的公开状态操作拒绝 v4。

自动化验收使用隔离合成历史事实，以及来源固定在初始 Git 提交的真实 v3 引擎夹具；夹具出处和逐文件 hash 位于 `scripts/fixtures/legacy-v3/provenance.json`。这些检查不执行真实旧运行迁移，不把保存的历史 passed 转换为新版接受。
