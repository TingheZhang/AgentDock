---
title: CHANGELOG - Platform
type: note
permalink: vcc-notes/projects/platform/changelog
tags:
- platform
- changelog
- history
---

# 平台变更日志 (Platform CHANGELOG)

> 记录针对平台架构、底层服务、代理网络、监控看板等维度的所有 Agent 改动。
> 规约要求：每次改动收工前必须按标准格式追加记录并提交至 memory 仓库。

## 格式规范

```markdown
### YYYY-MM-DD [Task-ID / 任务简述] by <Agent-ID>
- **背景与原因 (Context / Why)**: ...
- **改动清单 (What Changed)**: ...
- **验证结果 (Verification)**: ...
- **关联提交 (Git Commit)**: ...
```

---

## 历史变更记录

### 2026-10-06 [双网络 IP 适配与动态 Host 跳转] by <AGENT_ID>
- **背景与原因 (Context / Why)**:
  用户需要在局域网 IP (`<LAN_IP>`) 和 Tailscale IP (`<TAILSCALE_IP>`) 下均能无缝访问各看板及编辑任务，原平台代码硬编码了单个 IP，且端口代理未绑定双网。
- **改动清单 (What Changed)**:
  1. `ports.json`: `bind_ip` 改为 `"<LAN_IP>,<TAILSCALE_IP>"` 双 IP 列表。
  2. `kb-proxy.py`: 改造 `DualBoundThreadingHTTPServer`，支持利用多 socket fd 同时监听两个 IP 接口，解决 Linux 下与 127.0.0.1 冲突而无法使用 0.0.0.0 的问题。
  3. `bb-ports.sh`: raw relay 同样支持遍历多 IP 启动并监听；探活逻辑统一截取主 IP 探测。
  4. `bb-aggregate.py`: 修复 4 处 `@CLOSE@` 语法遗留，在前端注入基于 `window.location.hostname` 的动态 host 适配脚本，看板卡片跳转与端口映射自适应当前访问域名。
  5. `vcc-readonly.py`: 修复前端 HOST 硬编码，动态取当前访问 host，使注入知识面板在双网下均能正常通信。
- **验证结果 (Verification)**:
  - 局域网（<LAN_IP>）与 Tailscale（<TAILSCALE_IP>）全端口 (6420, 6421, 6422, 6423, 6424) 现场 curl 探测，10 个端点 100% 返回 HTTP 200。
  - 双网络下访问 6421 聚合看板及 6420/6422/6423 项目面板均正常展示且跳转正常。
- **关联提交 (Git Commit)**:
  - `platform`: `967d203`

### 2026-10-05 [P0 缺陷修复：Supervisor 隔离自愈与级联重启消除] by <AGENT_ID>
- **背景与原因 (Context / Why)**:
  原 `vcc-backlog-supervise.sh` 检测到任意单项目故障时触发全局 `exit 1`，导致 systemd 重启整个 `vcc-backlog.service`，使得全平台所有端口下线约 25 秒（级联瘫痪）。
- **改动清单 (What Changed)**:
  1. `vcc-backlog-supervise.sh`: 改为常驻守护进程，拆解为细粒度单项目探测（要求 HTTP 200 + upstream 监听），消除全局 `exit 1`。
  2. 新增靶向自愈逻辑 `heal_project()` 与 `heal_dashboard()`，带 3 次失败冷却 60 秒的熔断机制，仅重启故障的单个项目进程与代理。
  3. 优化关闭 trap 信号处理，确保外部主动退出时平稳清理子进程。
- **验证结果 (Verification)**:
  - 故障注入演练：`kill -9` 杀死 6422 端口 upstream 进程，Supervisor 在 11.2 秒内完成靶向重启，恢复 HTTP 200。
  - 在此期间，其余 4 个端口（6420, 6421, 6423, 6424）全程 100% 保持可用，零抖动。
- **关联提交 (Git Commit)**:
  - `platform`: `008afcf`

### 2026-10-06 [wiki 纳入 git 纳管 + 陈旧页机制根治] by CodeBuddy Agent
- **背景与原因 (Context / Why)**:
  1. wiki 目录完全不在版本控制中，ingest 覆盖只能靠 `wiki_sync.py backup` 手工回滚，错误知识无法追溯引入时间与责任人。
  2. 发现二次根因：上一轮存储层纠错（`a015686`）写的是指代歧义句「该路径本身才是 NFS」，LLM 摘要层忠实继承，产出「平台仓库位于 NFS 路径 `<legacy-projects-path>`」的错误实体页。成因链可复现：`歧义源 → LLM 继承 → 错误实体页 → ingest 不重写 → 长期驻留`。
  3. 进一步查明 `wiki/log.md` 显示 `wrote 34 pages` 而总页数 57 —— **ingest 是增量合并，不重写未命中的页面**，源改对了旧页仍长期驻留。
- **改动清单 (What Changed)**:
  1. `$PLATFORM_ROOT/wiki` 建为**独立 git 仓库**（首次提交 `4a44572`）：跟踪 `raw/sources/*.md`（真源）、`wiki/**/*.md`（61 个摘要页）、`_wiki_engines/wiki-sources.json`；`.gitignore` 排除 `*.db` / `*.db-wal` / `*.db-shm`。
  2. 排除数据库的依据（实测）：`index.db` 由 MemoryKnowledge 8421 读写，每次 ingest 整体重写（729088 → 835584 字节）；`knowledge.db-wal` 静置 6s 无变化但读取后 mtime 立即更新。含库 1.6MB → 排除后 63.5KB。
  3. 笔记 `2e6714c`：消除指代歧义，改为逐路径显式列举 + 附 `df -Th` 核对命令。
  4. wiki 源新增「纠错文本不能用指代」与「wiki 已纳入 git」两节，并**显式点名两处陈旧页**要求重建时一并改写。
  5. `platform/AGENTS.md`（`c11e209`）：修正布局节把 wiki 误描述为 "knowledge.db — the LIVE wiki database"（真库其实是 `index.db`）、NFS 倍率 100~700 倍 → ~200 倍、新增 wiki 仓库工作流小节。
  6. `platform-core/AGENTS.md`：布局表加「In git」列，标出 platform-core 无 git 而 wiki 为独立仓库。
- **验证结果 (Verification)**:
  - **陈旧页已改写**：version 3(57) → 5(59) → **6(61) 页**；`entities/nfs-mnt-share.md` 与 `entities/本地盘-data.md` mtime 由 13:55 更新为 14:49。
  - **残留扫描负控**：全 wiki grep 命中 9 条，逐条判定全为合规内容（纠错记录本身、链接列表、核对命令）；负控样本「平台仓库在 <legacy-projects-path>（NFS）」未标注时命中数 1，证明过滤器未失效。
  - **gitignore 负控**：`git check-ignore` 逐文件验证 5 个数据库文件全部忽略、3 类内容未被误忽略；`git diff --cached` 中 `.db` 文件数 = 0。
  - **git 功能实测**：改动摘要页 → `git diff --stat` 检出 2 insertions → `git checkout --` 回滚后与原文一致；`touch knowledge.db-wal` 后工作区仍干净。
  - **盘位核对**：`df -Th $PLATFORM_ROOT/wiki` → `/dev/sdb ext4`，符合铁律 9。
  - **五端口**：公网 IP 与 127.0.0.1 双路全 200。
  - **补丁幂等**：笔记与两份契约的补丁连跑两次均输出 `ALREADY`，字节数不变。
  - **健康检查**：`health_check --deep` → `ok=True, problems=[]`。
- **关联提交 (Git Commit)**:
  - `wiki`: `4a44572`（首次入库）
  - `platform`: `c11e209`（AGENTS.md 更新）
  - `memory`: `2e6714c`（歧义句修正）

### 2026-10-06 [四个上游组件使用教程 + 6 处错误认知更正] by CodeBuddy Agent
- **背景与原因 (Context / Why)**:
  平台四个组件均来自 GitHub 上游，但既有笔记只零散记录了几个坑，缺少成体系的教程；
  更严重的是**既有知识里有 6 处与实际不符的描述**，会让后续 agent 踩空。
- **改动清单 (What Changed)**:
  1. wiki 源新增「三之二、四个上游组件的使用教程（2026-10-06 实测核实）」，
     15045B → 26817B，六节：每个组件的端口/路径/必填参数/CLI 命令/限制，
     附协作关系图与「本节更正过的错误认知」对照表。
  2. 新增笔记 `projects/platform/上游组件参考.md`（8784B），归属 platform-core。
  3. 铁律 14 补上`write_note` 的真实必填参数。
- **本次推翻的 6 处错误认知**（均经实测或负控）:
  1. 错误 `service_id` 返回 **400 `wiki_id is required`**，不是 404 —— 缺鉴权头时
     请求仍进应用，先被参数校验层挡住。`/v3/code-graph/status` 才返回 401。
  2. `/v3/wiki/get` 在只读白名单，**错误 Bearer key 仍可能 200**，不能用作负控。
  3. `write_note` 必填 **`content` + `directory` + `title`** 三个参数，
     少 `directory` 直接 422 —— 此前以为很简单。
  4. **代码图谱不来自 8421 code-graph API**：该 API 返回 `items:[]`，
     `state/codebase-memory/` 下是 `*.db.pre-migration`（迁移未完成）；
     6424 展示的是自研预渲染 HTML 快照。**两条独立路径。**
  5. `backlog task` 子命令**没有 `status`**，状态通过 `edit -s` 改。
  6. `backlog browser` 只监听 127.0.0.1，**VPN 访问不到**（本平台不用它）。
- **验证结果 (Verification)**:
  - 探测手段为实测而非文档转述：`git remote -v`（确认远端是
    `TencentDB-Agent-Memory` **大写 TDAM**）、`openapi.yaml`（28 端点 =
    wiki 14 + code-graph 14）、运行时 MCP `tools/list`（21 工具的真实
    `inputSchema` 必填参数）、`backlog --help` + `task --help`（命令全集）。
  - ingest：version 7 (65 页) → **version 8 (75 页)**，摘要层把每个坑独立成页
    （`concepts/write-note-必填三参数.md` 等 6 个 + 4 个实体页）。
  - **真实端点验证**：6424 `/api/knowledge?project=platform-core` 显示
    `wikis: [{wiki_id: <WIKI_ID>, status: ready, page_count: 75}]`，
    笔记 5 篇含新增的 `上游组件参考.md`。
  - 五端口 127.0.0.1 全 200；`health_check --deep` → `ok=True, problems=[]`，
    notes disk=11 claimed=10 unassigned=1。
- **关联提交 (Git Commit)**:
  - `wiki`: `7e0a51e`（ingest v8，79 文件 / 0 个 db）
  - `memory`: `c1c5cb6`（新增上游组件参考笔记）

### 2026-10-06 [Basic Memory 重复实体页收敛] by CodeBuddy Agent
- **背景与原因 (Context / Why)**:
  发现 wiki 中同一服务存在两个实体页：规范页 `basic-memory-8765.md`（21 工具、
  必填三参数，内容完整）与历史页 `basic-memory-mcp-8765.md`（仅 4 个工具名、
  内容过时）。后者有 11 处入链，读者会被引向过时副本。
- **改动清单 (What Changed)**:
  1. 源 `ops-manual.md` 新增铁律 14.1，确立规范页名，27211B → 29603B。
  2. 13 个 wiki 页面中 22 处 wikilink 归一为 `[[Basic Memory（8765）]]`。
  3. 保留说明性文字与 `index.md` 第 16 行的废弃别名标注 —— 它们是问题的事实
     记录，不是活链接。
- **过程中被推翻的两次做法**（均实测，非推断）:
  1. **「在源里点名要求重写旧页」无效**：ingest 未改写旧页（mtime 停在 14:51），
     反而**新建**了同义页。page_count 75→79。
  2. **「在源里列出待改指页面清单」也无效**：三轮 ingest 后旧页入链
     11 → 19 → 22，**不降反增**。反直觉成因：在源里反复写旧页名，
     给了 ingest 重建旧页的理由。**提及度 ≠ 链接可解析性。**
  3. 结论：不在这两条路上继续投入，改用直接改写链接（wiki 已入 git，可回滚）。
- **验证结果 (Verification)**:
  - 残留活链接 **22 → 0**；四个核心链接（Basic Memory / Backlog.md /
    codebase-memory-mcp / TencentDB Agent Memory）均实测可解析到实际文件。
  - ingest version 8 → 10，page_count 75 → **80**，`pages=80` 与元数据一致。
  - `health_check --deep` → **`VCC health: OK`**，14 项探测全 200。
  - 备份：`state/wiki-backup-basicmem-20261006-162451`（436K）。
- **顺带确认的一处误报（非缺陷）**:
  ingest 期间 `page_count>0` 而 `pages=[]`，`health_check --deep` 报
  `metadata has pages but page list is empty`。实测 12 次轮询约 2 分钟后
  转 `ready 80 80`，错误自行消失。**该检查项未排除 `processing` 状态，
  是检查器的小缺陷，但不应据此去「修」wiki。**
- **关联提交 (Git Commit)**:
  - `wiki`: `dfea4c6`（ingest v10）、`3479ebf`（链接收敛）

### 2026-10-06 [kb-proxy WebSocket 透传：消除公网看板 "Server disconnected" 红条] by CodeBuddy Agent
- **背景与原因 (Context / Why)**:
  用户截图报 6423 公网看板顶部常驻红色横幅 `Server disconnected`。初判为
  服务不可用，实测五端口双路全 200、`/board` 2.3ms —— **页面能开不等于
  每个功能都通**，前端报错条被误当成服务挂了。
  决定性判据不是 HTTP 状态码，而是**问 WebSocket 升级**：
  `127.0.0.1:6423`（原生 backlog）返 **101**，`<LAN_IP>:6423`
  （kb-proxy）返 **404**。
- **定位过程（含一处被实测推翻的判断）**:
  1. 原计划改 `vcc-readonly.py` 前端做降级轮询，`grep "WebSocket|ws://|
     onclose"` **零命中** —— 落点错了。
  2. `grep -rl "Server disconnected" $HOME_DIR/.local` 命中
     `~/.local/bin/backlog`（ELF 64-bit，非 stripped）：红条由 Backlog.md
     自带的 `useHealthCheck` 渲染，每 5s 重连，连的是**站点根路径 `/`**。
     代码打包在上游二进制里，**不在本仓库**，改不了。
  3. 结论：方案 2 意图仍可达成，落点从「改前端」改为「让代理支持 WS」，
     上游 WS 不断即可。**不动端口拓扑。**
- **改动清单 (What Changed)**: `kb-proxy.py` 8873B → 15173B
  1. `_is_ws_upgrade()`：要求 `Connection: Upgrade` **与**
     `Upgrade: websocket` **同时**存在（RFC 6455 §4.1）。
  2. `_headers_ws()`：`Connection`/`Upgrade` 原样转发，不再过
     `_headers_forwardable()`；**Host 改写**为 `127.0.0.1:<upstream_port>`
     而非删除。
  3. `_proxy_ws()`：裸 socket 握手原样转发（无需推导 accept-key），
     之后每方向一个 pump 线程；socket 由持有它的线程 shutdown，
     两个 pump 都 join 后显式 close 一次。
  4. `server_version` 1.0 → 1.1。
- **过程中被实测推翻的两处**（均为「先怀疑自己」）:
  1. **「代理已复制 Upgrade 头」是错的**：`HOP_BY_HOP` 第 79 行本就含
     `"upgrade"`，被显式排除。但这反而让根因更清楚 —— 问题不在头过滤，
     而在通用路径交给 `http.client`，其 `resp.read()` 吃掉 101 body
     并关连接，帧根本没机会流动。**转发请求头不够，必须让响应也升级
     且保持连接。**
  2. **fd 泄漏（真实设计缺陷）**：首版在 `finally` 里对 `src`+`dst` 双双
     `shutdown`，50 次连接即 `Too many open files`。改为只 shutdown
     `dst`，并在两 pump join 后显式 `client.close()`。
- **验证结果 (Verification)**:
  - **公网侧 WS 升级 404 → 101**，6420/6422/6423 三端口全 101，
    与 `127.0.0.1` 及第二 bind 地址 `<TAILSCALE_IP>` 一致。
  - **50/50 连接同时拿到 101 与数据帧**（`pong`），与原生逐字节一致；
    fd **5 → 5** 持平，`Too many open files` 出现 **0** 次。
  - **负控**：普通 GET、只带 `Connection`、只带 `Upgrade`、
    `Connection: keep-alive` 四种均走普通路径返 **200**（未误判为握手）。
  - 面板注入未受影响：三端口 `vk-panel` + 项目名均在；`/board` 200 / 1.9ms。
  - 经代理的 `/api/knowledge` 404 是**原生 board 行为**（原生同样 404，
    纯文本 `Not Found` = 未进应用），面板数据源在 6424 返 200，非回归。
  - `health_check.py --deep` → **`VCC health: OK`**，15 项探测全 200。
- **部署与回滚**:
  - 备份 `kb-proxy.py.bak-ws-20261006-163716`（8873B，md5 `e9aa5bc3712a`）。
  - 三代理按**监听端口反查 PID** 精确重启（无 systemd 托管，
    `vcc-backlog-supervise.sh` 不含 kb-proxy），轮询到 HTTP 200 就绪
    分别耗时 4s / 14s / 6s。
- **关联提交 (Git Commit)**:
  - `platform`: `1aa231e`（kb-proxy 151 insertions / 1 deletion）


### 2026-10-06 [看板↔聚合页双向导航] by CodeBuddy Agent
- **背景与原因 (Context / Why)**:
  用户反馈 6422 看板页「回不到聚合界面」。实测确认这是**导航单向**：
  6421 的项目卡片整体是一个 `<a>`（`target=_blank`），卡片内不能再嵌套
  `<a>`（HTML 非法且会破坏布局），所以卡片无法兼作「返回」入口；
  而看板页本身也没有任何指向 6421 的链接。**唯一出路是地址栏或浏览器历史。**
- **改动清单 (What Changed)**: 2 文件 +91/-1
  1. `bb-aggregate.py`：标题下新增导航条，链接聚合页自身 + 全部项目看板
     （`model["sources"][*]["uiPort"]`）+ `extra_links`。
     **渲染为独立块而非塞进卡片**，理由同上（不能嵌套锚点）。
  2. `vcc-readonly.py`：新增 `dashboard_port()` 读 `ports.json`；注入面板
     底部加「← 返回聚合页」。注册表不可读或缺 `dashboard_port` 时返回
     `None`，此时**省略该链接而不是指向错误位置**。
  3. 面板链接用**绝对地址**，与既有笔记链接同理：这段 HTML 被注入到
     另一 origin 的看板页，相对地址会解析到看板自身而 404。
- **过程中被实测推翻的三处**（均为「先怀疑断言」）:
  1. **导航条第一次只生成了 2 个链接**：我用了 `ui_port`（snake_case），
     但 `render_html` 用的键是 `uiPort`（camelCase，由 `read_one_source`
     转换而来）。`load_sources` 返回的是注册表原始 dict，**两套键名并存**。
  2. **负控第一次全报 0**：`refresh_minutes=0` 时没有 `<meta>`，而我的正则
     要求 `</div>\s*<meta`，匹配不上 —— 测试架错了，产品代码没错。
  3. **负控「去掉 ui_port 后仍有 5 个链接」**：`render_html` 第 444 行
     `sources = model["sources"]` **覆盖了同名参数**，我改位置参数没用；
     且 `source` 是类实例，`dict(s, uiPort=None)` 会丢键，须用 `copy.copy`。
  另有一处判据过宽：`"vk-back" in h` 命中的是 **CSS 规则文本**而非 `<a>` 标签，
  负控因此误报；精确判据是 `<a class="vk-back"`。
- **验证结果 (Verification)**:
  - 聚合页导航条 **5 条链接，逐条 fetch 真实端点全 HTTP 200**
    （6421/6420/6422/6423/6424）。
  - 导航条负控 4 项全 PASS：去 `uiPort`→2 条、去 `dashboard_port`→4 条、
    数据全空→不产生 navbar、去 `extra_links`→4 条。
  - 面板负控 3 项全 PASS：注册表不存在→无链接、缺 `dashboard_port`→无链接、
    **测试注册表改 `dashboard_port: 9999` → 链接跟着变 9999（证明非硬编码）**。
  - 端到端经代理：三看板页均含 `<a class="vk-back" href="...:6421/">`。
  - 幂等：`refresh` 连跑 3 次，**除时间戳外字节完全一致**；旧版脚本亦然，
    故非本次引入。
  - 走生成入口 `bb-ports.sh refresh`，未手写 `--out`。
  - `health_check --deep` → **`VCC health: OK`**，五单元全 active。
- **过程中被纠正的一处操作错误**:
  重启 6424 时我先 kill 进程再 `nohup` 手工拉起，结果 `health_check` 报
  `units/vcc-readonly.service: expected active/running, got inactive/dead`。
  根因：**6424 由 user 级 systemd 单元托管**，手工 nohup 会让单元保持
  inactive。改正：`systemctl --user restart vcc-readonly.service`。
  （`kb-proxy` 确实**无**单元，手工 nohup 是正确方式。）
- **关联提交 (Git Commit)**:
  - `platform`: `7cec86a`（bb-aggregate.py +vcc-readonly.py，+91/-1）


### 2026-10-06 [回滚：看板↔聚合页双向导航 7cec86a] by CodeBuddy Agent
- **回滚原因**:
  用户反馈「修复有问题」，具体两点：**看板页面被破坏**、**task 页面仍然没有返回入口**。
  根因是上一轮的设计判断错误：我把「返回聚合页」链接加进了
  **注入的知识面板**底部，但该面板在看板页上**本来就不可见**
  （上一轮已查明：它被注入在 `<div id="root">` 之后作兄弟节点，
  看板又是全屏布局，面板被挤出视口）。
  **等于把返回入口放到了一个没人看得见的地方。**
  我当时选择了「面板可见性先不修」，却没有把「面板不可见」对
  「返回链接」的影响纳入判断 —— 两个决定叠加起来，等于产出了一个无效修复。
- **回滚方式**: `git revert 7cec86a` → **`5914829`**
  （用 revert 而非 reset：共享仓库不改写历史，原提交与回滚都可追溯）
- **回滚后验证**:
  - `bb-aggregate.py` md5 `0046d07e00eac10e10358d88c4b051db`、
    `vcc-readonly.py` md5 `203ff5ea685214dcbfff75c443839e89`
    —— **与 7cec86a 之前逐字节一致**。
  - 重新生成聚合页：产物回到 **8527B**（`navbar` 出现 **0** 次，
    项目卡片与 4 条入口链接完好）。
  - `/api/panel` 与三看板页：`vk-back` 出现 **0** 次；
    `vk-panel` 仍 25 次（面板注入未受影响）。
  - 五端口双路全 200；WS 升级三看板仍 **101**（红条修复保留）。
  - 五单元全 active；`health_check --deep` → **`VCC health: OK`**。
- **保留的教训（比这次修复本身更重要）**:
  1. **改前先确认「改动落点用户看得见吗」**。面板不可见时，往面板里加任何
     功能都是白做。「先不修 X」与「在 X 里加 Y」不能并存 —— 后者依赖前者。
  2. **`grep -c` 验注入 ≠ 验可见**。面板 HTML 有 25 处 `vk-panel` 却看不见，
     我据此报告过「面板注入正常」，被用户截图当场推翻。
  3. **用户说「有问题」时，先问清现象再回滚**。本次先问了「布局错位 /
     链接不好用 / 页面被破坏」，才定位到是「落点不可见」而非「样式难看」。
- **后续（未做，等用户决定）**:
  若要真正解决「看板页回不到聚合页」，落点应在**看板页顶部固定条**
  （`position:fixed` 的悬浮返回按钮）或**修好面板的可见性**，
  而不是往不可见面板里塞链接。
- **关联提交 (Git Commit)**:
  - `platform`: `5914829`（revert 7cec86a，-91/+1）


---

## 6424 总览页：补齐 6421 形态的统计条与状态标记（`8421308`，+121/-15）

### 需求与实际差距
用户要求「6424 那样的页面，但进去先是 6421 那样的 projects overview，
点项目才进 6424 现有 project overview」。

**实测发现三项要求 6424 本来就满足**：默认路由已是 `/projects` → `projectsHome()`，
卡片标题已链到 `#/project/<name>`，左侧栏 `projectRail` 已在 `projectShell()` 里。
真正差距只有**信息密度**：卡片只有「任务 N」一个计数。

### 根因：两边取任务数据的深度不同
| | 6421 `bb-aggregate.py` | 6424 改前 |
|---|---|---|
| 取任务 | `task list --json` + 每任务 `task view --json` | 只 `task list --json` |
| 字段 | 20 个（含 `readiness.isBlocked`） | 4 个 |
| 卡片 | tasks / AC / blocked / done | 只有任务数 |

实测 `task list --json`：`readiness = null`（**无 isBlocked**），
但 `isReady` 与 `acceptanceCriteriaCompleted/Count` **都有**。

### 改动
- `_tasks_for_project_uncached` 补 `acDone/acTotal/isReady/dependencies`
  —— **全部来自已有的同一次调用，0 新增 subprocess**，耗时 0.81s → 0.85s
- 新增 `rollup()`：客户端派生统计的纯函数，与卡片同源，不会自相矛盾
- 新增 `statsBar()`：`projects ok` / `tasks` / `declared done` / `waiting on deps`
- `projectCard()` 分组渲染 + AC 进度条；看板端口改**独立小链接**，
  卡片不再整块包 `<a>`（否则端口链接不可点）
- `isReady` **只认显式 false**，null/缺失一律不计为阻塞
- 6421 按决策**保留不动**；blocked 标记**不做**（需 13 个额外 subprocess，
  实测 3 项目均为 0）

### 生产验收（真实 Chrome headless 渲染，非 grep 源码）
- 首屏统计条 4 格 = **3 / 3 · 13 · 1 · 1**
- 3 张卡片分组渲染；Platform Core 显示 `AC 8/41 · 1 declared done · 1 waiting`
- 点卡片进 project overview，5 项导航齐全、侧栏 `on` 高亮跟随
- manage / tasks / notes / wiki 无回归；五端口双路 200；WS 仍 101
- 备份：`vcc-readonly.py.bak-overview-20261006-205252`
- **负控**：改数据后统计从 `13|1|1` → `6|0|0`，证明非硬编码

### 教训
1. **先确认需求是否已满足**。差点去「搬」一个已经在位的东西。
2. **「JS 渲染的页面」必须在剥掉 `<script>` 后断言** —— 源码里的模板字面量
   会被当成渲染产物（本轮让统计条数成 5、卡片数成 5，真实是 4 和 3）。
3. **恒真断言改完还会再犯**。修掉 `or True` 后又写了
   `is not True or ...` —— 还是恒真。最后靠**负控组**（空串/截断/错页面 8 FAIL）
   才证明判据真有鉴别力。
4. **远端 `pkill -f` / `pgrep -af` 会匹配到 ssh 命令行自身**，导致会话 SIGTERM。
   正解：`ss -tlnp | grep ":PORT " | grep -o "pid=[0-9]*"`。
5. **本机 headless Chrome 取远程 DOM 要清 `HTTP_PROXY`**，否则本地隧道被代理吞掉，
   症状与「服务挂了」完全一样。

- **关联提交 (Git Commit)**:
  - `platform`: `8421308`
\n\n
## 2026-10-06 [TASK-2/TASK-3/TASK-4/TASK-6 / 项目知识入口与运维文档纠正] by luna-worker

- **背景与原因 (Context / Why)**: 浏览器发现 6423 看板仍使用旧的 project query 全局入口和 API 直链；面板缓存使 reader 已更新但真实 board 未更新。运维规则还保留全量重启、DNS/PATH 和项目漂移的过时或绝对表述。
- **改动清单 (What Changed)**:
  - 6424 面板入口改为项目 scoped #/knowledge/p/<project>；笔记/Wiki 改为 #/notes/p/<project>/... 与 #/wiki/p/<project>/...?wiki=...。
  - 增加固定项目知识入口按钮和 sticky 面板头；代码图谱标注静态快照及文件更新时间。
  - 更新 platform AGENTS、运维铁律和多 agent 规约，保留历史语境并修正单双 IP、DNS、PATH、隔离恢复和运行时漂移表述。
  - platform-core 验收文档补充 TASK-1 跨仓库证据边界及 TASK-2–7 awaiting acceptance 规则。
- **验证结果 (Verification)**:
  - vcc-readonly.service 重启后 /api/panel?project=platform-core 返回 HTTP 200。
  - 三个 board proxy 逐项刷新后，http://<TAILSCALE_IP>:6423/ 实际包含 vk-float、项目 scoped note/Wiki 路由；6420/6422 同样通过；三板及 6424 HTTP 200。
  - 浏览器已实际点击 6423 面板入口、CHANGELOG、Wiki index 和 TASK-2 弹窗，均进入项目 scoped reader；代码图谱显示快照标记。
  - health_check --deep 在 Wiki ingest 前正确报告平台笔记晚于 Wiki sync；待本条日志提交后执行同步并复验。
- **关联提交 (Git Commit)**:
  - platform: 759d424（reader scoped routes, panel visibility, docs）
  - memory: 8d85057（本条目首次提交）


## 2026-10-06 [TASK-2/TASK-4 / Wiki 同步与旧别名列表过滤] by luna-worker

- **背景与原因 (Context / Why)**: 浏览器验收通过后，Wiki 必须吸收本轮运维校正；Basic Memory 旧页有明确的“历史残留别名/已废弃别名”标记，可从普通列表隐藏但保留直接读取。
- **改动清单 (What Changed)**:
  - 保留原始 Wiki source 内容，仅在 ops-manual.md 追加当前部署校正并提交 wiki。
  - 普通 Wiki 列表隐藏明确标记的 basic-memory-mcp-8765；直接 page route/API 仍可访问。
- **验证结果 (Verification)**:
  - Wiki source commit 9ee7a4f；POST /v3/wiki/ingest 返回 202，随后状态 ready，page_count=80，last_sync_at=2026-10-06T13:30:31.436Z。
  - 6424 reader py_compile、HTTP 200 和项目 scoped 路由复验通过；health deep 将在本条日志同步后复验。
- **关联提交 (Git Commit)**:
  - platform: 2b3546e（仅列表过滤）
  - wiki: 9ee7a4f（source 追加，未覆盖原内容）
  - memory: dfb65b2（本条目首次提交）
\n
    
## 2026-10-06 [TASK-2–TASK-7 / awaiting_acceptance 标签可见性] by luna-worker

- **背景与原因 (Context / Why)**: 浏览器验收发现标签默认只显示前几项，待验收状态被折叠为 +1，历史证据边界也不易见。
- **改动清单 (What Changed)**: 通过 Backlog CLI 将 awaiting_acceptance 放到 TASK-2–TASK-7 标签首位，将 historical-evidence-boundary 放到 TASK-1 标签首位；保留其余标签顺序与全部任务状态。
- **验证结果 (Verification)**: TASK-1 仍为 Done；TASK-2–TASK-7 仍为 To Do；逐项 backlog task view --json 已确认首标签顺序。
- **关联提交 (Git Commit)**: platform-core 无 git；状态由 Backlog CLI 写入，验收记录位于 docs/acceptance-2026-10-06.md。

## 2026-10-06 [TASK-2/TASK-4 / 项目代码图谱入口归位] by luna-worker

- **背景与原因 (Context / Why)**: 项目侧栏重复显示代码图谱链接，且项目概述/知识页缺少与当前项目绑定的图谱入口。
- **改动清单 (What Changed)**: 移除三项目侧栏中的重复图谱链接；在 `#/project/<project>` 与 `#/knowledge/p/<project>` 内容底部增加“代码图谱”卡片，使用当前 reader host 的 `/api/codegraph?name=<project>` 链接，并明确这是项目静态快照而非 TASK-ID 独立图谱。
- **验证结果 (Verification)**: `vcc-readonly.service` 重启后 active；reader Python 编译、`git diff --check` 通过；`GET http://127.0.0.1:6424/api/codegraph?name=platform-core` 返回 200。浏览器验收 URL：`http://<TAILSCALE_IP>:6424/#/project/platform-core`、`http://<TAILSCALE_IP>:6424/#/knowledge/p/platform-core`。
- **关联提交 (Git Commit)**:
  - platform: 2eeded1（reader 项目内容图谱入口）
  - memory: 本条目提交后生成

