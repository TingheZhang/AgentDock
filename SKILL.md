---
name: platform-core-ops
description: Operate, diagnose, and extend a multi-project research platform where several coding agents share projects: one task board per project, an aggregate dashboard, and a read-only per-project knowledge reader that combines notes, a wiki and a code graph. Use when starting or stopping platform services, registering a project, diagnosing a port that is bound but not serving, wiring knowledge to a project, or reviewing the architecture. Also covers the traps that make changes silently not take effect.
agent_created: true
---

# Platform Core Operations

Multi-project research platform: one task board per project, an aggregate
dashboard, and knowledge (notes + wiki) belonging to each project and
rendered **inside** that project's board. The notes on disk are the source of
truth; the wiki and the code graph are separate stores that never update
automatically.

## Read this before touching anything

Three facts cause most silent failures here. Internalize them first.

1. **A bound port is not a working service.** When a proxy's upstream dies,
   the listening socket stays and every request 502s. Any "is it
   listening?" check reports healthy forever. **Always require HTTP 200.**
2. **The unit source directory is not the live directory.** Editing
   `systemd/*.service` in the repo does nothing until it is copied to
   `~/.config/systemd/user/` and `daemon-reload` is run. The change looks
   like it applied — journal messages keep printing from the old file.
3. **git on NFS is 100–700× slower** and can hang. The platform repo
   already uses `--separate-git-dir`; keep it that way.

## Layout

```
$PLATFORM_ROOT
├── platform/           platform scripts + ports.json (the registry)
│   └── www/index.html  dashboard (generated — never hand-edit)
├── memory/             notes, source of truth for Basic Memory
│   ├── shared/
│   └── projects/<name>/
├── wiki/knowledge.db   live wiki database (6 tables)
├── state/              runtime: basic-memory index (72M), codebase-memory
└── projects/example-project project task data + symlinks to 16GB on NFS
```

Note: `state/knowledge.db` is a **0-byte stale copy**. The live database is
`wiki/knowledge.db`. Do not "fix" the wiki by writing to `state/`.

## Ports and units

| Port | Serves | Unit |
|---|---|---|
| 6420 | writing-side board + knowledge panel | `vcc-backlog` |
| 6421 | aggregate dashboard | `vcc-backlog` |
| 6422 | example-project board + knowledge panel | `vcc-backlog` |
| 6423 | platform board + knowledge panel | `vcc-backlog` |
| 6424 | knowledge browser (wiki + notes) | `vcc-readonly` |
| 8421 | MemoryKnowledge API | `vcc-knowledge` |
| 8765 | Basic Memory MCP | `vcc-basic-memory` |

## Common operations

```bash
cd $PLATFORM_ROOT/platform

# Lifecycle — the target brings up all three boards + dashboard
systemctl --user start platform-core.target
systemctl --user stop  platform-core.target
systemctl --user status vcc-backlog --no-pager -n 20

# Status of everything at once
./bb-ports.sh status

# Rebuild the dashboard AFTER editing anything it renders
./bb-ports.sh refresh          # writes www/index.html atomically
```

`bb-ports.sh` and `bb-aggregate.py` both read `ports.json`, so the
dashboard cannot drift from what is running. **After editing
`ports.json`, run `bb-ports.sh refresh`** or the dashboard shows stale data.

### Adding a project

1. Add an entry to `ports.json` `projects[]` (see `references/registry.md`)
2. `mkdir -p` the project path and initialize its Backlog container
3. `./bb-ports.sh add <name> <path> <group> <port>`
4. `./bb-ports.sh refresh`
5. Verify with `./bb-ports.sh status` and a real HTTP status code

## Health diagnosis

When a port does not respond, work down this list rather than guessing.

```bash
PORT=6422

# 1. Is anything listening, and on WHICH address? Two layers bind the same
#    port: the board on 127.0.0.1, the injecting proxy on the public IP.
ss -tlnp | grep ":$PORT "

# 2. Which PID actually holds it? ss renders pid= comma-delimited, so parse
#    with a regex -- whitespace splitting never yields a "pid=" token.
PID=$(ss -tlnp | grep ":$PORT " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
tr '\0' ' ' < /proc/$PID/cmdline

# 3. Does it actually SERVE? This is the question that matters.
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:$PORT/

# 4. Is the process working in the directory you think?
readlink /proc/$PID/cwd
```

**Probe `127.0.0.1` for local health, not the public IP.** The public IP
needs DNS, which can fail transiently and make a healthy service look dead.

If step 3 returns 502 while step 1 shows the port bound, the proxy's
upstream is gone. The supervisor also requires 200 and will rebuild it:

```bash
journalctl --user -u vcc-backlog -f
```

Recovery takes roughly 25 seconds and currently **restarts all three
boards**, not just the failed one. That is a known limitation, not a new
outage — see `references/known-issues.md`.

## Wiring knowledge to a project

Knowledge is mapped per project in `ports.json` so the board and the
reader can never disagree:

```json
"knowledge": {
  "note_subdirs": ["projects/my-project"],
  "wikis": ["wiki-abc123"],
  "panel": true
}
```

- `note_subdirs` — directories under `memory/` holding this project's notes,
  matched by prefix
- `wikis` — wiki ids from MemoryKnowledge; `[]` for none
- `panel` — `false` runs the board without a knowledge panel

Notes no project claims are **not discarded**; they surface in an
`unassigned` bucket. If a note seems to have vanished, check that bucket
before assuming loss. Verify totals reconcile:

```bash
find $PLATFORM_ROOT/memory -name '*.md' -not -path '*/.git/*' | wc -l
curl -s 'http://127.0.0.1:6424/api/knowledge' | head -c 2000
```

## Forbidden

Do not modify these without an explicit human instruction:

| Path | Why |
|---|---|
| `platform/ports.json` | single source of truth; a bad port or path breaks every consumer |
| `platform/www/index.html` | generated — write via `bb-ports.sh refresh` |
| `~/.config/systemd/user/vcc-*.service` | changing these desyncs the repo copy |
| `memory/**` and `wiki/**` | research content, not platform code |
| `state/**` | runtime state; regenerated |

If a change to a forbidden file seems necessary, describe the change and
wait. Editing `ports.json` in place without a backup has no undo — the
project-to-port mapping is consulted by every script.

## Reporting state honestly

- **A bound port is not health.** Quote an HTTP status, not a PID.
- **`systemctl is-active` is not health either.** Confirm the port serves.
- **Assert a change took effect on the real endpoint**, not on the file you
  edited. A dashboard write to the wrong path passes disk checks and
  changes nothing live.
- **Never mark a task Done before its branch is merged into `main`.** The
  dashboard shows status as a *claim*; the merge is what makes it real.
- When a check fails, first suspect the check. Confirm by inspecting the
  real artifact before changing product code.

## References

- `references/registry.md` — `ports.json` field reference, adding projects
- `references/knowledge.md` — wiki/notes backends, API shapes, attribution
- `references/known-issues.md` — open defects and their current state
- `scripts/health_check.py` — one-shot health probe across all ports
