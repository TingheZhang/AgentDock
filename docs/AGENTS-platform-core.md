<!-- PLATFORM-CORE-OPS-START -->
# AGENTS.md — Platform Core Task Directory

> `platform-core` holds **Backlog tasks only**. Code lives elsewhere.
> Read this before creating, editing, or closing any task here.
> Platform code rules live in `$PLATFORM_ROOT/platform/AGENTS.md`
> and `OPERATIONS.md` — read those before touching code.

## Layout: tasks and code are separate

| Role | Path | In git | Holds |
|---|---|---|---|
| Task directory (this one) | `$PROJECT_DIR/platform-core` | **no** | `.backlog/` only |
| Code directory | `$PLATFORM_ROOT/platform` | yes | scripts, `ports.json`, `AGENTS.md`, `systemd/` |
| Notes | `$PLATFORM_ROOT/memory` | yes | `projects/<name>/`, source of truth |
| Wiki | `$PLATFORM_ROOT/wiki` | yes, **separate repo** | `raw/sources/*.md` =真源；`wiki/**/*.md` = LLM 摘要页；`*.db` **excluded** |

The wiki became a git repository on 2026-10-06 (first commit `4a44572`,
65 files, 63.5 KB, `.git` 732 KB). It is a **separate repo** — its history is
not shared with `memory`. `*.db` is excluded because `index.db` is the LIVE
database, rewritten wholesale on every ingest; a binary diff of it says
nothing. Being in git makes the summary pages *traceable*, not correct:
still grep after an ingest.

There is no `ports.json`, no scripts, and no `www/` here. If you need to
change how the platform behaves, you are editing the wrong directory.

## This directory is not in git

`git rev-parse` fails here, and `config.yml` sets `auto_commit: false`
and `filesystem_only: true`. Consequences:

- **A task file has no history.** `git diff` cannot show what changed, and
  a bad edit is not recoverable through git. Read a task fully before
  overwriting it.
- **Commit references in a task point at other repositories.** A commit
  hash in a task here may belong to `platform` or to a project such as
  `example-project`. Always name the repository next to the hash. A bare hash
  is not verifiable.
- Before rewriting several tasks, copy `.backlog/tasks/` aside. There is
  no undo.

## Managing tasks

**Use the `backlog` CLI. Do not hand-edit task markdown.** Metadata,
relationships, ordering, and history are maintained by the tool; manual
edits desynchronise them.

```bash
export PATH="$HOME/.local/bin:$PATH"   # non-interactive shells have a bare PATH
cd $PROJECT_DIR/platform-core

backlog instructions overview            # read at the start of a session
backlog task list
backlog task view <taskId>               # note: `view`, not `show`
backlog task create "title" -d "desc"
```

`backlog instructions task-creation`, `task-execution`, and
`task-finalization` cover the lifecycle in detail. Use
`backlog <command> --help` before unfamiliar commands.

Reading task files directly is fine and often faster for inspection —
just do not write them that way.

## Status is a claim, not a result

`Done` on the board records an intention. It does not prove the work
succeeded. Shared Unix accounts make review unenforceable by mechanism,
so the record is the only guarantee.

Before writing a final summary or moving a task to a terminal status,
the acceptance report must contain:

| Field | Requirement |
|---|---|
| task ID | the `TASK-n` this report closes |
| repository + commit | hash **with** its repository named |
| artifact path | real file or endpoint that was inspected |
| endpoint result | HTTP status from the **live** port, not the edited file |
| negative control | a check that would have failed before the fix |

"`git merge` succeeded" is not an acceptance result. Neither is a green
assertion whose conditions were never checked — see below.

## Handover: write CHANGELOG before closing tasks

Before moving a task to Done:
- Record your work in `$PLATFORM_ROOT/memory/projects/platform/CHANGELOG.md`.
- Include task ID, commit hash, description of change, and verification results.
- Commit the entry in `memory` git repository.

## Reporting state honestly

- **Bound is not healthy.** A proxy keeps its listening socket while the
  upstream is dead and returns 502. Require HTTP 200 from a real request.
- **Verify on the live endpoint, not on the file you edited.** A generated
  file on disk can be correct while the service reads a different one.
- **When a check fails, first suspect the check.** Compare against the real
  artifact before changing product code.
- **Every new assertion needs a negative control.** Point it at a wrong
  premise once and confirm it reports FAIL. An assertion that cannot fail
  is worse than none — it inflates the pass count.
- **A status line is not health.** Quote an HTTP code.

## Do not modify without asking

| Path | Why |
|---|---|
| `.backlog/config.yml` | statuses, labels, and prefixes are shared by every task |
| Completed task files | they are the only record of what was verified |
| `../platform/ports.json` | single source of truth for every consumer |
| `../platform/www/index.html` | generated — use `bb-ports.sh refresh` |
| `../memory/**`, `../wiki/**/*.md` | research content, not task state |
| `../wiki/**/*.db*` | LIVE wiki database, rewritten on every ingest |

Describe the change and wait for confirmation.
<!-- PLATFORM-CORE-OPS-END -->
