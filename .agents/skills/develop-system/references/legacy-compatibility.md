# 旧帧兼容接收与历史证据核验（T10）

本协议延续[明确采用](migration-preview.md)的授权、精确版本、锁和原字节恢复材料。它支持来源清楚的在途 v3 帧和已接受历史；不执行模型、重启工作、覆盖全局文件或迁移真实运行。普通新帧仍按回流代次 1 验证，缺字段不能请求降级。

## 显式旧契约清单

在 adoption 对象中增加以下声明，重新生成并审阅完整 preview。frame_ids 必须准确列出原快照的全部帧，包括保留但不能再接收的已替代帧；basis 是专业核对原契约与版本适用性的真实文件引用。

```json
{"compatibility":{"contract":"state-v3","frame_ids":["原帧 ID"],"basis":{"path":"原契约依据绝对路径","sha256":"依据 hash"}}}
```

只支持已明确的 state-v3 原契约；不根据事件缺少 protocol_version 猜测来源。采用时保存原帧身份、原事件身份清单及原引擎/Skill/资源版本，不给旧帧补造 protocol_version 或空 completion_obligations。缺少明确兼容清单的继续执行请求仍拒绝；有清单的有效在途工作继续保留，同一锁仅暂缓切换期间派发。

## 接收与待核验

使用原来的 `RunStore.receive(revision, original_payload)` 或 CLI `return --payload`。只有权威迁移清单中的旧身份进入兼容路径。原 v3 的 run/frame/parent/skill/input_version、执行方式、状态、数组、文件与检查记录契约仍须成立；错误身份、失效/已替代帧、冲突事件及伪称状态拒绝且不改权威字节。旧回传不得声明新的协议代次。

有效原 payload 原样保存到同一权威状态的兼容 receipts，并分别保存规范文件引用。未核验结果标为 pending；它不产生新版通过资格、父恢复或整票完成。已返回工作不再占执行容量，独立任务可在原授权和其他限制内继续，依赖该结果的完成资格仍待核验。相同 payload 重复接收不增加 revision；已有历史事件按原 v3 文件引用规范化后比较，核验前后都保持幂等。旧、新路径共用事件身份，不能互相占用 ID。已有历史事件保持原样，核验不重投递或复制成第二个执行事件。

用户暂停时仍可保存有效回传和专业报告；保持用户控制及所有系统限制。明确继续之前不应用核验、不派发助手、不关闭验收。保存回传、核验和新的派发都是采用后的新事实，原无新事实回滚将拒绝覆盖。

## 专业核验

由已有授权内的专业帧实际核对原执行身份、相关运行环境与候选、原 Skill 正文及完成要求。运行中的旧父帧可以派发真正的专业子帧；已结束历史可通过范围内的新专业步骤核验。来源必须实际回流、当前 completed、无未决发现，所引用依据必须是该来源的真实 artifacts。调度器不能填一份自报结论替代此职责。

专业结果增加 legacy_verifications 数组，每项只描述一个原执行身份。示例（所有 path/hash 需替换为实际引用）：

```json
{"legacy_verifications":[{
  "frame_id":"原执行帧", "input_version":"原输入版本", "source_sha256":"migration.source.sha256",
  "identity":{"status":"verified","evidence":[{"path":"专业核验报告","sha256":"报告 hash"}]},
  "obligations":{"required_calls":[],"evidence":[{"path":"专业核验报告","sha256":"报告 hash"}]},
  "checks":[]
}]}
```

identity.status 为 verified 或 insufficient。required_calls 的 null 表示尚不明确；明确数组是针对这项原工作、阅读原依据后作出的适用性结论，不是缺字段的默认值。每个实际必需调用使用 `{skill_id, skill_path, skill_sha256}`，必须收到对应父帧下该来源的必要助手有效回流。旧子帧自己的资格未明确时也不能越过它完成父任务；另一票、另一父帧或过期助手不能顶替。

报告接收后调用 `RunStore.qualify_legacy(revision, original_frame_id, professional_source_frame_id)`，或 CLI `qualify-legacy --frame-id <原帧> --source-frame-id <专业帧> --revision <当前 revision>`。该操作持普通运行锁，追加核验事实，复用统一回流和完成校验。应用前按首次留存的规范引用核对 artifacts、检查记录、AC 证据与 grill 文档的原 hash；内容变化则拒绝且不改状态，不能按原 payload 的路径重新取 hash 来批准新内容。统一回流消费首次绑定的引用，原 payload 与留存引用都不回填。身份不足或调用要求未知保存为 pending；有充分依据才将已留存结果用于当前资格。重复消费同一专业结论幂等。所有这些原引用、专业报告或来源随后失效时资格随之失效，原事件和记录不改写。

## v1 检查复用

每个原 checks 项都要有一个匹配原 record path/hash 与 record_id 的专业结论。reuse 要求原命令真实 passed，并提供当时冻结资料、实际加载资料和当前匹配候选；failed 不能支持通过。示例放入上方 checks 数组：

```json
{"record":{"path":"原 v1 JSON","sha256":"原记录 hash"},"record_id":"原记录 ID",
 "decision":"reuse","evidence":[{"path":"专业核验报告","sha256":"报告 hash"}],
 "frozen":{"path":"当时冻结清单","sha256":"原清单 hash"},
 "loaded":{"path":"当时加载观察","sha256":"原观察 hash"},
 "candidate":[{"path":"相关源码绝对路径","sha256":"当时内容 hash"}]}
```

冻结与加载资料采用 `{"files":[{"path":"绝对路径","sha256":"原 hash"}]}` 清单。冻结资料必须已经属于原帧 inputs/artifacts；加载资料必须属于原历史 artifacts 或此次旧回传，不能使用核验者事后创建的同内容新文件替换。candidate 与完整冻结清单一致，加载清单非空且包含于其中；所有相关内容当前仍匹配。专业技能还必须核对观察确实属于原命令和适用环境；清单格式/hash 不是任意运行时的执行证明、签名或环境健康证明。

资料缺少时用 decision: reverify，或在明确指出缺口的 reuse 结论中保留缺失项。工具追加只指向该原帧和原记录的重新验证需求，保留 pending，不自动重跑或扩大额度。没有当时资料的 v1 不会升级为 v2。后续实际重新验证需要已有范围内的专业任务与正常新检查，不能伪称旧命令已重跑或回填旧采集字段。

复用保持原 command/cwd/时间/record_id/状态、原始执行者及原 payload；专业核验者只拥有新增结论。无关内容不触发重验；相关候选或核验来源变化通过既有 resume 规则处理，独立有效结果保留。直接新的 checks 和现代评审引用仍遵循[机器检查记录](check-records.md)，不能借此让独立记录冒充新的受管执行。
