---
title: 多 agent 改平台作业规约
type: note
permalink: vcc-notes/projects/platform/多agent改平台作业规约
tags:
- vcc
- platform
- multi-agent
- 规约
---

# 多 agent 改平台作业规约

**面向：准备动手改这个平台的 agent。** 先读本页和[[平台运维铁律]]，再动代码。

## 单一事实源

| 想知道 | 看哪里 |
|---|---|
| 有哪些项目、端口、知识归属 | `$PLATFORM_ROOT/platform/ports.json` |
| 怎么启停 | `bb-ports.sh`（读同一份 ports.json） |
| 聚合页显示什么 | `bb-aggregate.py`（读同一份） |
| 知识面板归属 | `ports.json` 的 `knowledge` 块 |

**`ports.json` 被三方同读，所以看板不可能与现实漂移。**
加项目只改这一个文件。

## 禁止手改的东西

| 路径 | 原因 |
|---|---|
| `platform/www/index.html` | **生成物**，手改在磁盘检查里能过、线上纹丝不动 |
| `~/.config/systemd/user/vcc-*` | 与仓库副本脱同步 |
| `state/**` | 运行时产物，会被重新生成 |
| `memory/**`、`wiki/**` | 研究内容，不是平台代码 |

`www/index.html` 一律用 `bb-ports.sh refresh` 重生成。

## 改完必须做的验收

**打真实端口，不只读磁盘文件。** 曾发生过：写对了
`platform/index.html`，而服务读的是 `www/index.html` —— 断言全绿、线上毫无变化。

最小验收清单：

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:<PORT>/   # 期望 200
curl -s http://<LAN_IP>:<PORT>/ | grep -c panel             # 面板项目期望 >0
python3 scripts/health_check.py --json --deep                      # 期望 ok=true
```

**每条新断言配一个负控**：把它指向错误前提跑一次，必须报 FAIL 才算有效验证。
恒真断言（`... or True`）混进全绿结果里，比没写还坏。

## 断言失败时先怀疑断言

不是先改产品代码。判据：**先 `curl` / `grep` 看真实产物，再决定改哪边。**
历史上多次是断言本身写错（模板占位符本该消失、聚合页不该给自己加链接）。

## 任务状态不等于完成

看板上的 Done 只是**声明**。
`git merge` 只证明代码进了分支，**不证明实验成功**。
验收记录必须含：task ID、run ID、artifact 路径、version/commit。

> 共享 Unix 账号无法强制 review 约定 —— 这是当前架构的已知缺口，
> 靠自律和记录补，不要假装已被机制保证。

## 并发安全

- 平台仓库在 `$PLATFORM_ROOT/platform`，该目录在 **ext4**（`/dev/sdb`）上。
  `/mnt/zhangth/projects` 是指向它的**软链**，本身也落在 ext4，**不是 NFS 路径**；
  真正的 NFS 是它的**上级目录** `/mnt/zhangth`（nfs4，`<LAN_IP_2>:/Backup`）。
  仓库**用 worktree 隔离**：共用一个工作目录时，一个 agent 切分支会改变别人
  看到的文件。worktree 在这里是为了**并发隔离**，与 NFS 性能无关。
  核对方式：`df -Th /mnt/zhangth /mnt/zhangth/projects $PLATFORM_ROOT/platform`
  —— 第二、三行应显示 ext4。
- 笔记在 `$PLATFORM_ROOT/memory`，**入 git 纳管**（source of truth）。
  改完立刻提交，否则回滚或clean checkout 会丢内容。
- 生成型产物**一律走生成入口**，不要手写 `--out`。

## 任务收工必须追加 CHANGELOG

每个 agent 在完成任务、合并代码或执行重大改动后，**必须**在所属项目的笔记目录更新 `CHANGELOG.md`：
- 平台改动：`$PLATFORM_ROOT/memory/projects/platform/CHANGELOG.md`
- example-project 改动：`$PLATFORM_ROOT/memory/projects/example-project/CHANGELOG.md`
- 写作侧改动：`$PLATFORM_ROOT/memory/projects/writing-side/CHANGELOG.md`
- 其他项目类似：`$PLATFORM_ROOT/memory/projects/<项目名>/CHANGELOG.md`

### 记录格式要求
按统一的 Markdown 规范追加条目，包含：
1. **日期与任务标识**：`### YYYY-MM-DD [Task-ID / 任务简述] by <Agent-ID>`
2. **背景与原因 (Context / Why)**：为什么做此改动。
3. **改动清单 (What Changed)**：改动的文件、模块或配置项，写明关键逻辑。
4. **验证结果 (Verification)**：实测命令与结果（例如 HTTP 200、测试通过日志、消融指标等）。
5. **关联提交 (Git Commit)**：关联的 Commit hash 与仓库名。

> **提示**：`CHANGELOG.md` 位于各项目 `memory` 目录下，会被 6424 知识库及各项目看板底部的知识面板自动索引呈现，所有人与后续 Agent 都能直接在 Web 端查看改动历史。更新后必须在 `memory` 仓库执行 `git commit` 提交！
