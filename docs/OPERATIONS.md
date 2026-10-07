# VCC 平台运维手册

> 2026-10-05 实测。四项需求全部落地，服务已托管 systemd，开机自启 + 崩溃自愈。

## 一键操作

```bash
systemctl --user start|stop|restart|status vcc-platform.target
systemctl --user list-timers vcc-healthcheck.timer   # 看下次自愈检查
tail -f $LOG_DIR/healthcheck.log                # 自愈记录
```

## 服务拓扑

| 端口 | 服务 | 单元 | 启动方式 |
|---|---|---|---|
| 6420 | Backlog 写作侧 | vcc-backlog.service | bb-ports.sh |
| 6421 | Backlog 聚合页 | vcc-backlog.service | bb-ports.sh |
| 6422 | Backlog VCC 项目 | vcc-backlog.service | bb-ports.sh |
| 8421 | MemoryKnowledge | vcc-knowledge.service | ks.sh |
| 8765 | Basic Memory MCP | vcc-basic-memory.service | 直接跑二进制 |

```bash
ss -tlnp | grep -E '6420|6421|6422|8421|8765'   # 一次看全
```

## 当前 unit 与历史 oneshot 说明

当前 `vcc-backlog.service` 使用常驻 `Type=simple` supervisor，并按项目隔离
恢复；`vcc-knowledge.service` 直接持有构建后的知识服务进程。实际 unit 仍需用
`systemctl --user cat/show` 核对，`is-active` 不能代替 HTTP 探测。

旧 oneshot 盲区是历史背景：旧脚本用 nohup 拉起子进程后自己退出，systemd
可能仍报 `active`，子进程崩溃也不会被 unit 感知。它不是当前 unit 的行为保证。

补偿机制：`vcc-healthcheck.timer` 每 2 分钟检查五个端口，DOWN 就重启对应单元。
日志在 `$LOG_DIR/healthcheck.log`。

**判据：`systemctl is-active` 说 active 不等于服务活着。以端口为准。**

## Git 布局（两个独立仓库）

| 仓库 | 工作树 | git 目录 | 为什么这么放 |
|---|---|---|---|
| 平台脚本 | `$PLATFORM_ROOT/platform`（NFS） | `$GIT_DIR`（本地盘） | NFS 上 git init 22.6s，separate-git-dir 后 0.197s |
| 笔记 | `$PLATFORM_ROOT/memory`（本地盘） | 同目录 | 本地盘无此问题 |

```bash
cd $PLATFORM_ROOT/platform && git status
cd $PLATFORM_ROOT/memory && git status
```

平台仓库**不纳管** `.state/`（运行时产物，`bb-ports.sh:110` 生成 relay.py）、
`backlog/tasks/`、`www/index.html`。任务卡要在项目工作副本里单独管：
```bash
cd $PROJECT_DIR/example-project && git status
```

## ✅ 已修：Backlog 看板读目录（2026-10-05）

原先 `ports.json` 的 path 指向 NFS 真实数据目录，而 agent 在本地盘工作副本工作，
两边各有一套 `.backlog/`，**看板读不到 agent 建的任务卡**。

已改为 `$PROJECT_DIR/example-project`。验证方式（可复现）：

```bash
# 1. 判据：browser 进程的 cwd 必须等于 agent 的工作目录
readlink /proc/$(pgrep -f 'browser --port 6422')/cwd

# 2. 建测试卡（backlog 不在非交互 ssh 的 PATH 里，用绝对路径）
cd $PROJECT_DIR/example-project
$HOME_DIR/.local/bin/backlog task create '测试卡'

# 3. 看板 API 必须能读到
curl -s http://<LAN_IP>:6422/api/tasks | head -c 200

# 4. 清理（注意 demote 会挪到 drafts/，要 rm -rf .backlog/drafts）
$HOME_DIR/.local/bin/backlog task demote TASK-1
rm -f '.backlog/tasks/task-1 - 测试卡.md'
rm -rf .backlog/drafts
git status --short   # 必须为空
```

**接新项目时同样要验这条**：browser 进程 cwd == agent 工作目录。

⚠️ `bb-ports.sh` 内部的 `pkill` 会匹配到调用者的 ssh 命令行，
远程 `restart` 会 SIGTERM 打断会话（ssh 退出码 1）。
**服务本身是正常的——判据看端口和 `systemctl is-active`，别看 ssh 退出码。**

💡 `backlog` 二进制在 `$HOME_DIR/.local/bin/backlog`，
非交互 ssh 的 PATH 里没有，直接敲 `backlog` 会 command not found。

## 部署到新机器

```bash
# 1. 单元
cp $PLATFORM_ROOT/platform/systemd/vcc-* ~/.config/systemd/user/
chmod +x ~/.config/systemd/user/vcc-healthcheck.sh
# 2. 开机自启（关键，默认为 no）
loginctl enable-linger $USER
# 3. 启用
systemctl --user daemon-reload
systemctl --user enable --now vcc-platform.target vcc-healthcheck.timer
# 4. 校验
systemd-analyze verify ~/.config/systemd/user/vcc-backlog.service
```

## 排错

| 症状 | 排查 |
|---|---|
| 端口不通但 is-active=active | 看 healthcheck.log，等 2 分钟或手动 `systemctl --user start vcc-healthcheck.service` |
| `systemctl stop` 收不掉进程 | 旧的手动进程不在 unit cgroup，按端口 kill：`P=\$(ss -tlnp\|grep ':8765 '\|grep -o 'pid=[0-9]*'\|cut -d= -f2); kill \$P` |
| 8765 报 `Errno 101` | `HF_ENDPOINT` 没设。systemd 单元里已设，手动启动必须带 |
| 8421 返 404 wiki not found | 先查 `x-tdai-service-id` 是否填错（真值 `<TEAM_SLUG>`，不是 team_id `vcc`） |
| git 命令在 NFS 上极慢 | 确认 `.git` 是文件不是目录（separate-git-dir 已配）；`cat .git` 看路径 |
| NFS 目录 git 提交慢 | 提交前确认 `$GIT_DIR` 存在，git 目录不在 NFS 上 |
