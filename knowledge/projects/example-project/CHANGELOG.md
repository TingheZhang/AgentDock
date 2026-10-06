---
title: CHANGELOG - Double OOD
type: note
permalink: vcc-notes/projects/example-project/changelog
tags:
- example-project
- changelog
- history
---

# example-project 实验与变更日志 (CHANGELOG)

> 记录 example-project 算法开发、消融实验、论文写作等任务改动。
> 规约要求：每位 Agent 在分支合并入 main 或完成阶段性实验后，必须追加记录并提交至 memory 仓库。

## 记录规范模板

```markdown
### YYYY-MM-DD [Task-ID: 简述] by <Agent-ID>
- **背景/动机 (Context / Motivation)**: ...
- **核心改动 (What Changed)**: 脚本/代码修改、参数调整或数据处理细节。
- **实验与指标 (Metrics / Results)**: 准确率/指标对比（Baseline vs Current）、Artifact 输出路径。
- **关联提交 (Git Commit)**: Commit hash 与分支名（如 `agent/deepseek-t12`）。
```

---

## 历史变更记录

### 2026-10-06 [初始化项目 CHANGELOG 机制] by <AGENT_ID>
- **背景/动机 (Context / Motivation)**: 为多 Agent 协同实验建立统一的变更沉淀机制，配合 Backlog 任务流与 6422 看板知识面板展示。
- **核心改动 (What Changed)**: 创建 CHANGELOG 规范模板并在项目 AGENTS.md 契约中建立收工登记流程。
- **关联提交 (Git Commit)**: 初始化提交。
