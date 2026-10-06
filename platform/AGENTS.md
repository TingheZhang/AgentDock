<!-- BACKLOG.MD GUIDELINES START -->
<!-- backlog.md-instructions-version: 1.53.0 -->
<CRITICAL_INSTRUCTION>

## Backlog.md Workflow

This project uses Backlog.md for task and project management.

**At the beginning of each conversation in this project, run `backlog instructions overview` before answering or taking action. Re-read it only if you have not read it yet in the current conversation.**

Use the overview to decide whether to search, read, create, or update Backlog tasks.

Before task lifecycle actions, read the matching detailed guide:
- `backlog instructions task-creation` before creating or splitting tasks
- `backlog instructions task-execution` before planning, changing status or assignee, adding a plan or implementation notes, or implementing task work
- `backlog instructions task-finalization` before checking acceptance criteria, writing final summaries, or moving tasks to terminal statuses

Use `backlog <command> --help` before running unfamiliar commands. Help shows options, fields, and examples.

Do not edit Backlog task, draft, document, decision, or milestone markdown files directly. Use the `backlog` CLI so metadata, relationships, and history stay consistent.

</CRITICAL_INSTRUCTION>
<!-- BACKLOG.MD GUIDELINES END -->

---

<!-- PLATFORM-OPS-START -->
# AGENTS.md — Platform Core

> Read this before operating the platform. It is short on purpose: only the
> things that are non-obvious or that fail silently.
> Detail lives in `OPERATIONS.md` and the `platform-core-ops` skill.

## The three facts that cause silent failures

1. **A bound port is not a working service.** A proxy whose upstream died
   keeps its listening socket and returns 502 for every request. Any
   "is it listening?" check reports healthy forever. **Require HTTP 200.**
2. **The unit source directory is not the live directory.** After editing
   `systemd/*.service`, copy it to `~/.config/systemd/user/` and run
   `daemon-reload`, or the change does nothing while the journal keeps
   printing from the old file.
3. **Never `git init` on NFS** (measured ~200× slower on small writes, can
   hang). The platform repo already uses `--separate-git-dir`; leave it
   alone. Check the real filesystem with `df -Th <path>` before assuming:/n   a path under an NFS mount may still be a symlink to ext4.

## Layout

```
$PLATFORM_ROOT
├── platform/     scripts + ports.json (the registry) + www/ (generated)
├── memory/       notes, source of truth  (projects/<name>/, shared/)
├── wiki/         LLM wiki (index.db = LIVE db, excluded from git; md pages tracked)
├── state/        runtime only; knowledge.db here is a 0-byte stale copy
└── projects/example-project/  task data + symlinks to 16 GB on NFS
```

## Ports

| Port | Serves | Unit |
|---|---|---|
| 6420 / 6422 / 6423 | the three project boards (+ knowledge panel) | `vcc-backlog` |
| 6421 | aggregate dashboard | `vcc-backlog` |
| 6424 | knowledge reader (wiki + notes) | `vcc-readonly` |
| 8421 | MemoryKnowledge | `vcc-knowledge` |
| 8765 | Basic Memory MCP | `vcc-basic-memory` |

Quick health check, including note reconciliation:

```bash
python3 /tmp/hc.py --deep        # if hc.py is not present, see OPERATIONS.md
./bb-ports.sh status
```

## Operating rules

- **`ports.json` is the single source of truth.** `bb-ports.sh` and
  `bb-aggregate.py` both read it, so the dashboard cannot drift from
  reality. **After editing it, run `./bb-ports.sh refresh`** or the
  dashboard shows stale data.
- **Never hand-edit `www/index.html`.** It is generated. Hand edits pass
  on-disk checks and change nothing live.
- **After changing a systemd unit, copy it into `~/.config/systemd/user/`
  and `daemon-reload`.** Verify with `systemctl --user show <unit> -p Type`.
- **Probe `127.0.0.1` for local health, and probe each configured public bind
  address separately. Numeric IPs do not require DNS; DNS names are a separate
  reachability check.
- **To find the process holding a port**, use
  `ss -tlnp | grep ":PORT "` then parse `pid=` with a regex. Do not use
  `pkill -f <script>`: it matches the shell running it and kills the
  session (exit 255, no output — looks like the command failed).
  Note two processes can share a port: the board on `127.0.0.1` and the
  injecting proxy on the public IP.

## The wiki is now in git

`$PLATFORM_ROOT/wiki` is an **independent git repository** (its own
history, separate from `memory`). Tracked: `raw/sources/*.md` (the
hand-maintained truth), `wiki/**/*.md` (the generated summary pages), and
`_wiki_engines/wiki-sources.json`. Excluded: `*.db`, `*.db-wal`, `*.db-shm` —
`index.db` is the LIVE database, rewritten wholesale on every ingest.

```bash
cd $PLATFORM_ROOT/wiki
git diff                # see what an ingest changed, line by line
git log --oneline -- "<TEAM_SLUG>/vcc/<WIKI_ID>/raw/sources/ops-manual.md"
git checkout --<path>   # roll back one summary page
```

This closes the gap where a bad ingest could only be undone with
`wiki_sync.py backup`. It also means the **source is recoverable**: if
`raw/sources/ops-manual.md` is damaged, `git log -p` on that path shows
every version.

The summary pages are still LLM output and can be wrong. Being in git makes
them *traceable*, not correct — grep after an ingest rather than assuming.

## Knowledge belongs to a project

Mapped per project in registry, so board and reader should agree when they read the registry; runtime fallback or port drift must still be checked:

```json
"knowledge": {
  "note_subdirs": ["projects/my-project"],
  "wikis": ["wiki-abc123"],
  "panel": true
}
```

Notes no project claims are **not discarded** — they appear under
`unassigned`. If a note seems lost, check there, and check the
enumeration's directory filters, before concluding anything. (A skip list
once filtered out `projects/`, which is exactly where per-project notes
live, so the first project registered showed 0 notes and looked like data
loss.)

## Do not modify without asking

| Path | Why |
|---|---|
| `platform/ports.json` | every consumer reads it; a bad entry breaks the platform |
| `platform/www/index.html` | generated — use `bb-ports.sh refresh` |
| `~/.config/systemd/user/vcc-*.service` | desyncs from the repo copy |
| `memory/**`, `wiki/**/*.md` | research content, not platform code |
| `wiki/**/*.db*` | LIVE wiki database, rewritten on every ingest |
| `state/**` | runtime, regenerated |

Describe the change and wait for confirmation instead.

## Reporting state honestly

- Quote an **HTTP status**, not a PID. `systemctl is-active` is not proof
  of life either.
- **Verify a change on the real endpoint**, not on the file you edited.
- **Never mark a task Done before its branch is merged into `main`.** The
  board shows status as a *claim*; the merge is what makes it real.
- **When a check fails, first suspect the check.** Confirm against the
  real artifact before editing product code.
- Report recovery-in-progress as such. The supervisor performs isolated
  target recovery; a port being briefly absent right after a failure may be
  expected, but verify every required endpoint before calling it healthy.

## Handover: write CHANGELOG before wrapping up

Whenever you finish a task, fix an issue, or alter platform code:
- Append an entry to `$PLATFORM_ROOT/memory/projects/platform/CHANGELOG.md`.
- Format: date, task ID, agent name, context/why, changes made, verification results (HTTP status / tests), git commit hash.
- Commit the change in the `memory` git repository.
- This changelog is automatically indexed and displayed in the knowledge panel on port 6424 and the dashboard.

## For agents working in a project

Project-level collaboration rules (worktree isolation, branch naming, the
review gate) live in that project's own `AGENTS.md`, e.g.
`$PROJECT_DIR/example-project/AGENTS.md`. Read it before touching
task state.
<!-- PLATFORM-OPS-END -->
