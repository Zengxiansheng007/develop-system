# 旧运行的迁移预览、采用与恢复（T08–T09）

需要判断 v3 运行如何采用候选版本时，先生成只读预览，核对绑定与待核验清单。#10 提供明确采用与恢复；旧帧继续执行和旧证据资格仍由 #11 交付。真实运行采用需要单独的实际授权。

1. 选择实际候选的 `scripts/workflow_state.py`。调用 `RunStore(project, run_id).preview_migration(target_registry=...)`，或 CLI `preview-migration --entry develop-system --project-root <原项目> --run-id <原身份> [--registry <目标登记表>]`。默认目标登记表属于所加载候选。完成条件是返回源绑定与目标清单，不能以同名全局 Skill 替代。
2. 核对 `source` 的实际路径、run、revision、原始字节 SHA-256 和原契约版本；`target` 固定目标 state/registry/result/check 版本及实际文件清单/hash。原状态读取前后变化则拒绝。预览不创建锁、目录、帧或事件，也不修改原状态。
3. 阅读 `inventory.snapshot` 的完整原状态及目标、计划、帧/AC、授权、额度、事件、在途和证据位置视图。扩展字段、原接受状态、null 与历史计数原样保留；不存在的授权或额度仍待核验。清单不是另一份执行账本。
4. 阅读 `historical` 和 `mapping`。原 Skill、资源、引擎和检查只与原记录的绝对路径/hash 比较；`matches_recorded_bytes` 只说明当前字节匹配。没有原 hash、缺失或变更均不能补成历史版本；检查保留原记录、执行者及引用，复用资格始终待核验。原有 `engine_binding` / 帧 `engine_binding` 与 `resources` 若存在则展示，缺少时明确列缺口，不能事后写回旧状态制造历史依据。
5. 逐项处理 `pending` 后再讨论显式采用。缺失的帧协议、必需义务、工作树与环境核验不默认为通过。局部映射需要原步骤/父帧归属或明确范围；无法解释的整体停止保留未知限制，用户暂停和完成/取消终态保持原含义。预览中的映射仅供审阅，不能作为恢复或派发授权。

## 原事件与当前限制

核对限制时，同时查看当前帧 `basis` 和匹配原执行身份的结果 `event_basis`。v3 将 findings/blockers 保存在结果事件中；即使帧为 completed 或恢复后变为 stale，只要 `needs_assessment` 仍为 true，预览就保留该未决限制。明确且一致的原范围可用于映射；范围缺失或冲突列为 unknown，缺少匹配事件另列原始依据待核验。

专业修复结果返回后，旧引擎还需通过 assess 才清除原帧的待判断标志。已清除标志的完成帧和已 superseded 帧仅保留历史事实，预览不会因原事件仍有 findings/blockers 而重新激活限制；原事件和完整 snapshot 均保持原样。

## 目标清单边界

清单涵盖所加载 develop-system 包、所选登记表，以及登记到相邻候选 Skill 包的正文与资源。目录别名或越出其包的资源拒绝；外置单文件来源只绑定该文件，其资源列待核验；缺失来源不会回退全局版本。清单不认证技能名称/专业结论、不证明旧运行与目标版本兼容，也不证明环境健康。后续采用前应重新核对准确清单与所有受影响绑定。

## 版本与验证边界

新运行仍创建 state v4 / registry v3 / result 1，检查记录使用 v2。v3 未显式迁移只能读取和预览，新语义写入拒绝；v1/v2 不自动续跑。历史 v3 引擎的公开状态操作拒绝 v4。

自动化验收使用隔离合成历史事实，以及来源固定在初始 Git 提交的真实 v3 引擎夹具；夹具出处和逐文件 hash 位于 `scripts/fixtures/legacy-v3/provenance.json`。这些检查不执行真实旧运行迁移，不把保存的历史 passed 转换为新版接受。

## 明确采用

1. 取得单运行采用授权和原版本专业依据。先用普通预览取得 source/target hash，再向 `preview_migration(..., adoption=...)` 提供下列对象。reference/basis 均为已有真实文件的绝对 path 与 sha256，不能以事后当前全局文件冒充历史。authorization 保留人类授权来源，historical.basis 说明所列原引擎及实际 Skill/资源闭包的专业依据；工具核对绑定与字节，不代作授权或历史适用性判断。

```json
{
  "authorization": {
    "run_id": "原运行 ID",
    "source_sha256": "普通预览的 source.sha256",
    "target_sha256": "普通预览的 target.sha256",
    "reference": {"path": "授权依据绝对路径", "sha256": "原依据 hash"}
  },
  "historical": {
    "engine": {"path": "原引擎绝对路径", "sha256": "原版本 hash"},
    "files": [{"path": "每个原引擎依赖、原 registry、实际 Skill/资源绝对路径", "sha256": "原版本 hash"}],
    "basis": {"path": "原版本专业依据绝对路径", "sha256": "原依据 hash"}
  }
}
```

2. 审阅新的完整预览，调用 `apply_migration(preview)`。CLI 为 `preview-migration --adoption <上述 JSON>`，随后 `apply-migration --revision <原 revision> --payload <完整预览 JSON>`。缺授权、原依据不匹配、源 revision/字节或精确目标变化时，保留原状态并停止；重新核对后生成新的明确预览。
3. 当前中间候选支持无旧帧的运行，以及无 running 帧、仅保留历史的 completed/cancelled 终态。非终态已有旧帧需要继续判定其协议、义务或证据资格；这些情形与全部在途旧帧均在写入前拒绝，等待 #11。旧快照若已有 control/restrictions/selected_skills/migration 同名扩展，先明确原含义与映射；工具拒绝覆盖。不得填空义务、删除帧或抹去检查来绕过。终态历史帧/事件/接受状态原样保存，不因此获得新版完成资格。
4. 调用持既有运行锁完成复核、原字节和版本保存、暂存验证及原子替换；这段切换窗口阻止新派发，不修改用户暂停。局部限制保留所属工作，未知限制保持保守停止，原暂停和终态继续有效。run/项目/票/计划/帧/AC/事件、授权、未决扩展字段和额度保留，unknown 不补零；原 registry 快照与恢复材料继续可查。
5. 成功返回 state、recovery 与 receipt（恢复清单 path/hash）。核对权威 state 与返回结果；恢复目录只有非运行格式清单和 `.bin` 原字节，不是另一个可续跑 state.json。提交前失败保持原权威字节；可能留下不可执行的准备材料。若错误明确表示已经提交但复读异常，先核对权威文件和清单，不自动重试或覆盖恢复。

## 不覆盖新事实的恢复

调用 `rollback_migration(adopted_revision, receipt)`，或 `rollback-migration --revision <采用后 revision> --payload <receipt 的 path/hash JSON>`。恢复清单的 path/hash 已固定在权威状态中，调用方不能换清单重声明采用点。工具在同一锁内核对原采用 revision、规范状态字节及完整载荷摘要（仅清单互引引用单独核对），以及源身份、目标文件版本和原版本材料。任何新结果、派发、用户控制或其它字节变化均拒绝覆盖；事件数量相同不等于可以恢复。

通过后恢复完整原始 v3 字节并返回匹配的原引擎绑定，原暂停也随原字节恢复。旧 Skill/资源原路径必须仍与保留版本一致；文件恢复与状态恢复分别授权，不自动覆盖全局文件。恢复前写入不可执行的条件恢复意图记录，调用成功及权威字节复核才说明实际完成；失败记录本身不冒充恢复成功。
