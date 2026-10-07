# review/ 目录索引 —— 请先读这一页

> **Historical evidence:** this is a dated review snapshot, not the current
> operations authority. Use the repository root `SKILL.md`, `README.md`, and
> `docs/OPERATIONS.md` for current procedures.

> 最后更新：2026-10-05 20:35
> 服务器 `<USER>@<LAN_IP>`，登录后：`cd <legacy-projects-path>/review && ls`

---

## ⚠️ 先看这个：材料分两代，**请只审 2026-10-05 那份**

| 文件 | 时间 | 状态 |
|---|---|---|
| **`platform-review-2026-10-05.md`** | 20:34 | ✅ **当前有效，请审这份** |
| `REVIEW-README.md`（本文件） | 20:35 | 索引 |
| `platform-review-brief.md` | 16:35 | ⚠️ **已过时** |
| `idea-verification-report.md` | 16:35 | ⚠️ **已过时** |
| `expert-onboarding.md` | 16:35 | ⚠️ 部分过时 |
| `memory-tools-config.md` | 16:35 | ⚠️ 部分过时 |

### 为什么旧的过时

16:35 之后平台发生了一次**架构级重构**：

1. **目录迁移** —— 全部路径从 `<legacy-projects-path>` / `$NOTES_PROJECT`
   改到 `$PLATFORM_ROOT/` 统一布局。旧文档里的路径大半已失效。
2. **知识从平台级改为项目级** —— 旧文档写的是 6424 挂一个全局 wiki；
   现在是**每个看板内嵌自己项目的知识面板**（这是您上轮的指正方向）。
3. **新增 `kb-proxy.py`** —— 旧文档里没有这个文件，它负责把面板注入看板。

**如果按旧文档去审，您审的是一个已经不存在的架构。**
旧文档保留仅作历史参考。

---

## 请审这份

📄 **`platform-review-2026-10-05.md`**（约 11 KB，7 节）

特点是**所有数字都从运行中的服务实测抓取**，不是设计意图转述。
每条结论都标了实测依据，§6 附了可复跑的只读命令。

| 节 | 内容 |
|---|---|
| §1 | 知识归属的架构变更（您上轮指正的方向） |
| §2 | **四个需要您判断的问题** ← 本文档的目的 |
| §3 | 当前系统形态、端口分配、核心文件行数 |
| §4 | **五个实测缺陷**（A 是本轮演练抓到的真问题） |
| §5 | 已知遗留项（含仍未修的审核闭环） |
| §6 | 复跑命令（只读，不改状态） |
| §7 | **留白，待您填写结论** |

### 建议阅读顺序

1. 先读 §2（四个问题）—— 直接给出您的判断即可
2. 再读 §4（五个缺陷）—— 尤其**缺陷 A**，那是真问题
3. §3/§6 用于核对数据
4. 需要深入时再看代码

**预计 20 分钟可给出结论**，不必逐行读代码。

---

## 环境速查

- Ubuntu 22.04/ glibc 2.35 / Python 3.10
- 独立 Node 22：`$NODE_BIN`（**不动系统 node**，它还是 v12）
- `backlog` CLI：`$HOME_DIR/.local/bin/backlog`（**不在非交互 PATH 里**）
- 独立 Python 3.12：`$HOME_DIR/miniconda3/envs/basic-memory/`
- git 仓库：`$PLATFORM_ROOT/platform`（separate-git-dir，git 目录在本地盘）
- **NFS 上不要 git**（实测慢 100~700倍）

---

## ⚠️ 请不要直接改这些

| 路径 | 原因 |
|---|---|
| `$PLATFORM_ROOT/platform/bb-ports.sh` | 改坏三个看板全挂 |
| `$PLATFORM_ROOT/platform/ports.json` | 全平台端口与知识映射的**唯一真源** |
| `$PLATFORM_ROOT/platform/kb-proxy.py` | 面板注入逻辑 |
| `$PLATFORM_ROOT/platform/vcc-readonly.py` | 6424 阅读页 |
| `~/.config/systemd/user/vcc-*.service` | 改完**必须** `cp` 回 `MyTask/platform/systemd/` 再 `daemon-reload` |

审查中发现问题请**写在 review/ 里告诉我**，由我来改。
目录属主 `tgh:biolab`，但 `test -w` 已实测**通过** —— 您可以直接写批注。

---

## 上一轮您的意见，落实情况

| 您的指正 | 状态 |
|---|---|
| `auto_commit` / `bypass_git_hooks` 不是审核门禁 | ✅ 已从文档移除（25fd724） |
| `pgrep -f \| head -1` 不足以证明持端口 | ✅ 改用端口反查 + cmdline 校验 |
| HTTP 200 不能证明读对项目 | ✅ 本轮端口数据均配归属校验 |
| 协作契约在 `double_ood_work/AGENTS.md` | ✅ 已在 255b062 更正 |
| 审核闭环需绑定运行 ID + 产物 + 验收记录 | ❌ **仍未做**，见新文档 §5 遗留项 5 |
