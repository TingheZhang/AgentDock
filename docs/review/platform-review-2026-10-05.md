# 平台审阅材料 —— 知识跟项目走 + 看板内嵌面板

> **本文件所有数字均为 2026-10-05 20:30 前后从运行中的服务实测读出**，
> 不是从设计文档或记忆转述。若与您观察到的现象不符，**以您看到的为准**，
> 并请在 §7 记录差异——上一轮就是因为文档写着「已修」而实际未修，
> 双方对同一件事的理解出现了分叉。
>
> 生成时间：2026-10-05 20:35
> 采集方式：见 §6，每条命令都可复跑

---

## §1 本轮最大的变化：知识从「平台级」改成「项目级」

上一轮您审过一个方向性问题，我当时判断错了。**您指出：「VCC 知识与笔记不是跟项目走的吗？」**

我确认您是对的，而且错得不轻：

| | 上一轮我的实现 | 实际情况 |
|---|---|---|
| Basic Memory | 写死读一个目录 | 真库 `project` 表有 **2 行**，本来就是多项目模型 |
| wiki | 写死 `WIKI_ID = <WIKI_ID>` | `/v3/wiki/list?team_id=` **能枚举** |
| 呈现 | 6424 做成全平台唯一入口 | —— |

**即：我读的是一个项目的视图，却把它挂成了全平台的唯一入口。**

现在的形态：

- 知识**归属于项目**，映射集中在 `ports.json` 的 `knowledge` 块
- 打开某个项目的看板（6420/6422/6423），**直接看到本项目的笔记与 wiki**
- 6424 保留为总览，按项目分组/切换
- 新增项目只改注册表，**不改代码**

### 映射长这样（单一真源）

```json
{
  "name": "vcc-example-project", "ui_port": 6422,
  "knowledge": { "note_subdirs": ["projects/example-project"], "wikis": [], "panel": true }
}
```

`bb-ports.sh`（决定是否注入）与 `vcc-readonly.py`（决定显示什么）读**同一份**映射，
所以看板和总览页**结构上不可能冲突**。

---

## §2 请您重点判断的四个问题

### Q1 注入式面板这条路，走得对吗？

Backlog.md 是封闭的第三方 SPA，没法从内部加面板。我采用的办法是
**在每个看板前加一层自有代理，在HTTP 响应里拼上面板 HTML**。

- 优点：零侵入第三方、不改 SPA、不 fork
- 代价：多一跳、多一层可能出错的地方；代理挂了看板就挂

**替代方案**是给每个项目做独立的知识页并从看板链过去（不注入）。
我没选它，因为您明确要求「6424 里面的记忆在 6422 展示」。
**但注入是否值得这个代价，我想听您的判断。**

### Q2 知识归属的默认值应该是什么？

现状：笔记按 `note_subdirs` 前缀匹配项目，**没被任何项目声明的进 `unassigned` 兜底**。

实测（总数必须对得上，否则就是静默丢数据）：

```
磁盘上 md 文件           = 5
三个项目认领             = 4  (example-project 1 / writing-side 1 / platform 2)
unassigned 兜底          = 1  (vcc-setup.md)
4 + 1 = 5                ✓ MATCH
```

**我的判断**：`unassigned` 兜底比「严格过滤」安全。宁可让人看到孤儿笔记，
也不要静默消失。**但这会让项目归属不够严格，您怎么看？**

### Q3 supervisor 这次暴露的问题该怎么修？

见 §4，这是本轮**最值得您看的真实缺陷**。

### Q4 我这次又犯了「文档写已修、实际未修」的老毛病

上一轮 `platform-review-brief.md` 写着缺口 1「✅ 已修」，而当时的修复
在您审阅之后才真正落地。**本轮我把所有数字都改成实测抓取**，
并附了复跑命令，请您核对这份材料的数据采集方式是否可靠
（§6 的命令您可以随便挑几条跑）。

---

## §3 现在的系统是什么样

### 端口与绑定

| 端口 | 绑定 | 服务 | unit |
|---|---|---|---|
| 6420 | `<LAN_IP>` | 写作侧看板 + 面板 | vcc-backlog |
| 6421 | `127.0.0.1` | 聚合页 | vcc-backlog |
| 6422 | `<LAN_IP>` | example-project 看板 + 面板 | vcc-backlog |
| 6423 | `<LAN_IP>` | 平台看板 + 面板 | vcc-backlog |
| 6424 | `0.0.0.0` | wiki + 笔记只读页 | vcc-readonly |
| 8421 | — | MemoryKnowledge (wiki 后端) | vcc-knowledge |
| 8765 | `0.0.0.0` | Basic Memory MCP | vcc-basic-memory |

四单元全部 `active` + `enabled`，MainPID 均有效。

### 目录结构

```
$PLATFORM_ROOT
├── platform/           平台脚本（bb-ports.sh / kb-proxy.py / vcc-readonly.py / ports.json）
│   └── www/index.html  聚合页（生成物，只经 bb-ports.sh refresh 重建）
├── memory/             笔记 source of truth
│   ├── shared/
│   └── projects/{example-project,writing-side,platform}/
├── wiki/knowledge.db   ★ 活库（73KB / 6 表）
├── state/              运行时状态
│   └── knowledge.db    ★ 0 字节空壳（见 §5缺陷 C）
└── projects/example-project  项目任务数据
```

### 核心文件

| 文件 | 行数 | 作用 |
|---|---|---|
| `bb-ports.sh` | 510 | 端口/进程生命周期、supervisor |
| `bb-aggregate.py` | 929 | 聚合页生成 |
| `vcc-readonly.py` | 1227 | 6424 只读阅读页 + `/api/panel` |
| `kb-proxy.py` | 232 | HTTP 层反向代理，注入面板 |

git：22 个追踪文件，工作区 clean，18 次提交。

---

## §4 🔴 本轮实测发现的真实缺陷（最需要您判断的部分）

### 缺陷 A：supervisor 重启是「全量推倒」，不是「按需重建」

我做了一个 kill -9 演练：杀掉 6422 的 backlog upstream。实测结果：

```
kill 后立刻:      HTTP 502（端口仍 bound —— 正是旧健康检查漏掉的状态）
25 秒后:          6422 恢复 200
但是:             6420 / 6421 / 6423 全部短暂消失
约 25 秒后:       才陆续恢复
面板:             最后一个回来
```

**这不是探针误报**——我第一次测时也以为是，核对了 `ss` 绑定和 systemd 日志才确认：
supervisor 检测到故障后 `exit 1`，由 `Restart=always` 拉起，**`bb-ports.sh start` 是把所有项目重新起一遍**。

**代价**：任意单个看板的故障，会让**所有**看板短暂不可用；
恢复时间从「单项目几秒」变成「全平台几十秒」。

**我认为这需要改成按需重建**（只重建故障的那个 project），
但这会改动 supervisor 的核心逻辑，**想先请您评估**：

- 值得为「单点故障不扩散」增加这个复杂度吗？
- 还是「全量重启」在项目数只有 3 的情况下够用，等项目变多再说？

### 缺陷 B：健康检查的语义修正（我认为已修，请您复核）

旧逻辑只要端口被 bound 就报 UP。**但代理上游死后端口仍在、每请求 502**，
supervisor 因此认为健康、永不重启。我实测抓到的日志：

```
supervise: UP ... http 502      ← 三个项目全坏，它报健康
```

现要求 HTTP 200 才算 up；bound 但非 200 且无本地监听时清stale 代理重建。

**请您确认这个判据是否够**——我只能证明它能抓住 502，
但「上游返回 500 但不算死」这类情况我没想到。

### 缺陷 C：`MyTask/state/knowledge.db` 是 0 字节空壳

```
MyTask/wiki/knowledge.db      73728 bytes  tables=6   ← 活库
MyTask/state/knowledge.db         0 bytes  tables=0   ← 迁移残留，无任何表
```

活库在 `wiki/`，`state/` 里那个是迁移时留下的空壳。
**目前无害**（服务读的是 `wiki/`），但它是个**误导性文件**——
将来谁按「state 存运行时状态」的直觉去读它，会得到「表不存在」。

我倾向改名 `.unused`（**不删除**，遵循既有保留惯例）。**请确认。**

### 缺陷 D：`vcc-readonly.py` 没有 argparse

四个脚本里只有它没有 usage：

```
bb-ports.sh --help          usage_present=True
bb-aggregate.py --help      usage_present=True
vcc-readonly.py --help      usage_present=False   ← 无 argparse
kb-proxy.py --help          usage_present=True
```

无参数直接跑会尝试绑定端口并抛 traceback，
**把「端口已占用」和「参数写错」两种情况混成同一个栈**。
影响维护（配置全靠环境变量，没有 `--help` 可查）。**优先级低，但我承认是缺陷。**

### 缺陷 E：注入面板的 HTML 转义边界

面板会被拼进第三方页面。现状：Python 侧统一 `html.escape`（含引号），
命名空间用 `vk-` 前缀 + `#vk-panel` 作用域 + `all:initial`。

**我没有对Backlog.md 自身做 XSS 审计**——
它是个封闭 SPA，但我没验证过它的页面上有没有会和面板交互的脚本。
**这属于我审不动的范围，请您判断风险。**

---

## §5 已知遗留项

| # | 项目| 状态 |
|---|---|---|
| 1 | `state/knowledge.db` 空壳 | 待确认改名 |
| 2 | `vcc-readonly.py` 无 argparse | 待您定优先级 |
| 3 | 面板 XSS 边界未审 | 待您判断 |
| 4 | supervisor 全量重启 | 待您评估（缺陷 A） |
| 5 | 审核闭环仍**零强制力** | 上轮您已指出，**仍未修** |
| 6 | 面板无写操作 | 设计如此（只读） |
| 7 | 项目数 3，全量重启可接受 | 项目变多需重评 |

**第 5 项我特别说明**：您上一轮说「git 合并只能证明代码进了分支，
不能证明实验成功」，并给出了任务ID → 运行ID → 产物 → 验收记录的判据。
**我这一轮完全没碰这块**，知识面板和它无关。它仍然开放。

---

## §6 请您复跑这些命令

都是只读或纯探测，不改状态。

```bash
# 1. 五个入口是否都活着
for p in 6420 6421 6422 6423 6424; do
  printf "%s " $p; curl -s -o /dev/null -w "%{http_code}\n" --max-time 8 http://<LAN_IP>:$p/
done

# 2. 知识归属是否正确（面板内容不应串台）
curl -s "http://<LAN_IP>:6424/api/knowledge" | head -c 2000

# 3. 笔记总数是否对账（claimed + unassigned 应等于磁盘上的 md 数）
find $PLATFORM_ROOT/memory -name '*.md' -not -path '*/.git/*' | wc -l

# 4. 那个 0 字节空壳
ls -l $PLATFORM_ROOT/wiki/knowledge.db $PLATFORM_ROOT/state/knowledge.db

# 5. 代理的注入是否逐字节透传静态资源
#    （已验证过5.4MB 的 JS bundle 一致，见下）
cd $PLATFORM_ROOT/platform && git log --oneline | head -5

# 6. 单元真活着吗（is-active 不等于服务可用）
systemctl --user status vcc-backlog --no-pager -n 15
```

---

## §7 请您在这里写结论

（这一节留白，请直接在此文件追加）

### 我希望您回答的

1. **缺陷 A**：supervisor 全量重启 vs 按需重建，选哪个？
2. **Q1**：注入式面板 vs 独立页面 + 链接，您倾向哪个？
3. **缺陷 B**：健康检查只要 200，够吗？还该看什么？
4. **缺陷 C**：空壳改名 `.unused` 可以吗？
5. **审核闭环（遗留 5）**：是否要在下一轮做？
6. **本材料的数据可信度**：§3/§4 的数字您复核过几个？有没有与您观察不符的？

### 上一轮您的意见与本轮的落实

| 您上轮的指正 | 本轮状态 |
|---|---|
| auto_commit / bypass_git_hooks 不是门禁 | 已从文档移除（25fd724） |
| pgrep -f \| head -1 不足以证明持端口 | 改用端口反查 + cmdline 校验 |
| HTTP 200 不能证明读对项目 | 本材料所有端口数据都配了归属校验 |
| 协作契约在 double-ood_work/AGENTS.md | 已在255b062 更正 |
