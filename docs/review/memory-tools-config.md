# 三个记忆工具的完整配置（174 服务器）

> 2026-10-05 实测。三套工具都已跑通，配置可直接粘进 agent。

---

## 总览：三层分工

| 层 | 工具 | 端口 | 存什么 | 传输 |
|---|---|---|---|---|
| 进度 | Backlog.md | 6420/6421/6422 | 任务卡 Markdown | HTTP（网页） |
| 知识/笔记 | Basic Memory | 8765 | 人工笔记 + 语义搜索 | HTTP（推荐） |
| 自动知识抽取 | **TencentDB MemoryKnowledge** | **8421** | **LLM 从文档自动生成 Wiki** | HTTP |
| 代码图谱 | codebase-memory-mcp | 无（stdio） | AST 调用关系 | SSH |

**TencentDB 补的是前三个都没有的能力：用 LLM 把一堆文档自动变成结构化 Wiki。**

---

## 1. TencentDB MemoryKnowledge（新装，最有价值）

### 服务信息

| 项 | 值 |
|---|---|
| 端点 | `http://<LAN_IP>:8421/v3`（健康检查 `/health`） |
| 源码 | `$TDAM_DIR/` |
| 数据 | `$PLATFORM_ROOT/wiki/` |
| 数据库 | `$PLATFORM_ROOT/wiki/knowledge.db`（5 张表） |
| LLM | `http://<PRIVATE_IP>:8787/v1` / `deepseek-v4.1-flash` |
| 管理脚本 | `$HOME_DIR/ks.sh {start\|stop\|restart\|status\|logs}` |
| Node | `$NODE_BIN`（v22.14.0，**系统 node 仍是 v12，未动**） |

### 鉴权（已启用）

服务有 `KNOWLEDGE_SERVICE_KEY`，**写操作必须带 Bearer token**：

```bash
# 读取 key
ssh <USER>@<LAN_IP> \
  "grep '^KNOWLEDGE_SERVICE_KEY=' $TDAM_DIR/.env | cut -d= -f2"
```

- **写/管理端点**（`create` / `raw/write` / `ingest` / `delete` / `page/write` 等）：需 `Authorization: Bearer <KEY>`
- **只读查询**（`get` / `list` / `raw/ls` / `page/ls` / `search`）：**免鉴权**，可直接给 agent 用

实测（2026-10-05）：写无 key → **401**；写带 key → **201**；只读无 key → **200**。

### 必带的请求头

```
x-tdai-service-id: <TEAM_SLUG>      ← service_id，不是 team_id！填错返 404
Content-Type: application/json
```

`service_id` 走 header，**不在 body 里**。body 里必须有 `team_id`。
本平台当前值：`service_id=<TEAM_SLUG>` / `team_id=<TEAM_ID>`。

### 四步工作流（实测通过）

```bash
KEY=$(ssh <USER>@<LAN_IP> "grep '^KNOWLEDGE_SERVICE_KEY=' $TDAM_DIR/.env | cut -d= -f2")
H='-H x-tdai-service-id:<TEAM_SLUG> -H Content-Type:application/json'
AUTH="Authorization: Bearer $KEY"
```

**① 建 wiki**
```bash
curl -sS -X POST http://<LAN_IP>:8421/v3/wiki/create \
  -H "x-tdai-service-id: <TEAM_SLUG>" -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"team_id": "<TEAM_ID>","user_id":"<USER>","name":"实验笔记"}'
# 返回 wiki_id，形如 <WIKI_ID>
```

**② 写源文件**（⚠️ 三个坑，见下）
```bash
curl -sS -X POST http://<LAN_IP>:8421/v3/wiki/raw/write \
  -H "x-tdai-service-id: <TEAM_SLUG>" -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"wiki_id":"wiki-xxx","team_id": "<TEAM_ID>",
       "files":[{"filename":"a.md","content":"# 标题\n内容"}]}'
```

**③ 触发 LLM 生成**（异步，立刻返回 pending）
```bash
curl -sS -X POST http://<LAN_IP>:8421/v3/wiki/ingest \
  -H "x-tdai-service-id: <TEAM_SLUG>" -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"wiki_id":"wiki-xxx","team_id": "<TEAM_ID>"}'
```

**④ 轮询状态**（实测约 20 秒完成）
```bash
curl -sS -X POST http://<LAN_IP>:8421/v3/wiki/get \
  -H "x-tdai-service-id: <TEAM_SLUG>" -H "Content-Type: application/json" \
  -d '{"wiki_id":"wiki-xxx","team_id": "<TEAM_ID>"}'
# status: draft → processing → ready，page_count 就是生成页数
```

### ⚠️ 四个实测踩到的坑

1. **`files` 必须是数组**，不是单个对象
   → 报 `files is required (non-empty array)`

2. **`filename` 不要自带 `sources/` 前缀**
   → 报 `ENOENT .../raw/sources/sources/xxx.md`（服务端会自己加）
   正确：`"filename":"a.md"`；错误：`"filename":"sources/a.md"`

3. **`raw/write` 和 `ingest` 的 body 都必须有 `team_id`**
   → 报 `x-tdai-service-id header and team_id are required`

4. 🔴 **`x-tdai-service-id` 填的是 `service_id`，不是 `team_id`**
   → 填错**不报鉴权错，报 404 `wiki not found`**，极易误判为「wiki 被删了」
   查真值：
   ```bash
   sqlite3 -header -column $PLATFORM_ROOT/wiki/knowledge.db \
     'select wiki_id, service_id, team_id, status, page_count from knowledge_wiki;'
   # wiki_id=<WIKI_ID>  service_id=<TEAM_SLUG>  team_id=<TEAM_ID>
   ```
   **本平台约定：service_id = `<team>-team`，team_id = `<team>`。**

**排错口诀**：401/400 = 鉴权或参数；**404 = 先怀疑 `x-tdai-service-id` 填错**，别急着重建 wiki。
服务重启时日志会打印 `Restored wiki index {"name":"...","pageCount":13}`，能看到说明数据没丢。

限制：单文件 ≤ **512KB**，一次最多 **10 个**，总计 ≤ **5MB**。

### LLM 生成效果（实测）

198 字节的源文件 → 生成 **13 个 Wiki 页面**：

```
wiki/
├── overview.md              ← 全局综述，带 [[双链]]
├── purpose.md / schema.md / index.md / log.md
├── entities/                ← 实体页：174 / dashboard / 本地盘-data / nfs-mnt-share
├── concepts/                ← 概念页：hub-agent-部署模型 / 存储分层约定 / 看板端口分工
└── sources/                 ← 源文件镜像
```

自动产出**双链**（`[[看板端口分工]]`）、**表格**、**frontmatter tags**，
且能识别"哪些是实体、哪些是约定"。这是手写 Markdown 做不到的。

### 全部 28 个端点

`/wiki/`：`create get list ingest delete raw/ls raw/read raw/write raw/rm
page/ls page/read page/write page/rm graph search`
`/code-graph/`：`create list get sync delete search explore callers callees
impact node status files`

完整定义：`curl http://<LAN_IP>:8421/openapi.json`
（⚠️ 返回的是 **YAML 不是 JSON**，用 grep 解析，别丢给 json.load）

### 卸载/回滚

```bash
ssh <USER>@<LAN_IP> '$HOME_DIR/ks.sh stop && rm -rf $HOME_DIR/tdbam $PLATFORM_ROOT/wiki $HOME_DIR/node22'
```

---

## 2. Basic Memory（笔记 + 语义搜索）

| 项 | 值 |
|---|---|
| 端点 | `http://<LAN_IP>:8765/mcp`（21 个工具） |
| 二进制 | `$HOME_DIR/miniconda3/envs/basic-memory/bin/basic-memory` |
| 项目 | `vcc-notes` → `$PLATFORM_ROOT/memory` |
| 索引库 | `~/.basic-memory/memory.db` |

```json
{
  "mcpServers": {
    "basic-memory": {
      "type": "http",
      "url": "http://<LAN_IP>:8765/mcp"
    }
  }
}
```

**⚠️ 任何操作必须带 `HF_ENDPOINT=https://hf-mirror.com`**，否则 embedding 报 `Errno 101`。
重启服务：
```bash
ssh <USER>@<LAN_IP> 'cd /tmp && HF_ENDPOINT=https://hf-mirror.com nohup \
  $HOME_DIR/miniconda3/envs/basic-memory/bin/basic-memory mcp \
  --transport streamable-http --host 0.0.0.0 --port 8765 > $LOG_DIR/bmmcp.log 2>&1 &'
```

---

## 3. codebase-memory-mcp（代码图谱）

| 项 | 值 |
|---|---|
| 二进制 | `$CBM_DIR/codebase-memory-mcp`（**portable 版**） |
| 已索引 | `vcc-example-project` → 83 节点 / 82 边 |

```json
{
  "mcpServers": {
    "codebase-memory": {
      "command": "ssh",
      "args": ["<USER>@<LAN_IP>", "$CBM_DIR/codebase-memory-mcp"]
    }
  }
}
```

**⚠️ `cli` 子命令会卡死**，用这个脚本：
```bash
python3 $LOG_DIR/cbmprobe.py list_projects detail=stats
python3 $LOG_DIR/cbmprobe.py get_architecture project=vcc-example-project aspects=overview
```

---

## 4. Backlog.md（任务看板）

| 端口 | 项目 |
|---|---|
| 6421 | 聚合页 |
| 6422 | VCC 真实项目 |
| 6420 | 写作侧 |

管理：`$PLATFORM_ROOT/platform/bb-ports.sh {start|stop|restart|status|refresh}`

**⚠️ 未解决**：`ports.json` 里 VCC 路径指向 NFS 目录，而 agent 在本地盘工作副本，
两边各有一套 `.backlog/`，看板读不到本地盘建的任务。

---

## 三个工具怎么配合

```
文档/笔记/对话  ──LLM 抽取──▶  TencentDB Wiki   （自动结构化，8421）
agent 手写笔记  ────────────▶  Basic Memory    （人工沉淀，8765）
代码           ────────────▶  codebase-memory  （调用关系，stdio）
任务           ────────────▶  Backlog.md       （进度，6421）
```

**典型流程**：
1. 实验跑完，agent 把结论写进 Basic Memory 笔记
2. 攒够一批笔记，丢给 TencentDB 自动抽成 Wiki（省去手动整理）
3. 改代码前用 codebase-memory 查影响面
4. 任务状态在 Backlog.md 更新

---

## 服务管理速查

```bash
# Knowledge (8421)
$HOME_DIR/ks.sh status

# Basic Memory (8765)
pgrep -f "basic-memory mcp"

# Backlog 三个端口
$PLATFORM_ROOT/platform/bb-ports.sh status

# 一次看全
ss -tlnp | grep -E "6420|6421|6422|8421|8765"
```

**四个服务都没有开机自启**，服务器重启后需手动拉起。
