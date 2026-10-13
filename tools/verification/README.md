# 校验器来源

`skill_format.py` 是本机 skill-creator 的 `scripts/quick_validate.py` 原样快照，来源路径 `skill-creator/scripts/quick_validate.py`，复制日期 2026-10-07。独立保存使 CI 不依赖开发者机器的全局技能目录。UTF-8 由统一入口的子进程环境指定，PyYAML 版本由 requirements-checks.txt 声明并检查。

升级校验规则时显式更新快照并重新验证，避免运行时从宿主隐式选取不同版本。

Independent repository adaptation (2026-10-13): validate all bundled skills. Accept existing upstream argument-hint (string) and disable-model-invocation (boolean) metadata with explicit type checks; retain invocation policies. This validator does not assert host support for these fields.
