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

旧 v1 原件继续可结构读取，不能自动补身份升级。v1 与 independent v2 都不能直接充当当前受管 checks。旧证据核验复用归 #11，下游评审引用及进一步候选失效传播归 #6；本票不宣称完成这些能力。既有记录文件哈希新鲜度仍有效，原始执行者和记录身份不能替换为引用者。
