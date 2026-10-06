# 平台审查说明：让专家看哪里、能做什么、哪里有坑

> 生成时间：2026-10-05
> 服务器：<LAN_IP>（Ubuntu 22.04 / glibc 2.35）
> 本文档目的是让一个**不了解上下文的专家**能在 30 分钟内独立判断这套多 agent 协作平台是否正确。

---

## 0. 一句话说明这个平台解决什么问题

5 个 agent（CodeBuddy、Claude、2×Codex、Antigravity）分布在 3 台电脑上，
但都 SSH 到同一台 174 服务器、在同一个科研项目里工作。目标是解决三个问题：

1. **进度可见** —— 不去问任何人，打开网页就知道谁在做什么
2. **记忆共享** —— agent 之间不重复劳动、不偏离主线
3. **审核闭环** —— 执行者的产出能被另一个 agent 审核后才算数

---

## 1. 让专家看哪里（按优先级，含访问方式）

### 优先级 1：直接打开网页看（最快，30 秒）

| 地址 | 是什么 | 专家要看什么 |
|---|---|---|
| `http://<LAN_IP>:6421` | 聚合看板 | 项目列表 + 跳转链接是否正确、是否硬编码 |
| `http://<LAN_IP>:6422` | VCC 项目看板（agent 工作副本） | ✅ 已修（2026-10-05），见 §4 缺口 1 |
| `http://<LAN_IP>:6420` | 写作侧演示看板 | 有 5 个任务卡，可作为任务卡格式参考 |
| `http://<LAN_IP>:8765/mcp` | Basic Memory MCP | 打开会返回 400（正常，MCP 要 POST+协议头） |

**专家需要验证的核心问题**：6421 上的项目列表是不是真的从 `ports.json` 读的，
还是写死的？换一个项目进去，它的看板是不是真的独立？

### 优先级 2：读关键源码（3 个文件，共 ~47KB）

| 文件 | 行数 | 审查什么 |
|---|---|---|
| `$PLATFORM_ROOT/platform/ports.json` | 37 | **唯一真相源**。看板和启动脚本都读它，所以不可能不一致。看项目路径/端口/分组是否清晰 |
| `$PLATFORM_ROOT/platform/bb-ports.sh` | 398 | 进程管理：start/stop/restart/status/refresh/add/remove。重点看**参数分派**和**错误处理** |
| `$PLATFORM_ROOT/platform/bb-aggregate.py` | 741 | 看板生成器。重点看 **`main()` 是否完整**（有过被截断的事故，见 §4 缺口 3）、HTML 转义、异常处理 |

`$PLATFORM_ROOT/platform/www/index.html`（6.2KB）是生成产物，不是源文件。

### 优先级 3：读协作规则（这是"审核闭环"的核心）

| 文件 | 为什么重要 |
|---|---|
| `$PROJECT_DIR/example-project/AGENTS.md`（95 行） | **agent 的行为契约**。定义了分支命名、review 流程、什么该 git 追踪。审核平台正确性时这是最该被审的文件 |
| `$PROJECT_DIR/example-project/.gitignore` | 大数据排除规则。专家应验证没有 15G 文件混进 git |
| `$PROJECT_DIR/example-project/.backlog/config.yml` | 看板配置。注意 `filesystem_only: true`（纯文件，不存 DB） |

**SSH 访问方式**：`ssh <USER>@<LAN_IP>`

---

## 2. 现在真正能用的功能（已实测，非推测）

### ✅ 完全可用

| 功能 | 实测证据 |
|---|---|
| **聚合看板** | 6421 显示 2 个项目，链接指向 6422/6420，`vcc-example-project` / `writing-side` 名称正确渲染 |
| **多项目独立看板** | 6420 / 6422 各跑独立容器，各自任务 ID 从 TASK-1 起，不撞号 |
| **局域网访问** | 从本地 Windows 直连实测 200；监听在 `<LAN_IP>` 而非 127.0.0.1 |
| **端口统一管理** | `bb-ports.sh` 支持 7 个动作；`ports.json` 单一真相源 |
| **git 审核闭环** | 2 次提交在 `main`；`--no-ff` 合并流程已写进 AGENTS.md 并实测跑通 |
| **大数据排除** | git 只追踪 42 文件/1.1M，16G 数据全排除，泄漏 0 |
| **代码图谱索引** | `vcc-example-project` 83 节点/82 边；软链自动排除，没扫 NFS 上的 15G |
| **知识库语义搜索** | 查「嵌套 CV」→ score 1.199 精确命中；21 个 MCP 工具可用 |
| **远程 MCP 接入** | `http://<LAN_IP>:8765/mcp` 从本地 Windows 实测 200/20ms |

### ⚠️ 部分可用 / 有前提

| 功能 | 限制 |
|---|---|
| Basic Memory 语义索引 | **任何操作必须带 `HF_ENDPOINT=https://hf-mirror.com`**，否则报 `Errno 101`（huggingface.co 在这台机器上不通） |
| codebase-memory-mcp | **必须用 `-portable` 版**，普通版要 glibc 2.38，这台是 2.35 |
| 自动刷新 | 看板每 5 分钟刷新，无 WebSocket 推送 |
| 服务开机自启 | **未配置**。服务器重启后 4 个服务全部要手动拉起 |

### ❌ 尚未实现

| 功能 | 状态 |
|---|---|
| 多 agent 身份区分 | 所有 agent 看到同一份任务列表，无法区分"谁做的" |
| 审核状态机 | 没有"待审核 → 已批准"的独立状态，审核只体现在 git 分支上 |
| 笔记进 git | `$PLATFORM_ROOT/memory` **未纳入版本控制**，多 agent 并行写记忆会冲突 |
| 死任务检测 | 无超时/卡住检测，agent 崩了任务永远停在 In Progress |
| 跨项目搜索 | 只能按项目过滤，没有全局搜索 |

---

## 3. 已知技术债（审查时重点看这几处）

### 出网是选择性的，不是全通也不是全封
| 可用 | 封锁 |
|---|---|
| `api.github.com:443`、`github.com`（Releases 重定向到 `release-assets.githubusercontent.com`） | `objects.githubusercontent.com` |
| `gitlab.com:443`（1GB clone 成功） | `raw.githubusercontent.com` |
| `registry.npmjs.org:443` | `pypi.org:443`（**但 pip 实际能用**，4.8MB/s） |
| `hf-mirror.com` | `huggingface.co` |
| `repo.anaconda.com`、`conda.anaconda.org` | — |

**陷阱**：`pypi.org` 裸 curl 测是 403，但 `pip install` 正常。**别用 curl 的结论否定 pip。**

### NFS 上不能放 git
`$NFS_MOUNT` 是 NFSv4（`<LAN_IP_2>:/Backup`）。实测 git 慢 100~700 倍：
`git add` 0.02s→**6.86s**、`commit` 0.02s→**2.83s**、300 文件时卡在 `D` 状态 2.5 分钟未完成。

**当前设计**：git 仓库在 `$BARE_REPO`（本地 ext4），
工作副本 `$PROJECT_DIR/example-project`，大数据目录用**软链**回 NFS。

### `.gitignore` 软链陷阱
`ext_data/` 这条规则**不匹配同名软链**。曾导致 7 个软链被 git 追踪。
**排除目录必须同时写 `ext_data` 和 `ext_data/` 两种形式**。这是已修复的状态，专家应验证现在的规则仍生效：

```bash
ssh <USER>@<LAN_IP> 'cd $PROJECT_DIR/example-project && git check-ignore -v ext_data priors results'
```

### 容器布局硬限制
Backlog.md 的 `--backlog-dir` **只接受项目内路径**，实测拒绝 `../../shared`：
```
Invalid --backlog-dir value. Use 'backlog', '.backlog', or a project-relative path inside the project.
```
所以多项目必须各建一套容器，**不能共享一个 backlog 目录**。

---

## 4. 缺口清单（七项，按优先级）（缺口 1 已由我们修复，仍请专家复核）

### 缺口 1：VCC 项目看板曾是空的（✅ 已修，请复核）

原因：`ports.json` 的 `path` 指向 NFS 真实数据目录，而 agent 在本地盘工作副本工作——
两边各有一套 `.backlog/`，**agent 在工作副本建的任务看板看不到**。

已修（commit `8510dd2`）：`path` 改为 `$PROJECT_DIR/example-project`。
验证实测：建测试卡 TASK-1 → `/api/tasks` 从 `[]` 变为返回该卡（含描述/状态/filePath）
→ browser 进程 cwd 从 NFS 路径切到本地盘 → 聚合页 VCC 卡片 tasks 0→1 → 清理后回到基线。

**仍请专家看的问题**：平台该如何防止这类"看板读错目录"再发生？
接新项目时怎么自动校验（我们把一次性的验证步骤写进了 `OPERATIONS.md`，但未做成自动化）。

### 缺口 2：审核闭环只在文档里，没有强制力

`AGENTS.md` 写了规则：agent 走 `agent/<who>-<task-id>` 分支 →
reviewer 用 `git merge --no-ff` 合并 → **合并后才能标 Done**。

但这条规则**没有任何机制强制执行**。agent 完全可以直接在 `main` 上改、
直接把任务标 Done，看板照样显示 Done。

**要问专家的问题**：怎么让"未合并的分支不允许标 Done"成为硬约束？

⚠️ **此处我原先的判断有错，已更正**。我曾把两个配置开关当成门禁：
- `auto_commit: false` —— 只是**关闭 Backlog 自动提交**（它自己写任务卡时不自动 commit）
- `bypass_git_hooks: false` —— 只是**提交时不加 `--no-verify`**（hook 跳过开关）

**这两个都不是审核门禁**，即使打开 `auto_commit` 也补不上审核闭环。
感谢专家指正（附[官方配置说明](https://github.com/MrLesk/Backlog.md/blob/main/ADVANCED-CONFIG.md)）。

**另一层纠正**：git 合并只能证明代码进了某个分支，**不能证明科研实验成功、
指标正确、产物齐全**。科研场景需要的是：

```
任务 ID
  → 提交版本 / 实验运行 ID
    → 检查结果与产物
      → 审核者的验收记录        ← 必须绑定具体版本
        → 已验收
```

且**后续修改不能沿用旧的通过结论**（版本变了要重新验收）。

**真正的强制力在哪**：如果所有 Agent 用同一个 Unix 账号、都能改验收文件，
那这仍然只是协作约定，不是强制隔离。
**真正的强制需要把验收记录的写权限交给审核账号或受控服务**
（bare 仓库的写权限、或独立的验收服务）。

可行方向：
- 验收记录由**独立 Unix 账号**持有，Agent 无写权限
- bare 仓库 `$BARE_REPO` 设为审核专用
- 看板读取验收记录而非任务 status 字段
- 单独审核队列 + 审核后才写回任务状态

**审的时候注意文件位置**：这条契约写在
**`$PROJECT_DIR/example-project/AGENTS.md`（95 行，项目工作副本里）**，
不在 `$PLATFORM_ROOT/platform/AGENTS.md`（那个只有 24 行，全是 Backlog.md
自动注入的工作流规范）。规则文本本身写得很完整，
还解释了为什么必须用 `--no-ff`（保留审阅点，避免
fast-forward 抹掉「谁做的/谁批的」的边界）——
**问题不在规则写得不好，而在没有任何机制执行它**。
顺带一问：平台级的协作规则该放哪？
（平台目录那份目前仅用于容器，不被任何 agent 读取。）

### 缺口 3：脚本曾被截断，交付纪律需要制度化

`bb-aggregate.py` 曾在编辑中被截断，`main()` 和 `if __name__` 整块丢失。
现象极具欺骗性：**上传 sha256 一致、本地测试全绿、命令行返回 RC=0，
但零输出、不产文件**。

已定的验证纪律（应作为平台规范）：
1. 改完脚本**必跑入口探针**（`<script> --help`）——函数级冒烟测试发现不了入口丢失
2. **「RC=0 但无输出无产物」= 代码被截断的典型信号**，不要当成功
3. sha256 一致 ≠ 逻辑完整，只证明传字节没损坏
4. 校验 HTML 结构数量用 `grep -o ... | wc -l`，**不要用 `grep -c`**（按行计数会少数）

**要问专家的问题**：这套纪律够不够？要不要加一个 CI 阶段的"入口探针"自动检查？

---

### 缺口 4（高）：所有 Agent 共用一个工作目录，没有 worktree 隔离 —— ✅ 已修复

**修复前的实测**：`git worktree list` 只有一个目录且在 `main`；`git branch -a` 只有
`main` + `remotes/origin/main`，**没有任何 agent 工作副本**。

**问题**：`AGENTS.md` 要求每个 agent 在 `agent/<who>-<task-id>` 分支上工作，
**但所有人共用一个工作目录**——一个 agent 切换分支会改变其他 agent 看到的文件，
分支隔离完全失效。

**已交付**：`$PROJECT_DIR/example-project/vcc-wt.sh`（已随审阅合并进 main）。

| 子命令 | 作用 |
|---|---|
| `vcc-wt.sh add <who> <task-id>` | 建 `$HOME_DIR/wt-<who>`，分支 `agent/<who>-<task-id>` |
| `vcc-wt.sh list` | 全部 worktree + 分支 + 是否 dirty |
| `vcc-wt.sh check <who>` | 预检：symlink 是否可解析、有无未提交工作 |
| `vcc-wt.sh sync` | 重新注册存活 worktree、剪掉失效条目 |
| `vcc-wt.sh remove <who>` | 拒绝删有未提交工作的；未合并的分支保留 |

**为什么裸 `git worktree add` 不够**——脚本额外解决三件事：

1. **数据 symlink**。`ext_data`/`priors`/`results` 指向 NFS 且**故意不跟踪**
   （`.gitignore` 同时匹配 `name/` 和 `name`，所以 symlink 本身也被忽略）。
   新 worktree 一个都没有，agent 根本读不到那 16 GB 数据。
2. **看板可见性**。聚合看板只从 `ports.json` 读任务，worktree 不注册就
   **在 6421 上完全不可见**。脚本自动注册（`display_name` + 任务标题内联展示）。
3. **防误删**。有未提交工作 refuse；分支未合并则保留（这是防丢工作的正确默认）。

**实测（双 agent 真并行）**：

```
alice 分支: agent/alice-301  dirty=1
bob   分支: agent/bob-302    dirty=1
main  分支: main             dirty=0    ← 主目录完全未被污染
alice: 3/3 数据 symlink 可解析
bob:   3/3 数据 symlink 可解析
看板: sources 5/5 ok, tasks 11
  agent worktree: alice  (agent/alice-301)
    - TASK-2  alice: analysis of OOD recombination variants
  agent worktree: bob    (agent/bob-302)
    - TASK-2  bob: baseline figure regeneration
```

**踩到的两个静默失败**（已写进 `AGENTS.md`）：

- `git -C <主目录> merge <branch>` 合的是**主目录当前签出的分支**，不是 `main`。
  主目录被留在 agent 分支上时，merge 报「成功」而 `main` 从未移动，
  历史呈线性、**没有审阅点**。审阅前必须先
  `git -C $M rev-parse --abbrev-ref HEAD` 确认在 `main`。
- `GIT_DIR=... ` 不设 `GIT_WORK_TREE` 时，git 用 `GIT_DIR` 的默认工作树
  （主目录）——`cd $WORKTREE` 之后 `git commit` **仍然提交到主目录**。
  worktree 上操作一律用 `git -C <worktree>`，别混用 `GIT_DIR`。

### 缺口 5（高）：systemd 单元是 oneshot，MainPID=0 —— ✅ 已修复

**修复前的实测**：

| 单元 | Type | MainPID |
|---|---|---|
| `vcc-backlog.service` | oneshot | **0** |
| `vcc-knowledge.service` | oneshot | **0** |
| `vcc-basic-memory.service` | simple | 402891 ✓ |

前两个的子进程由脚本 nohup 拉起，systemd 跟踪不到。后果有三：
kill -9 后 systemd 仍报 active；`Restart=on-failure` 永不触发（脚本退出码 0）；
`status` 里看不到任何真实 pid。

**已交付**：

- `vcc-knowledge`：`ExecStart` 改为 `node dist/server.mjs`（`tsdown` 构建产物，
  **单进程**）。不再走 `ks.sh` 的 `setsid nohup pnpm run dev`（tsx 三层进程树，
  SIGTERM 会留孤儿）。
  ⚠️ 构建入口是 `node_modules/tsdown/dist/run.mjs`，**不是** `node_modules/.bin/tsdown`
  ——后者是 shell wrapper，喂给 node 会报 `SyntaxError: missing ) after argument list`。
- `vcc-backlog`：`bb-ports.sh` 一次拉起 N 个子进程（N 个 browser + N 个 relay +
  1 个 dashboard），没有单一进程可供 systemd 持有。新增
  `vcc-backlog-supervise.sh` 作前台常驻 supervisor：任一子进程消失、
  或任一必需端口「还在监听但 HTTP 000」就整体清理并退出非 0。

**修复后实测**：

| 单元 | Type | MainPID | NRestarts |
|---|---|---|---|
| `vcc-knowledge.service` | **simple** | 459671（有值） | 1 |
| `vcc-backlog.service` | **simple** | 459968（有值） | 1 |
| `vcc-basic-memory.service` | simple | 402891 | 0 |

kill -9 实测：knowledge **12s 恢复**（`ActiveEnterTimestamp` 变化确认 systemd
真的重启了单元）；backlog 子进程被 kill → supervisor 检出 → 清理 → systemd
重启 → 约 30s 后三端口全 200。

**supervisor 自身的两个坑**：

- **输出不能走管道**。`bb-ports.sh` 用 nohup 拉起的子进程继承 stdout，
  `bb-ports.sh start | sed` 会让 sed 永远等不到 EOF，supervisor 卡在管道里 ——
  **单元显示 running 而实际什么都没监管**。改为写日志文件。
- **pid 文件记的不是真正的服务进程**。bb-ports.sh 里
  `( cd .. && nohup backlog .. & echo $! > x.browser.pid )` 的 `$!` 是子 shell pid，
  真正的 `backlog` 是它的子进程（实测 pid 文件写 457130，`ss` 显示持
  127.0.0.1:6422 的是 457131）。WATCH 集合必须取并集：**pid 文件 + 从 `ss`
  反查的持端口进程**。

配置细节：`StartLimitIntervalSec`/`StartLimitBurst` 属 `[Unit]` 段
（systemd 249），放 `[Service]` 里被静默忽略；`KillMode` 用 `control-group`，
`mixed` 会只 TERM 主进程然后 SIGKILL 留下 left-over 警告和端口抖动窗口。

### 缺口 6（中）：记忆数据分散在多个目录，不在统一项目根下

**实测**：

| 内容 | 路径 | 大小 | 权限 |
|---|---|---|---|
| 笔记 | `$PLATFORM_ROOT/memory` | 216K | 775 |
| Basic Memory 索引 | `$HOME_DIR/.basic-memory/` | **67M** | **700** |
| Wiki 数据 | `$PLATFORM_ROOT/wiki/` | 620K | — |
| codebase-memory 索引 | `$HOME_DIR/.cache/codebase-memory-mcp/` | 2.2M | — |
| 科研项目 | `$PROJECT_DIR/example-project` | 2.2M | `770` |

**问题**：记忆分布在四处、**备份范围不清楚**；且 `700` 的状态目录意味着
**不同Unix 用户运行的 agent 不一定能共享**。

**建议**：统一配置数据路径与备份范围，权限按多 agent 共享前提调整。

### 缺口 7（中）：笔记并发写入无互排

数据库锁不能防止两个 agent 覆盖同一篇 Markdown。
**建议**：每个任务写独立笔记，**汇总笔记由一个整理者更新**。

### 附：专家指出的两处文档问题（已修）

1. **`pgrep | head -1` 不足以证明进程拥有该端口** —— 它只取第一个匹配进程。
   应从监听端口反查 PID。已改 `REVIEW-README.md` 的判据①为 `PORT_PID()`。
2. **HTTP 200 不能证明读对项目** —— 实测 6422 首页返回 200，
   但页面 HTML **不含任何项目名标识**，`/api/tasks` 返回 `[]`。
   启动/健康检查应核对项目绝对路径、项目标识及任务来源。
3. **codebase-memory 索引目录已定位**：`$HOME_DIR/.cache/codebase-memory-mcp/`
   （`vcc-example-project.db` + `_config.db`），实测 83 节点 / 82 边。

## 5. 专家可以怎么验证（给具体命令）

```bash
# 1. 四个服务是否活着
ssh <USER>@<LAN_IP> 'ss -tlnp | grep -E "6420|6421|6422|8765"'

# 2. 端口管理脚本的接口是否完整（改完必跑入口探针）
ssh <USER>@<LAN_IP> '$PLATFORM_ROOT/platform/bb-ports.sh --help 2>&1 | head -20'
ssh <USER>@<LAN_IP> '$PLATFORM_ROOT/platform/bb-ports.sh status'

# 3. 聚合页生成器是否完整（重点！历史上被截断过）
ssh <USER>@<LAN_IP> '$LOG_DIR/../..$PLATFORM_ROOT/platform/bb-aggregate.py --help 2>&1 | head -20'

# 4. git 大数据排除是否仍然生效
ssh <USER>@<LAN_IP> 'cd $PROJECT_DIR/example-project && git check-ignore -v ext_data priors results && git ls-files | wc -l'
# 期望：三条都命中 ignore 规则，文件数 = 42

# 5. 审核流程是否留下痕迹（应看到 --no-ff 的合并点）
ssh <USER>@<LAN_IP> 'cd $PROJECT_DIR/example-project && git log --graph --oneline --all'

# 6. 知识库能否检索
ssh <USER>@<LAN_IP> 'HF_ENDPOINT=https://hf-mirror.com \
  $HOME_DIR/miniconda3/envs/basic-memory/bin/basic-memory \
  tool search-notes "git" 2>&1 | head -20'

# 7. 代码图谱索引状态
# ⚠️ `cli` 子命令会卡死（实测），必须用 stdio JSON-RPC 脚本
ssh <USER>@<LAN_IP> 'python3 $LOG_DIR/cbmprobe.py list_projects detail=stats'
# 索引目录在 $HOME_DIR/.cache/codebase-memory-mcp/

# 8. 看板 cwd 判据（已改为按端口反查 PID）
#    不要用 pgrep -f：它不保证匹配到的进程持有该端口
ssh <USER>@<LAN_IP> \
  'P=$(ss -tlnp | grep ":6422 " | grep -o "pid=[0-9]*" | head -1 | cut -d= -f2); readlink /proc/$P/cwd'
# 期望：$PROJECT_DIR/example-project

# 9. HTTP 200 不代表读对项目 —— 必须核对任务来源
ssh <USER>@<LAN_IP> 'curl -s http://127.0.0.1:6422/api/tasks | head -c 200'
# 当前是 []（尚无任务卡）。6422 首页 HTML 不含任何项目标识。

# 10. systemd 监管盲区（缺口 5）
ssh <USER>@<LAN_IP> \
  'for u in vcc-backlog vcc-knowledge vcc-basic-memory; do printf "%-22s " $u; systemctl --user show $u.service -p MainPID --value; done'
# 实测：backlog=0, knowledge=0, basic-memory=402891✓
# 前两个 MainPID=0 说明 systemd 跟踪不到子进程

# 11. worktree 隔离（缺口 4）
ssh <USER>@<LAN_IP> 'cd $PROJECT_DIR/example-project && git worktree list && git branch -a'
# 实测：只有一个 worktree、在 main，无 agent 分支

# 12. 记忆数据分布（缺口 6）
ssh <USER>@<LAN_IP> 'stat -c "%A %U:%G %n" $PROJECT_DIR/example-project/.backlog $HOME_DIR/.basic-memory $PLATFORM_ROOT/memory'
# 实测：.backlog=770, .basic-memory=700, vcc-notes=775
# 700 意味着不同 Unix 用户的 agent 不一定能共享

# 13. 知识层鉴权（已启用，实测确认）
ssh <USER>@<LAN_IP> \
  'curl -s -o /dev/null -w "%{http_code}
" -X POST http://127.0.0.1:8421/v3/wiki/create -H "Content-Type: application/json" -H "x-tdai-service-id: <TEAM_SLUG>" -d "{\"team_id\":\"vcc\"}"'
# 实测：无 key 写 -> 401；只读查询免鉴权 -> 200
```

---

## 6. 架构现状图

```
                     3 台电脑 / 5 个 agent
                    (SSH 到 174，均可访问 LAN)
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   ┌────▼────┐          ┌─────▼──────┐        ┌─────▼──────┐
   │ Backlog │          │  Basic     │        │ codebase-  │
   │ 看板    │          │  Memory    │        │ memory-mcp │
   │ stdio   │          │  HTTP      │        │  stdio     │
   └────┬────┘          └─────┬──────┘        └─────┬──────┘
        │  6420/6421/6422    │  8765               │  SSH
        │                     │                     │
   ┌────▼─────────────────────▼─────────────────────▼────┐
   │          <LAN_IP>  (Ubuntu 22.04)            │
   │                                                      │
   │  /data  (本地 ext4 11T)                             │
   │    ├── Git/double_ood_recomb.git   ← bare 远端      │
   │    ├── <USER>/double_ood_work/       ← 工作副本       │
   │    │     ├── scripts/ paper/ figures/  ← git 追踪   │
   │    │     ├── ext_data priors results → 软链 ────────┼──┐
   │    │     └── .backlog/  ← 看板读的任务状态            │  │
   │    ├── <USER>/cbm-p/         ← codebase-memory-mcp    │  │
   │    ├── <USER>/vcc-notes/     ← 知识笔记 Markdown      │  │
   │    └── <USER>/miniconda3/envs/basic-memory/           │  │
   │                                                      │  │
   │  $NFS_MOUNT  (NFSv4 → .180)  ◄─────────────────────┘  │
   │    └── vcc2026/double_ood_recomb_20261004/           │
   │          ext_data 15G / priors 493M / results 415M    │
   │                                                      │
   │  $PLATFORM_ROOT/platform/  ← 平台运维目录             │
   │    ├── ports.json          ← 唯一真相源             │
   │    ├── bb-ports.sh         ← 进程管理               │
   │    ├── bb-aggregate.py     ← 看板生成               │
   │    └── www/index.html      ← 生成产物               │
   └──────────────────────────────────────────────────────┘
```

**三层职责**：

| 层 | 工具 | 回答什么问题 |
|---|---|---|
| 进度 | Backlog.md | 现在有什么任务，谁在做 |
| 记忆 | Basic Memory | 我们为什么这么决定，经验是什么 |
| 代码 | codebase-memory-mcp | 这个函数谁调用的，改动影响面 |

---

## 7. 建议的下一步（按投入产出排序）

| 优先级 | 事项 | 状态 |
|---|---|---|
| 高 | 给 VCC 建第一批任务卡 | ✅ 已建（TASK-1），但只 1 张，需真实科研任务填充 |
| 高 | 让 Done 状态由 git 决定 | ⬜ **未做 —— 审核闭环仍无强制力，这是平台的核心价值** |
| 中 | 记忆数据统一路径与备份范围 | ⬜ 未做（缺口 6）；`.basic-memory` 权限 700，多账号 agent 共享不到 |
| 中 | 笔记并发写入互排 | ⬜ 未做（缺口 7） |
| 中 | agent 身份区分 | 🟡 部分 —— worktree + 分支名带了 who，但任务卡上没有作者字段 |
| 低 | 死任务检测 | ⬜ 未做；`vcc-healthcheck.timer` 只查端口，不查任务状态 |
| 低 | 接入另外 4 台计算服务器 | ⬜ 未做；需先确认出网与可达性 |

**已在本轮之前完成**（不再列为待办）：
笔记纳入 git（`39445ce`）、平台脚本纳入 git（`--separate-git-dir` 规避 NFS 慢）、
四个服务开机自启（`loginctl enable-linger`，不需 root）、
看板读对项目目录（`8510dd2`）、systemd 监管改造（缺口 5）、
worktree 隔离（缺口 4）。

---

## 8. 相关文件索引

**本地（Windows）**：`C:\Users\Admin\CodeBuddy\2026-10-04-14-17-47\`
- `mcp-config-174.md` — 两个记忆工具的 MCP 配置模板（已实测）
- `bb-ports.sh` / `bb-aggregate.py` / `ports.json` / `bb-serve.sh` — 平台脚本
- `.workbuddy/memory/2026-10-05.md` — 部署过程与踩坑记录

**174 服务器**：
- `$PLATFORM_ROOT/platform/` — 平台运维（上面 §1 优先级 2）
- `$PLATFORM_ROOT/platform/vcc-backlog-supervise.sh` — backlog supervisor（缺口 5 新增）
- `$PLATFORM_ROOT/platform/systemd/` — unit 源文件（**改完要同步到
  `~/.config/systemd/user/` 再 `daemon-reload`，只改源目录不生效**）
- `$PROJECT_DIR/example-project/vcc-wt.sh` — worktree 管理（缺口 4 新增）
- `$PROJECT_DIR/example-project/AGENTS.md` — agent 协作契约（含 worktree 流程）
- `$PROJECT_DIR/example-project/.gitignore` — 大数据排除
- `$PLATFORM_ROOT/memory/` — 知识笔记
- `$HOME_DIR/.basic-memory/config.json` — 知识库配置
