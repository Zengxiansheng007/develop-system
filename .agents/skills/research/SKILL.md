---
name: research
description: Investigate a question against high-trust primary sources and capture the findings as a Markdown file in the repo. Use when the user wants a topic researched, docs or API facts gathered, or reading legwork delegated to a background agent.
---

## Codex执行约定

按 [Codex统一执行协议](../develop-system/references/codex-runtime.md) 解析真实文件与使用实际工具。处于显式启动的 develop-system 流程内时，所有结果先回流总路由，辅助结果经检查后恢复父任务；流程外按本技能正文独立执行并交还调用方。

Execute the research using the unified Codex delegation protocol: delegate when authorized tools and capacity are available; otherwise investigate sequentially. A research worker performs the research itself and never spawns another research worker.

Its job:

1. Investigate the question against **primary sources** (official docs, source code, specs, first-party APIs), not a secondary write-up of them. Follow every claim back to the source that owns it.
2. Write the findings to a single Markdown file, citing each claim's source.
3. Save it where the repo already keeps such notes; match the existing convention, and if there is none, put it somewhere sensible and say where.
