# 机器检查记录 v2

专业技能选择相关范围、解释输出并给出验收结论；调度器只核对结构、当前授权关联、实际来源及内容一致性。命令成功与整票验收分别判断。

## 独立检查

原有 `run_record(argv, cwd, output, timeout=300)` 和 CLI 继续可用，无需建立 run。新输出为 schema_version: 2、context: independent，保存 command、execution_command、cwd、时间、stdout/stderr、exit_code、error 和 status。输出必须是新文件；退出 0 且没有执行/采集错误才是 passed。超时和启动错误如实保存。

`tools/checks.py` 默认产生独立 v2 记录，summary 的各检查列出 context。仓库检查通过不会自动成为某个运行的受管证明。独立记录可以作为明确说明用途的 artifact；新的 checks 回流要求受管契约。

## 派发授权与冻结

在既有任务授权内调用 `RunStore.dispatch(..., worktree=declaration)`；CLI 使用 `--worktree <JSON>`。声明示例：

```json
{"authorization_reference":"既有任务授权引用","worktree":"绝对工作树根目录","execution_roots":["明确允许的主仓库或工作树目录"],"source_ranges":["src","tests","config"]}
```

派发核对真实 Git 仓库、工作树，生成原 run/frame/input_version 对应的 worktree_binding，保存在原帧。同仓库、common-dir 或目录嵌套都不是授权；嵌套的其它 worktree 不能继承执行范围。source_ranges 是允许修改/取证范围，实施前的内容不会永久冻结。此约束不构成 OS 写入拦截，不新增或重置额度。

检查时专业技能提供候选 selection：

```json
{"ranges":[{"path":"src","role":"source"},{"path":"tests","role":"test"},{"path":"config","role":"config"}],"entry":"tests/check.py","loaded":["tests/check.py","src/value.py"]}
```

ranges 必须在授权范围内；role 是 source/test/config/dependency。目录枚举其相关现存文件，包含 dirty/new/untracked；文件项可缩小已确认相关范围。配置及必要项目依赖必须明确选择，缺声明不表示没有依赖。loaded 为必须真实执行到的源码；entry 为必须观察到的项目入口，与 argv 的解释器或 unittest launcher 分别记录。HEAD 不替代实际内容。

只采集明确范围的文件名、角色和指纹，不复制内容或完整环境。每个文件在读取指纹前解析实际目标，核对派发时固定的 source_ranges；目录联接或符号链接不能扩大范围，授权目录被替换也不能移动授权。原路径和实际目标中的 .git、.env、私钥及常见凭据名称均拒绝，Python 观察器也复用此读取前排除规则；专业技能仍须排除其它命名的敏感材料和无关文件。忽略缓存字节码。执行前后实际枚举并指纹核对，漂移保存为失败，不能支持当前候选通过。这是采集器校验，不是目标进程的文件访问沙箱或并发文件替换隔离。

## 真实加载观察

`run_record(..., binding=frame["worktree_binding"], candidate=selection)` 使用同一执行入口；CLI 同时传 `--binding <绑定JSON> --candidate <候选JSON>`。传 argv、不经 shell，cwd 可为绑定的工作树或明确允许的主仓库。

本版受管观察器支持 Python 脚本路径和 `-m module`，允许 `-B`、`-u`、`-X utf8`。实际目标在 Python 子进程执行，采集授权范围内真正执行到的代码路径及当时指纹；必须包含 entry/loaded，不能遗漏已加载的相关源文件。主仓库 cwd 误导入 canonical 副本时，即使命令退出 0，也不能通过候选检查。记录分别保存请求 command 与实际观察器 execution_command。

受支持的解释器开关实际传到子解释器，不能仅从目标 argv 删除。观察器隔离字节码查找并禁止写入，避免旧 .pyc 被标成当前源码指纹。其它可执行程序以及 Python 的 `-c`、stdin、其它开关在受管模式启动前明确失败；仍可用 independent 模式。没有为任意运行时、其子进程或未选系统库提供完整加载证明；专业技能须选适用入口。文件指纹及观察器结果不是签名、隔离或完整语义验收。

## 回流与兼容

checks 引用 `{"status":"passed","record":"绝对记录路径"}`。接收端要求 managed v2 与当前原始帧绑定完全匹配，核对仓库/工作树、cwd、当前候选、执行前后内容及加载观察。错误 run/frame/输入版本/工作树/候选、伪造状态或 command 均拒绝，权威状态不变。暂停回传照常核对并只保存；重复事件仍幂等。

非零退出如实记录 failed，可以表示专业诊断职责完成，但不能支持 passed 验收。来源采集不一致时保留真实退出码及输出，用 error 标记失败；CLI 返回非零，即使命令本身退出 0。保留所有红灯，最终验证引用实际绿灯。

## 当前证据资格与评审引用

记录的历史状态保持原样。派发、回流、完成、专业结论消费和 resume 还会核对受测候选及当前绑定，不能仅检查 JSON 文件未变。专业 selection 中的源码、测试、配置和共享依赖决定关联范围；已确认无关文件不触发重新验证。resume 使受影响执行者、引用者和真实下游失效，保留独立有效结果、原事件和原记录；父任务完成资格继续遵守局部作用范围规则。没有自动重跑或额度重置。

一次 resume 会完成计划依赖、后代和检查引用之间的失效传播，包括来源因上游变化在本次恢复中才失效的情况。引用者的真实下游同样受影响；独立已完成及在途工作保持原资格，独立派发仍服从原授权和容量。无需再次 resume 才处理同一变化。

code-review 帧可在已有授权的同一工作树范围内引用当前运行中已接收的原执行记录。仍使用 checks，增加明确 reference：

```json
{"status":"passed","record":"原记录绝对路径","reference":{"run_id":"原运行","frame_id":"原执行帧","record_id":"原记录身份","candidate_id":"原受测候选身份"}}
```

原执行帧须当前 completed；引用必须匹配它接收的直接检查、原文件及指纹、身份和当前候选。评审自己的绑定须覆盖同一候选。错误或失效来源、失败记录、其它工作树和跨运行引用拒绝，状态不变；引用必须直达执行来源，不能把另一份引用伪装成新执行。记录的 binding、command、cwd、时间及执行者保持原样；评审只拥有自己的专业结论。有效暂停回传可保存，重复事件保持幂等，保存不关闭验收。

新绑定同时保存实际工作树根目录与 Git 元数据目录的文件系统对象身份。搬迁、同路径替换、仓库身份失效要求重新核对受影响绑定，并针对必要环境执行新检查；同内容不能单独继承资格。旧绑定或旧记录不会自动改写。对象身份不是通用环境健康证明；其它运行时前提仍由专业技能核验并声明。此校验也不提供并发替换隔离。

旧 v1 原件继续可结构读取，不能自动补身份升级。v1 与 independent v2 都不能直接充当新的受管 checks。明确迁移清单中的原执行帧按[旧帧兼容与证据核验](legacy-compatibility.md)追加有来源的专业复用结论；资料不足仅保留待核验与受影响范围，不回填原记录或伪称重跑。当前引用能力不授权真实迁移；原始执行者和记录身份不能替换为核验者。
