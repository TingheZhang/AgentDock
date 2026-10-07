# 专家接手指引：审查 174 上的多 agent 协作平台

> 配套文档：`$PLATFORM_ROOT/platform/review/platform-review-brief.md`（审查说明，含完整功能清单与缺口分析）
>
> 本文档只解决一件事：**你 SSH 上来后，30 分钟内能独立判断这套平台对不对。**

---

## 1. 登录

```bash
ssh <USER>@<LAN_IP>
```

**登录后你会落在 `$HOME_DIR`，里面有两千多个历史文件（`adamson_*.log`、`*.err` 等），
跟本次审查无关，直接忽略。** 平台相关的东西全在下面三个位置。

如果你没有 `<USER>` 账号的密钥/密码，先找用户要，不要试图破解。

---

## 2. 先跑这一条命令（最重要）

```bash
$PLATFORM_ROOT/platform/bb-ports.sh status
```

**期望输出**（实测，2026-10-05）：

```
NAME               GROUP     PORT      STATE   HTTP
vcc-example-project     <STUDY_GROUP>  6422      up      200
writing-side       写作侧 6420      up      200
(dashboard)        -         6421      up      200

Open: http://<LAN_IP>:6421/
```

这条命令一次性告诉你：有几个项目、各自端口、进程是否活着、HTTP 是否通。
**审查从这条命令开始。** 如果 `STATE` 不是 `up` 或 `HTTP` 不是 `200`，问题已经定位了。

---

## 3. 三个位置，不要在别处找

| 位置 | 是什么 |
|---|---|
| `$PLATFORM_ROOT/platform/` | **平台运维目录**：看板生成器、端口管理、唯一真相源 |
| `$PROJECT_DIR/example-project/` | **真实科研项目**：代码、论文、图、git 仓库、协作契约 |
| `$PLATFORM_ROOT/memory/` | **知识笔记**：Basic Memory 的 Markdown 源文件 |
| `$HOME_DIR/.basic-memory/` | 知识库索引库（SQLite，一般不用动） |

注意 `$NFS_MOUNT/` 是 **NFS**（网络文件系统，慢，不要在上面跑 git）；
`<local-data-root>/` 和 `<home-root>/` 是**本地盘**。

---

## 4. 建议的审查顺序（约 30 分钟）

### 第 1 步：网页看一眼（2 分钟）

浏览器打开：

- `http://<LAN_IP>:6421` — 聚合看板，项目列表 + 跳转
- `http://<LAN_IP>:6422` — VCC 项目看板（**⚠️ 任务列表是空的，这是已知缺口**）
- `http://<LAN_IP>:6420` — 写作侧看板（有 5 个任务卡，可作格式参考）

### 第 2 步：读 3 个源文件（10 分钟）

按重要性排序：

```bash
# ① 唯一真相源 —— 37 行，先看这个建立整体印象
cat $PLATFORM_ROOT/platform/ports.json

# ② 进程管理 —— 重点看参数分派和错误处理
cat $PLATFORM_ROOT/platform/bb-ports.sh

# ③ 看板生成器 —— 741 行，重点看 main() 是否完整
cat $PLATFORM_ROOT/platform/bb-aggregate.py
```

**审查 `bb-aggregate.py` 时请特别确认**：文件末尾有完整的
`if __name__ == "__main__": main()`。这个脚本历史上被编辑截断过，
当时 `main()` 整块丢失，现象是**命令返回 0 但零输出、不产文件**。
所以先跑入口探针：

```bash
python3 $PLATFORM_ROOT/platform/bb-aggregate.py --help    # 必须有输出
$PLATFORM_ROOT/platform/bb-ports.sh --help                  # 必须有输出
```

**两个 `--help` 都能出东西，才说明脚本完整。** 这是项目定的验证纪律。

**注意两个脚本的 `--help` 行为不同**（写验证脚本时容易踩）：

| 脚本 | usage 输出到 | 退出码 | 正确的判断方式 |
|---|---|---|---|
| `bb-ports.sh` | **stderr** | **2** | `bash bb-ports.sh --help 2>&1 \| head -1` |
| `bb-aggregate.py` | stdout | 0 | `python3 bb-aggregate.py --help 2>&1 \| head -1` |

如果用 `test "$?" -eq 0` 判断，`bb-ports.sh` 会**假失败**（它成功也返回 2）。
**判断标准是「有没有 usage 文本」，不是退出码。**

一键检查两个脚本是否完整：

```bash
$PLATFORM_ROOT/platform/bb-ports.sh --help 2>&1 | grep -q "Usage:" \
  && echo "bb-ports.sh 完整" || echo "bb-ports.sh 被截断！"
python3 $PLATFORM_ROOT/platform/bb-aggregate.py --help 2>&1 | grep -q "usage:" \
  && echo "bb-aggregate.py 完整" || echo "bb-aggregate.py 被截断！"
```

### 第 3 步：读协作契约（10 分钟）

**这是本次审查最重要的文件。** 平台的正确性不取决于代码写得好不好，
取决于 agent 会不会照规矩做事。

```bash
cat $PROJECT_DIR/example-project/AGENTS.md          # 95 行，agent 行为契约
cat $PROJECT_DIR/example-project/.gitignore         # 大数据排除规则
cat $PROJECT_DIR/example-project/.backlog/config.yml
```

**看 `.gitignore` 时请注意第 12-20 行**——那里有个反直觉的设计，
文件里自己写了注释说明：

```
12:# NOTE: match both directory contents ("ext_data/") and a symlink of the same
13:# name ("ext_data"). A trailing-slash pattern does NOT match a symlink, so both
15:ext_data/
16:ext_data
17:priors/
18:priors
```

**原因**：`ext_data/` 这条带斜杠的规则**不匹配同名软链**。
本项目的大目录是软链（指向 NFS），曾因只写斜杠形式导致 **7 个软链被 git 追踪**。
所以每个目录必须**同时写 `dir/` 和 `dir` 两种形式**。
这是个容易复发的坑，如果你要往 `.gitignore` 加新目录，务必照做。

### 第 4 步：跑验证命令（8 分钟）

```bash
# ① 大数据排除是否生效（期望：三条都命中规则，文件数 = 42）
cd $PROJECT_DIR/example-project
git check-ignore -v ext_data priors results
git ls-files | wc -l

# ② git 历史里审核合并点是否可见（应有 --no-ff 的合并）
git log --graph --oneline --all

# ③ 代码图谱索引
# ⚠️ 不要用 `cli` 子命令 —— 它会卡死无输出（2026-10-05 实测）
# 用这个脚本（走 stdio JSON-RPC，已验证可用）：
python3 $LOG_DIR/cbmprobe.py list_projects detail=stats
# 期望：vcc-example-project ... 83 82 2293760

# ④ 知识库语义搜索（注意 HF_ENDPOINT 必带，否则报错）
HF_ENDPOINT=https://hf-mirror.com \
  $HOME_DIR/miniconda3/envs/basic-memory/bin/basic-memory \
  tool search-notes "git" 2>&1 | head -20

# ⑤ 知识库状态
HF_ENDPOINT=https://hf-mirror.com \
  $HOME_DIR/miniconda3/envs/basic-memory/bin/basic-memory project info vcc-notes
```

### ⚠️ 关于 codebase-memory-mcp 的 `cli` 子命令

它的 `--help` 里写着可以用 `cli <tool> [args]` 本地调工具，但**实测不可用**：

```bash
# ✗ 会卡死、零输出、直到被 SIGTERM
$CBM_DIR/codebase-memory-mcp cli --quiet list_projects detail=stats
```

两个坑叠加：`--quiet` 会吞掉真实错误，`--verbose` 会启 UI 卡住。
即使参数正确（`repo_path=xxx`）也可能报 `repo_path is required`。

**正确做法**：用 `$LOG_DIR/cbmprobe.py`，它走 stdio JSON-RPC 协议，
是 MCP 的原生调用方式，17 个工具全部可用：

```bash
python3 $LOG_DIR/cbmprobe.py <工具名> [key=value ...]

# 例子
python3 $LOG_DIR/cbmprobe.py list_projects detail=stats
python3 $LOG_DIR/cbmprobe.py get_architecture project=vcc-example-project aspects=overview
python3 $LOG_DIR/cbmprobe.py search_graph project=vcc-example-project query=index
```

**如果专家要验证平台本身**，这不算平台缺陷（二进制是第三方件），
但如果专家要**索引新的代码仓库**，发现 `cli` 不可用是正常的，用上面的脚本。

**顺带请专家评估**：`cli` 子命令的 `--help` 明确写着能本地调工具，实际却卡死，
且 `--quiet`/`--verbose` 两种模式都无法给出有效错误信息。
这属于**错误信息设计缺陷**——如果 agent 遇到这情况，会浪费大量 token 反复重试。
建议在我们的封装层统一走 stdio JSON-RPC，并在文档里写死这一点。

---

## 5. 三个已知缺口 —— 请重点判断

`platform-review-brief.md` §4 有完整分析，这里只列结论。

### 缺口 1：VCC 项目的看板是空的

```bash
ls -la $PROJECT_DIR/example-project/.backlog/tasks/    # 空目录
```

目录骨架齐全（tasks/completed/drafts/archive/decisions/milestones/docs 都在），
`config.yml` 配好了、端口也对，但**没有任务卡**。
6422 看板开着，列表是空白。

**请判断**：这是"部署了还没开始用"，还是配置有问题导致任务写不进去？
如果是前者，平台该提供什么引导让 agent 知道要先建任务？

### 缺口 2：审核闭环无强制力

`AGENTS.md` 规定：agent 走 `agent/<who>-<task-id>` 分支 →
reviewer 用 `git merge --no-ff` 合并 → **合并后才能标 Done**。

但没有任何机制阻止 agent 直接在 `main` 上改、直接把任务标 Done，看板照样显示 Done。

**请判断**：怎么让"未合并的分支不允许标 Done"成为硬约束？
（注意 pre-commit hook 有 `--no-verify` 漏洞，挡不住。）

### 缺口 3：平台自己的脚本没纳入 git

```bash
cd $PLATFORM_ROOT/platform && git rev-parse --git-dir   # 报错 = 没有 .git
```

专家审查后如果要改 `bb-ports.sh`，**改坏了无法回滚**。
而 VCC 项目是纳管了的（42 文件 / 2 提交 / 1.1M）——这个对比本身说明问题。

**请判断**：平台运维脚本应该独立一个 git 仓库，还是并入某个项目仓库？
（NFS 上不能放 git，需要用 `$BARE_REPO_ROOT/`。）

---

## 6. 硬约束：不要跑这三条命令

这三条会**破坏正在运行的服务或数据**：

```bash
# ✗ 不要：pkill 会打断其他人的 ssh 会话，也可能杀掉聚合页
pkill -f backlog

# ✗ 不要：会杀掉正在服务的 Basic Memory MCP
pkill -f basic-memory

# ✗ 不要：重置知识库索引库
$HOME_DIR/miniconda3/envs/basic-memory/bin/basic-memory reset
```

**要停服务用正规方式**：

```bash
# 先拿 PID，再按 PID kill
pgrep -f "basic-memory mcp" | head -1
kill <PID>

# 看板服务用管理脚本
$PLATFORM_ROOT/platform/bb-ports.sh stop <name>
```

---

## 7. 出网限制（会影响你能否装工具）

出网是**选择性封锁**，不是全通也不是全封：

| 可用 | 封锁 |
|---|---|
| `github.com`（Releases 重定向到 `release-assets.githubusercontent.com`，通） | `objects.githubusercontent.com` |
| `api.github.com:443` | `raw.githubusercontent.com` |
| `gitlab.com:443` | `pypi.org:443`（**但 pip 实际能用**，4.8MB/s） |
| `registry.npmjs.org:443` | `huggingface.co`（**用 `HF_ENDPOINT=https://hf-mirror.com`**） |
| `hf-mirror.com` | — |
| `repo.anaconda.com` / `conda.anaconda.org` | — |

**重要陷阱**：用裸 `curl` 测出的封锁结论**不能用来否定 pip**。
`pypi.org` 裸 curl 返回 403，但 `pip install` 正常工作。

---

## 8. 常用非交互命令（免 SSH 引号地狱）

在 Windows 上通过 `ssh host '...'` 传复杂命令时，双层引号会被吃掉。
**建议把脚本落成文件再 scp 过去执行**，或者用简单单引号命令。

```bash
# 查文件有多少行
ssh <USER>@<LAN_IP> 'wc -l $PLATFORM_ROOT/platform/bb-aggregate.py'

# 查 HTTP 状态
ssh <USER>@<LAN_IP> 'curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:6422/'

# 查服务监听
ssh <USER>@<LAN_IP> 'ss -tlnp | grep -E "6420|6421|6422|8765"'
```

---

## 9. 审查产出建议

请在 `$PLATFORM_ROOT/platform/review/` 下写你的审查报告（该目录可读写），
文件名如 `review-<你的名字>-<日期>.md`，包含：

1. **§1 平台是否正确** —— 三个工具的分工是否解决了「进度可见/记忆共享/审核闭环」
2. **§2 缺口 1 的判断** —— 空看板是配置问题还是引导问题
3. **§3 缺口 2 的方案** —— 怎么让审核闭环有强制力（这是最需要你专业意见的）
4. **§4 你发现的、我没发现的问题**
5. **§5 是否建议继续投入这套平台**

---

## 10. 一句话总结现状

- **已跑通**：多项目看板、LAN 访问、端口统一管理、git 审核流程、
  大数据排除（16G 全挡住）、代码图谱索引、知识库语义搜索、远程 MCP 接入
- **已知有前提**：语义搜索必须带 `HF_ENDPOINT`；codebase-memory 必须用 portable 版
- **没有**：服务开机自启、agent 身份区分、审核强制力
- **最需要你判断**：审核闭环怎么才能真正强制执行
