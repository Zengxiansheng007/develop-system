---
name: ask-matt
description: "Matt 技能流程导航员。根据当前情况推荐技能、工作路线与上下文衔接方式，只提供导航建议。"
license: MIT
---

# Ask Matt

本技能是 `/ask-matt` 与 `$ask-matt` 的纯导航入口。读取同级 develop-system 的 [导航地图](../develop-system/references/navigation.md)，结合用户的目标、已有产物与当前阶段，推荐适合的技能和路线。

回答说明首选技能、适用原因、必要前提、可选分支与后续候选；已有清晰规格或任务时复用，不强迫从需求访谈开始。需要时读取 [阶段边界](../develop-system/PHASE-BOUNDARIES.md) 给出继续当前会话或交接的建议。

以建议结束：不加载并执行被推荐技能、不自动切换至 develop-system 执行流程、不创建或修改流程状态，也不要求技能回流。运行中的执行流程不会因本次导航咨询推进。实际执行须由用户另行启动执行入口或直接请求相应技能。

技能地图不代表本机已安装。用户询问可用性时核对真实目录；无法找到技能则说明缺失。Codex 的命令展示以宿主实际支持为准，`$ask-matt` 是显式技能调用形式；不要把 `/ask-matt` 当作 shell 命令。
