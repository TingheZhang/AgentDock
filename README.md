# AgentDock
A local-first workbench integrating task tracking, persistent memory, and visual dashboards for multi-agent teams.

A multi-project research platform that lets several coding agents work on the
same projects without stepping on each other, and gives each project a
knowledge base that **belongs** to it.

It is assembled from four upstream open-source projects. This repository
contains **our** integration code, operational contracts, and hard-won
operational knowledge — not the upstreams themselves.

---

## The four upstream sources

Everything here is built on these. Their own docs do not cover our ports,
paths, or the deviations we hit, so this README documents how we actually use
each one.

| # | Upstream | Role in this platform | What we run it on |
|---|---|---|---|
| 1 | **[TencentCloud/TencentDB-Agent-Memory](https://github.com/TencentCloud/tencentdb-agent-memory)** | Knowledge service: LLM-driven wiki + code-graph engine | port `8421`, API prefix `/v3` |
| 2 | **[basicmachines-co/basic-memory](https://github.com/basicmachines-co/basic-memory)** | Semantic note layer over a plain markdown vault (the note files *are* the source of truth) | port `8765` (MCP) |
| 3 | **[DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)** | Static code graph (declarations, calls, impact) exposed to agents | daemon + UI `9749` |
| 4 | **[MrLesk/Backlog.md](https://github.com/MrLesk/Backlog.md)** | Task tracker; each project gets its own board and web UI | ports `6420` / `6422` / `6423` |

### 1. TencentDB Agent Memory

Node service; its `package.json` describes it as *"Standalone knowledge service
— Code-Graph + LLM-Wiki engine"*, which is exactly the two capabilities we
consume. **The API prefix is `/v3`**, and you must strip the leading slash when
joining it to an endpoint or you get a double slash.

Required headers: `x-tdai-service-id: <TEAM_SLUG>` plus `"team_id": "<TEAM_ID>"`
in the body. Write/management endpoints additionally need
`Authorization: Bearer $KNOWLEDGE_SERVICE_KEY`.

**The most useful diagnostic we learned**: a plain-text `404 Not Found` means
the request never entered the application (wrong path). A JSON body containing
`code` means it did enter and failed validation. That one distinction saves a
lot of time.

### 2. Basic Memory

A note vault where the files on disk are authoritative — writing through the
MCP *is* writing the repository. Probing the tool list requires an
`initialize` round-trip first to obtain `mcp-session-id`; calling `tools/list`
bare reports `Missing session ID`. The CLI has no `write` subcommand, which
does **not** mean the tool is unavailable.

`write_note` needs `content`, `directory` **and** `title` together; omitting
`directory` returns 422. Note format:

```markdown
---
title: <title>
type: note
permalink: <uri-slug>
tags: [optional]
---

## Observations
- [fact] something #tag

## Relations
- relates_to [[another entity]]
```

Our reader is deliberately **read-only**; write notes through Basic Memory or
edit the files directly.

### 3. codebase-memory-mcp

Single static binary. `index_repository` needs an **absolute** `repo_path`, and
large repositories want `async=true` plus polling. Call `get_graph_schema`
first; `query_graph` is read-only openCypher and returns `unsupported` for
`MERGE` rather than silently succeeding.

Important limitation: a clean `check_index_coverage` means *"no coverage gaps
were recorded"*, **not** a proof of completeness. Before claiming a symbol has
no callers or is dead, still read the source.

Do not commit `.codebase-memory/graph.db.zst` on every watcher update — it will
grow the repository to gigabytes.

### 4. Backlog.md

`backlog task view <id>` — it is `view`, **not** `show`. There is no standalone
`status` command; use `task edit <id> -s "<status>"`. Config lookup order is
`backlog.config.yml` → `backlog/config.yml` → `.backlog/config.yml`.

`backlog browser` only listens on `127.0.0.1`. We do not use it: boards are
served by our own supervisor so they can be reached from the LAN and VPN.

---

## What this repository adds

The upstreams give us a wiki engine, a note vault, a code graph, and a task
board. None of them know about each other. This repository is the integration
layer plus the operating knowledge that only shows up after running the thing
for weeks.

### Architecture

```
                     ┌──────────────────────────────────────┐
   LAN / VPN  ──────►│ kb-proxy.py  (injects notes into    │
                     │ boards, relays WebSocket)            │
                     └──────────────┬───────────────────────┘
                                    │
   ┌────────────────────────────────┼───────────────────────────────┐
   │                                                                │
 ports 6420/6422/6423                port 6424                ports 6421
 per-project boards           per-project knowledge          aggregate
 (Backlog.md)                 reader (read-only)             dashboard
   │                                │                            │
   │  tasks                        │  reads notes + wiki         │  lists every
   ▼                                ▼                            ▼  project
 .backlog/tasks/*.md          memory/**/*.md  +  wiki        one card each
                              (the note files are
                               the source of truth)
   │                                │
   │                                ▼
   │                        upstream knowledge service (8421)
   │                        └─ wiki + code-graph, /v3 API
   │
   └──► codebase-memory index ──► pre-rendered graph snapshots
                                   served at /api/codegraph
```

### The three layers do not propagate automatically

This is the single most important thing to know:

| You change | Does the next layer update? |
|---|---|
| a note | **No** — the wiki is a separate store |
| code | **No** — the code graph is rebuilt separately |
| a task | **No** — no layer watches another |

Every propagation is a deliberate, explicit step. A health check reports a
"wiki freshness" warning when notes are newer than the ingested wiki, but it
does not fix it for you. This is intentional: silent auto-sync made it
impossible to tell whether a page reflected a real measurement or a re-render.

### Port map

| Port | Role |
|---|---|
| `6421` | Aggregate dashboard across all projects |
| `6420` / `6422` / `6423` | One Backlog.md board per project |
| `6424` | Per-project knowledge reader (read-only) |
| `8421` | Upstream knowledge service (`/v3`) |
| `8765` | Upstream note vault (MCP) |
| `9749` | Upstream code-graph UI |

The knowledge panel injected into each board is the **same** render as the
`6424` reader — one implementation, so the board and the reader can never
disagree about which notes belong to which project.

---

## Install

```bash
git clone <this-repo> vcc-platform && cd vcc-platform
cp platform/ports.example.json platform/ports.json
$EDITOR platform/ports.json       # set your paths and bind IP
```

Then start the services. Units live in `platform/systemd/`; copy them to
`~/.config/systemd/user/` and reload — **editing the repo copy alone does
nothing**:

```bash
cp platform/systemd/*.service platform/systemd/*.target ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start vcc-platform.target
python3 tools/health_check.py --deep
```

Configuration is read from the environment, so nothing site-specific is baked
into the code:

| Variable | Purpose |
|---|---|
| `VCC_TEAM_SERVICE_ID` | upstream knowledge service id |
| `VCC_TEAM_ID` | upstream team id |
| `KNOWLEDGE_SERVICE_KEY` | bearer token for write endpoints |
| `VCC_BIND_IP` | address used in links and for the public bind |
| `VCC_READONLY_PORT` | knowledge reader port (default `6424`) |
| `VCC_REGISTRY` | path to `ports.json` |
| `VCC_BACKLOG_BIN` | path to the `backlog` binary |

---

## Install the agent skill

This package ships a skill so an agent can operate the platform without being
told any of the traps below.

```bash
# per-user
mkdir -p ~/.workbuddy/skills
cp -r skills/vcc-platform-ops ~/.workbuddy/skills/

# or per-project
mkdir -p .workbuddy/skills
cp -r skills/vcc-platform-ops .workbuddy/skills/
```

`SKILL.md` must keep its `name` and `description` frontmatter for the skill to
load. It is loaded on demand, so the agent reads it only when a task looks
platform-related.

---

## Operational knowledge (the part you cannot get from the upstreams)

Full detail lives in `docs/OPERATIONS.md`, `knowledge/projects/platform/` and
the skill's `references/`. The traps that cause the most damage:

1. **A bound port is not a working service.** When a proxy's upstream dies, the
   listening socket stays and every request returns 502. Any "is it listening"
   check reports healthy forever. **Always require an actual HTTP 200.**
2. **The unit source directory is not the live directory.** `systemd` units
   must be copied to `~/.config/systemd/user/` before `daemon-reload`. The
   failure is deceptive: the journal keeps printing, the old file keeps running.
3. **`StartLimitIntervalSec` / `StartLimitBurst` belong in `[Unit]`.** Putting
   them in `[Service]` is silently ignored by systemd.
4. **A supervisor's output must not go to a pipe.** An inherited pipe means the
   reader waits forever for EOF and the supervisor stalls while the unit still
   reports `running`. Write to a log file instead.
5. **`git` on NFS is 100–700× slower** and a large `git add` may never finish.
   Use a local working copy with `--separate-git-dir`, symlink the data.
6. **`grep` proves a string exists, not that anyone can see it.** A panel can be
   injected 25 times and still be invisible. Verify what the user sees.
7. **The service source directory is not the effective directory.** A pid file
   records the sub-shell pid, not the process that holds the port. Derive the
   real pid from the listening socket, and never `pkill -f <script>` over ssh —
   that pattern matches your own command line and kills the session.
8. **A skills-based verifier can pass while testing nothing.** Every assertion
   needs a negative control: point it at a wrong premise and require it to fail.
   An `or True` anywhere in a check makes the whole count meaningless.
9. **An always-true assertion is not a one-time slip.** Fixing `or True` and
   then writing `is not True or ...` is the same bug again. Assert against a
   *group* of wrong premises.

---

## De-identification

This copy is scrubbed. Hostnames, addresses, usernames, team and wiki
identifiers, study names, and absolute data paths are replaced with
placeholders such as `$PLATFORM_ROOT`, `<LAN_IP>`, `<TEAM_ID>`.

Not scrubbed, on purpose:

- `127.0.0.1` and `0.0.0.0` — portable, and load-bearing in the code
- port numbers — they are the platform's public contract
- the four upstream URLs — they are meant to be found

`$PLATFORM_ROOT`, `$HOME_DIR`, `<TEAM_ID>` and friends are **not** filled in for
you: set them in the environment or in `ports.json`.

## License

This repository is our integration and documentation work. Each upstream
project keeps its own license — see its repository. Nothing here redistributes
upstream source.
