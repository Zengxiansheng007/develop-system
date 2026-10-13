# 机器检查记录

本约定只约束执行命令产生的验证结论。专业技能解释输出、判断覆盖范围并给出验收结论；develop-system 只核对记录结构、一致性、归属及文件新鲜度。

## 执行与回流

专业执行帧使用所选 develop-system 的脚本，传 argv 参数、不经 shell 解释。输出路径必须是新的文件，不覆盖旧结果：

```powershell
python -B <所选技能目录>/scripts/record_check.py --output <新结果文件.json> --cwd <绝对项目目录> --timeout 300 -- python -B -m unittest discover -s <测试目录> -v
```

程序执行完才写入 schema_version、record_id、command 数组、cwd、时间、exit_code、stdout、stderr、error 与 status。只有退出码 0 且无执行异常才是 passed；超时、程序启动失败、非零退出码是 failed。退出码 0 只说明命令成功，是否覆盖需求由专业技能判断。长驻服务的启动不能当作此类完成检查。

回流示例：

```json
{"checks":[{"status":"passed","record":"绝对路径/实际结果.json"}]}
```

每项 checks 必须引用真实记录；可携带 command，但必须与记录一致。调度器拒绝空记录、状态与退出码矛盾、伪写 command 或失败记录支撑 passed 验收。记录文件哈希被保存到帧，后续内容变化会阻止路由/关闭，resume 时沿依赖失效。重复事件仍幂等。

失败结果如实回流，可 completed 表示诊断工作结束，但不能同时声明 passed 验收。预期失败测试用独立诊断记录/报告，最终验证帧引用实际通过的回归记录，不能把红灯改写成绿灯。

评审意见、文档检查等没有执行命令的专业判断存 artifacts 与 acceptance_checks；人工确认仍须真实 user_reference。记录是可追溯执行证据，并非防恶意篡改的签名证明，也不能替代专业语义判断。已有历史事件不重写；需要重新执行的旧命令检查须按本约定生成新记录再回流。

## 本工作区统一入口

本工作区的检查配置和 CI 见项目 tools/checks.py 及 docs/development/verification.md；其他项目自行提供检查命令，不把 <project-root> 的目录假定为全局运行环境。加载/登记结果不会由调度器启动测试。
