# 想法实现核查报告

> 核查时间：2026-10-05 11:55
> 核查方式：**逐项实测**，不凭记忆。所有结论都有命令输出支撑。

---

## 结论速览

| # | 你的要求 | 状态 | 一句话 |
|---|---|---|---|
| 1 | Backlog.md 管理和可视化任务 | ✅ 已完成 | 看板原本读错目录，2026-10-05 已修（ports.json path → 本地盘工作副本），实测通过 |
| 2 | Basic Memory 管理记忆 | ✅ **已实现** | 0.23.2 装好，MCP 服务 8765，语义搜索实测命中 |
| 3 | codebase-memory-mcp 管理记忆 | ✅ **已实现** | 0.11.0 portable 版，索引 83 节点/82 边 |
| 4 | TencentDB 只要 wiki 知识层 | ❌ **未实现** | 从未安装，且**有 3 个硬障碍**，需你决策 |

**总结：4 项里 2 项完全实现，1 项有实质缺陷，1 项未做且有前置障碍。**

---

## 第 1 项：Backlog.md ⚠️ 有实质缺陷

### 已实现的部分

```
NAME               GROUP     PORT      STATE   HTTP
vcc-example-project     <STUDY_GROUP>  6422      up      200
writing-side       写作侧 6420      up      200
(dashboard)        -         6421      up      200
```

- 三个服务全 up，HTTP 全 200
- 聚合看板（6421）项目列表 + 跳转正常
- 写入侧（6420）5 个任务卡正常显示
- **CLI 完全可用**——实测在 VCC 项目创建任务成功：
  ```
  File: .../.backlog/tasks/task-1 - 平台自检：确认任务可创建.md
  Task TASK-1 / Status: ○ To Do
  ```

### ✅ 已修的严重缺陷：两套 `.backlog`，看板读的是空的那套

`ports.json` 里 VCC 项目的 `path` 指向 **NFS 上的真实数据目录**：

```json
{ "name": "vcc-example-project",
  "path": "$NFS_DATA",   ← 看板读这里
  "ui_port": 6422 }
```

但 git 工作副本在本地盘：**`$PROJECT_DIR/example-project`**
两个位置各有一套 `.backlog/`：

| 位置 | 是否被看板读取 | 任务数 |
|---|---|---|
| `$NFS_DATA` | ✅ **是** | 0 |
| `$PROJECT_DIR/example-project/.backlog/` | ❌ 否 | 0（我建了 1 个测试后已清理） |

**实测证据**：

```bash
# 6422 的进程 cwd —— 指向 NFS
PID 353438 cwd: $NFS_DATA
cmd: backlog browser --port 6422 --no-open --non-interactive

# 在本地盘建任务后，看板 API 返回空
$ curl http://127.0.0.1:6422/api/tasks
[]

# 重启服务后仍然是空 → 不是缓存问题
$ bb-ports.sh restart vcc-example-project   # 新 PID 353438
$ curl http://127.0.0.1:6422/api/tasks
[]
```

### 这意味着什么

**agent 在 git 工作副本里建的任务，看板上看不到。**
而 agent 平时工作的地方正是 `$PROJECT_DIR/example-project`（因为 NFS 上 git 慢 100~700 倍）。
所以「看板可视化任务」这个核心功能，**在真实项目上等于没有生效**。

### 修复方案（需你选）

| 方案 | 做法 | 代价 |
|---|---|---|
| **A（推荐）** | `ports.json` 的 `path` 改成 `$PROJECT_DIR/example-project` | 一行配置。但 NFS 那套 `.backlog` 就成了孤儿，需决定删还是同步 |
| **B** | 保留 NFS 路径，把 `.backlog` 做成软链指向本地盘 | 保持「数据在 NFS、配置在本地」的分层，但多一层间接 |
| **C** | 两套都建任务 | 一定会分叉，不可行 |

我倾向 **A**：git 工作副本是 agent 真正工作的地方，看板应该跟着它走。

---

## 第 2 项：Basic Memory ✅ 完全实现

| 指标 | 实测值 |
|---|---|
| 版本 | 0.23.2 |
| Python | 3.12.15（conda 环境 `basic-memory`） |
| MCP 端口 | 8765，监听 `0.0.0.0` |
| LAN 连通 | 从本地 Windows 直连 **200 / 20ms** |
| 工具数 | 21 个 |
| 语义搜索 | 查「嵌套 CV」→ score **1.199**；查「git」→ **1.242** |
| 笔记 | `$PLATFORM_ROOT/memory/` 2 篇，Markdown 即 source of truth |

**踩过的坑（已在配置文档里标注）**：
- 语义索引必须带 `HF_ENDPOINT=https://hf-mirror.com`（huggingface.co 在 174 上不通）
- 任何操作漏了这个环境变量就报 `Errno 101`

**缺口**：笔记**未纳入 git**。多 agent 并行写记忆会冲突——这恰好是它要解决的问题本身。

---

## 第 3 项：codebase-memory-mcp ✅ 完全实现

| 指标 | 实测值 |
|---|---|
| 版本 | 0.11.0（**portable 版**） |
| 二进制 | `$CBM_DIR/codebase-memory-mcp`（299MB） |
| 已索引 | `vcc-example-project` → **83 节点 / 82 边 / 2.2MB** |
| 软链处理 | 自动排除 `ext_data`/`priors`/`results`（不去扫 NFS 上的 15G） |

**踩过的坑**：
- 必须用 portable 版（普通版要 glibc 2.38，174 是 2.35）
- **`cli` 子命令会卡死**，必须走 stdio JSON-RPC，可用脚本 `$LOG_DIR/cbmprobe.py`
- 只能 stdio，没有 HTTP 模式

---

## 第 4 项：TencentDB Agent Memory（wiki 知识层）❌ 未实现

### 现状

**从未安装。** 全盘搜索无任何痕迹，8765/9749/8000/3000 端口无相关服务。

### 但「只要 wiki 知识层」这个诉求，仓库结构上是支持的

`MemoryKnowledge` 确实是独立顶层目录（和 `MemoryCore`/`MemoryPanel`/`MemoryProxy` 平级）。
我拉了它的 `package.json` 解码，证据如下：

```
name        : @tencentdb-agent-memory/knowledge-service
version     : 0.1.0
engines.node: >=22.0.0          ← 硬门槛
bin         : { "knowledge-server": "./bin/server.mjs",
                "knowledge-mcp": "./bin/mcp.mjs" }   ← 独立 MCP server 确实存在
scripts     : dev / dev:mcp / build / db:generate / db:migrate / typecheck
```

**关键结论**：
- ✅ **无 `workspace:` / `file:` 跨包引用** → 理论上能脱离其他模块独立部署
- ✅ **自带 `knowledge-mcp`** → 可以只把它当 MCP server 用，不需要 Web 面板
- ✅ **有 `db:generate` / `db:migrate`** → 自己的 Drizzle/SQLite schema，不强依赖 MemoryCore
- ⚠️ 但官方推荐的 `./start-all.sh` 是**三件套一起起**（memory-core + memory-hub + proxy），
  只装一个属于官方未文档化的用法

### 三个硬障碍（实测确认）

**障碍 1：Node 版本差太远**

```
174 当前: v12.22.9
MemoryKnowledge 要求: >=22.0.0
```
- 无 nvm，只有 `/usr/bin/node` 一个
- **但可以绕过**：`nodejs.org/dist/v22.14.0/...tar.xz` 实测 **200**，
  npm 的 `node-linux-x64` 包也 200。解压到某个目录用绝对路径即可，**不动系统 node**。

**障碍 2：需要编译原生模块**

依赖里有 `better-sqlite3 ^11.10.0`，需要 node-gyp 编译。
好消息：`gcc 11.4.0` + `GNU Make 4.3` 齐全，Python 3.10 也在。

**障碍 3：Wiki 生成依赖 LLM API ⚠️ 这个最关键**

依赖里有 `@ai-sdk/anthropic ^3.0.95` 和 `@ai-sdk/openai ^3.0.53`。
README 明确说要把**文档转成 Wiki 是通过 LLM 做的**，且 `.env` 里要填**两组** LLM 参数
（memory group + proxy group）。

实测 174 上：
```bash
$ env | grep -i -E "api_key|openai|anthropic"
（空 —— 没有任何 LLM API key）
```

**没有 key，Wiki 就生成不出来。** 而且它还需要外网能访问 LLM 服务，
174 的出网是选择性的。

### 另外：Docker 路线也走不通

```
$ docker ps
permission denied (需要 docker 组权限，<USER> 不在组内)
$ sudo -n true
sudo: a password is required      ← 无免密 sudo
```
官方 `start-all.sh` 那套要拉多个镜像，需要 Docker 权限，**当前拿不到**。

### 我的建议

**先问清你的真实意图**，因为这决定了值不值得投入：

- 如果你想要的是「**自动把代码/文档转成结构化 Wiki**」——那确实只有 TencentDB 能做，
  Basic Memory 做不到（它只存你手写的 Markdown）。但需要你提供 LLM API key。
- 如果你想要的是「**多 agent 共享的团队记忆 + 权限管理**」——TencentDB 的核心价值在这里，
  但你现在只有 5 个 agent 都在同一台机器上，Basic Memory 的 Markdown + git 已经够用。

**折中方案**：如果确实需要 LLM 能力，可以只装 `MemoryKnowledge` 单个服务
（无 workspace 依赖，可行），配一个 key，只用它做 Wiki 生成，
产出物是 Markdown，可以直接进 git。**但这需要你先确认有可用 LLM API key。**

---

## 当时需要你决策的三件事（现已全部解决）

| # | 问题 | 结论 |
|---|---|---|
| **1** | VCC 看板路径选哪个方案？ | ✅ 采用方案 A，`ports.json` 改指向 `$PROJECT_DIR/example-project`，已实测通过（commit `8510dd2`） |
| **2** | 第 4 项（TencentDB 知识层）还要不要做？ | ✅ 已完成并端到端跑通：198 字节源文件 → 13 个 Wiki 页面（约 20 秒） |
| **3** | Basic Memory 笔记目录要不要做 git 追踪？ | ✅ 已做，`$PLATFORM_ROOT/memory` 仓库 commit `39445ce` |

**已解决**：看板缺陷已修复验证，agent 现在建的任务看板能读到。
**但任务卡仍然是空的** —— 平台已就绪，缺的是第一张任务卡。
建议先在 VCC 项目建几张任务卡（写到 `$PROJECT_DIR/example-project`，即看板实际读的目录），
否则让专家审查时，他只能验证「部署正确」，验证不了「协作流程能跑通」。
