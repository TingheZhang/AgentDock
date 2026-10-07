# 专家审阅入口（START HERE）

> **历史材料：** 本文件保留当时的审阅入口和证据，不是当前运行命令或
> 当前部署状态的权威来源。当前操作请读仓库根部 `SKILL.md`、`README.md`
> 和 `docs/OPERATIONS.md`。

> 2026-10-05 · 服务器 `<USER>@<LAN_IP>`
> 登录后第一条命令：`cd $PLATFORM_ROOT/platform/review && ls`

本文件是 review 目录的索引。专家**只需要读本文件 + 下面指定的 4 份材料**，
其余代码可按需抽查。

---

## 一、读材料的顺序（按这个顺序，约 60 分钟读完）

| 顺序 | 文件 | 行数 | 读它回答什么问题 |
|---|---|---|---|
| **1** | `expert-onboarding.md` | 327 | 我该怎么登录、怎么动手、**哪些命令绝对不能跑** |
| **2** | `platform-review-brief.md` | 282 | **平台整体设计**、功能分三级、技术债、7 条可复现的验证命令 |
| **3** | `idea-verification-report.md` | 225 | 用户的四项原始需求**逐项实现到什么程度**、证据链 |
| **4** | `memory-tools-config.md` | 266 | 三个记忆工具 + 知识层的配置与四个 API 坑 |

配套（在上一层目录）：

| 文件 | 行数 | 用途 |
|---|---|---|
| `../OPERATIONS.md` | 114 | 日常运维：服务拓扑、git 布局、排错表、部署到新机器 |
| `$PROJECT_DIR/example-project/AGENTS.md` | 95 | **多agent 协作契约**（分支/review/追踪边界）——见 §三.1 |
| `../bb-ports.sh` | 12KB | 端口管理主脚本 |
| `../bb-aggregate.py` | 30KB | 聚合页生成器（重点看 `main()` 完整性） |
| `../ports.json` | 1.3KB | 单一真相源 |
| `../systemd/` | 6 个文件 | 开机自启单元 + 健康检查 |

---

## 二、30 分钟快速验证（专家最先做这个）

```bash
cd $PLATFORM_ROOT/platform
./bb-ports.sh status                      # 期望 3 行全 up/200
```

| 端口 | 期望 | 实测（2026-10-05） |
|---|---|---|
| 6421 聚合页 | 200 | ✅ 200 |
| 6422 VCC 看板 | 200 | ✅ 200（但页面**不含项目标识**，200 ≠读对项目，见§二） |
| 6420 写作侧 | 200 | ✅ 200 |
| 8421 知识层 | `{"status":"ok"}` | ✅ ok |
| 8765 Basic Memory | MCP 握手 200 | ✅ 200 + 21 工具 |

再跑两条关键判据：

```bash
# ① 看板 cwd 判据：必须等于 agent 的工作目录
# 按监听端口反查 PID，不能用 pgrep（理由见下方警告）
PORT_PID() { ss -tlnp 2>/dev/null | grep ":$1 " \
  | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2; }
readlink /proc/$(PORT_PID 6422)/cwd
# 期望：$PROJECT_DIR/example-project

# ①b HTTP 200 不代表读对项目 —— 必须核对任务来源
curl -s http://<LAN_IP>:6422/api/tasks | head -c 200
# 期望：能看到任务数据。当前是 []（尚无任务卡）
# 注意：6422 首页 HTML 里不含任何项目名标识，
# 所以「看了页面没报错」不能证明它读对了目录

# ② 自启与自愈
systemctl --user is-active platform-core.target vcc-healthcheck.timer
loginctl show-user <USER> | grep -i linger       # 期望 Linger=yes
tail -5 $LOG_DIR/healthcheck.log
```

⚠️ **判据不要用 `pgrep -f`**。它匹配的是「命令行里含该字符串」的进程，
**不保证它真的持有这个监听端口**：
① 会匹配到执行这条命令的 bash 自己；
② 同端口有多个候选进程时，`head -1` 取的是第一个而非持端口的那个。

**正确做法**是从监听端口反查 PID（上面的 `PORT_PID()`）。
当前实测两者结果相同，但**证据强度不同**——只有按端口查的结果能证明
「持端口的那个进程在哪里工作」。此条为专家审阅指正，已改。

---

## 三、⚠️ 三个必须知道的陷阱

### 1. 别看错文件：协作契约在**项目里**，不在平台目录

平台目录 `$PLATFORM_ROOT/platform/AGENTS.md`（24 行）**只有** Backlog.md
自动注入的工作流规范——**没有协作契约**，那是对的，别去那里找。

真正的协作契约在**项目工作副本**：`$PROJECT_DIR/example-project/AGENTS.md`（95 行）：

| 内容 | 要点 |
|---|---|
| 仓库位置 | remote `$BARE_REPO`，工作副本 `$PROJECT_DIR/example-project` |
| **NFS 禁 git** | `$NFS_MOUNT/...` 是数据位置（16GB），**不是**工作副本；实测 NFS 上 `git add` 慢 100~700 倍，300 文件的 `git add` 跑不完，且有 stale index lock 风险 |
| 分支约定 | `agent/<who>-<task-id>`，**永不直接 commit 到 main** |
| review 流程 | commit 带 task id → `push` 分支 → reviewer 看 diff → `git merge --no-ff` → **合并后才标 Done** |
| 为什么 `--no-ff` | 保留reviewer 的合并作为 `git log` 里独立的审阅点；fast-forward 会抹掉「谁做的」和「谁批的」的边界 |
| 追踪边界 | 追踪 `scripts/ paper/ figures/ .backlog/ AGENTS.md`；**不追踪** `ext_data/ priors/ results/ *.log`（16GB 产物） |

⚠️ **`.backlog/` 必须保持被追踪**——它是看板读的共享任务状态，排除掉就丢审阅历史了。

**缺口 2 的现状**：规则文本**很完整**，但**没有任何机制强制执行**。
agent 完全可以直接在 `main` 上改、直接标 Done，看板照样显示 Done。
详见 `platform-review-brief.md` §4 缺口 2。

### 2. `$PLATFORM_ROOT/platform` 的 git 目录**不在这个目录里**

`.git` 是个 40 字节的**文件**（不是目录），指向本地盘：

```
.git内容: gitdir: $GIT_DIR
```

原因：NFS 上 `git init` 要 22.6 秒，separate-git-dir 后 0.197 秒（~100x）。
**查 git 历史用 `cd $PLATFORM_ROOT/platform && git log`，不要 `cd .git`。**

### 3. 判据不能只看退出码 / `is-active`

- `bb-ports.sh --help` 的 usage 走 **stderr** 且**返回 2**（bash 常规行为）。
  判据是「有没有 usage 文本」：`./bb-ports.sh --help 2>&1 | grep -q "Usage:"`
- **远程跑 `bb-ports.sh restart` 会 SIGTERM 打断你的 ssh 会话**（脚本内部 `pkill`
  匹配到了调用者的 ssh 命令行）。**服务本身完全正常**——看端口和
  `systemctl is-active`，别看 ssh 退出码。
- `Type=oneshot` 单元的 `is-active=active` **不代表子进程活着**
  （实测 `kill -9` 后仍报 active）。以端口为准。

**绝对不要跑**：`pkill -f backlog` / `pkill -f basic-memory` / `basic-memory reset`
（详见 `expert-onboarding.md` §5）。

---

## 四、git 历史（6 个 commit，可逐个 review）

```
8ffb5cd docs: 专家审查材料同步看板修复状态
34d06d4 docs: 手册更新看板章节——已修+ 可复现验证步骤
8510dd2 fix: VCC 看板读本地盘工作副本（原读 NFS 那套，看不到 agent 建的卡）
d089e69 docs: 平台运维手册 OPERATIONS.md
e4dcae5 feat: 四个服务 systemd user 单元 + 崩溃自愈定时器
d9248e3 init: 平台运维脚本纳入 git
```

`git show 8510dd2` 建议重点看——那是唯一改动运行时行为的 commit。

另一个仓库：`$PLATFORM_ROOT/memory`（commit `39445ce`，2 篇笔记）。

---

## 五、现状：四项需求 100% 达标

| # | 需求 | 状态 |
|---|---|---|
| 1 | Backlog.md 管理 + 可视化 | ✅ 路径缺陷已修（`8510dd2`），端到端验证过 |
| 2 | Basic Memory 笔记 | ✅ conda 环境 + 21 个 MCP 工具 |
| 3 | codebase-memory-mcp | ✅ 83 节点 / 82 边 |
| 4 | TencentDB 知识层 | ✅ 198 字节源文件 → 13 个 Wiki 页面（约 20 秒） |

**唯一的内容层缺口**：`tasks/` 仍是空的——平台已就绪，缺第一张任务卡。

---

## 六、留给专家的四个问题

1. **审核闭环怎么强制？** 契约文本很完整（见 §三.1），但**零强制力**。倾向方案：
   Done 判定权归 git（看板读 git 状态而非任务 status 字段），
   因为 `pre-commit hook` 有 `--no-verify` 漏洞。
2. **怎么防止「看板读错目录」再发生？** 一次性验证步骤写在 `OPERATIONS.md`，
   但没做成自动化检查。值得做吗？
3. **多 agent 并行写 Basic Memory 笔记的冲突策略？** 现在靠 git 合并冲突解决，
   够用吗，还是需要更细的粒度约定？
4. **平台级协作规则放哪？**当前契约在项目里，
   平台目录那份 `AGENTS.md` 仅用于容器、不被任何 agent 读取。

---

## 七、写批注放哪

`review/` 目录**可写**（`tgh:biolab` 属主，`<USER>` 有写权限）。建议：

- 直接在对应 md 文件里加批注，或新建 `review/<你的名字>-批注.md`
- **不要直接改 `bb-ports.sh` / `bb-ports.json` / `ports.json`**——
  改坏平台会挂。发现 bug 请写在批注文件里，我们改。

---

## 八、平台架构速览

```
5 台电脑（workbuddy/claude/2×codex/antigravity）
        │ 全部 ssh 到 174
        ▼
174 (<LAN_IP>)───────── 4 台计算服务器（$NFS_MOUNT NFSv4 共享）
  │
  ├─ Backlog.md  6420/6421/6422   任务看板
  ├─ Basic Memory 8765            语义笔记（MCP）
  ├─ MemoryKnowledge 8421LLM 自动抽 Wiki
  └─ codebase-memory-mcp (stdio)  代码调用图谱
```

存储分层：本地盘 `/data`（11T，剩 3.2T）跑服务 + git；
`$NFS_MOUNT`（NFS）存共享实验数据。
