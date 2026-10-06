
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

## Git collaboration (added by CodeBuddy)

This project is under git so that multiple agents can work without stepping on
each other, and so a reviewer can see exactly what changed before it is
accepted.

## Where the repository lives

- Remote (bare): `$BARE_REPO` -- local disk on 174.
- Working copy: `$PROJECT_DIR/example-project` -- also local disk.
- The NFS path `$NFS_DATA` is the **data**
  location (16 GB of binaries, results, logs) and is NOT a git working copy.

Do not run `git init` inside the NFS directory. Git on NFS measured 100-700x
slower than local disk (`git add` of 60 files: 0.02s local vs 14.84s NFS), and
a 300-file `git add` did not finish at all. It also risks stale index locks.

If you need to read the large data, the working copy has symlinks:
`ext_data`, `priors`, and `results` point back into the NFS mount.

## Branch convention

Every agent works on its own branch and never commits to `main` directly.

    agent/<who>-<task-id>        e.g. agent/deepseek-t12, agent/opus-t12-review

`main` is the reviewed line. Only a human-reviewer-accepted merge lands there.

## Get a worktree -- do not just `git checkout -b`

A branch alone does not isolate anything. The working copy at
`$PROJECT_DIR/example-project` has one index, one HEAD and one set of files, so
if two agents work there, one agent's `git checkout -b` changes the files the
other one is editing, and `git status` reports the other agent's changes as
your own. The branch convention only holds if each agent has their own
worktree.

Use the helper, which handles everything below:

    $PROJECT_DIR/example-project/vcc-wt.sh add <who> <task-id>

That creates `$HOME_DIR/wt-<who>` on branch `agent/<who>-<task-id>`, and
handles three things a plain `git worktree add` does not:

1. **The data symlinks.** `ext_data`, `priors` and `results` point into the
   NFS mount and are deliberately *not* tracked (`.gitignore` matches both
   `name/` and `name`, so the symlink itself is ignored too). A fresh
   worktree gets none of them, so without this step you cannot read the
   16 GB of data at all.
2. **Dashboard visibility.** The aggregate dashboard on port 6421 reads tasks
   from `$PLATFORM_ROOT/platform/ports.json` only. The worktree is registered
   there so your tasks show up under "<STUDY_GROUP> (agent worktrees)". No web UI
   port is assigned -- the card lists your task titles inline instead.
3. **Refusing a destructive remove.** `vcc-wt.sh remove` will not delete a
   worktree that has uncommitted work, and it keeps the branch when it was
   never merged into `main`.

Other subcommands:

    vcc-wt.sh list                # all worktrees, branch, and dirty state
    vcc-wt.sh check <who>         # preflight: symlinks resolve? uncommitted work?
    vcc-wt.sh sync                # re-register live worktrees, prune stale entries
    vcc-wt.sh remove <who>        # after the branch is merged or abandoned

Worktrees live on local disk (`/data`), never on NFS, for the git-metadata
reason above. The bulk data they point at stays on NFS, which is fine --
reads are what NFS is good at.

### Two ways this goes wrong silently

**A merge that merges nothing.** `git -C $PROJECT_DIR/example-project merge
<branch>` merges into whatever branch the *main worktree* has checked out, not
into `main` by name. If something left the main worktree on another branch,
the merge reports success (or "Already up to date") and `main` never moves --
you get a linear history with no review point in it. Check before merging:

    git -C $PROJECT_DIR/example-project rev-parse --abbrev-ref HEAD   # must say main

**Branches left behind after `vcc-wt.sh remove`.** The script deletes a branch
only when it was merged into `main`; otherwise it keeps the branch and tells
you so, because deleting an unmerged branch is how real work gets lost. Test
and demo agents therefore leave `agent/<who>-<task-id>` branches behind. That
is expected. Once you have confirmed nothing valuable is on one:

    git branch -D agent/<who>-<task-id>

### Backlog state is per-worktree

Each worktree has its own `.backlog/`, because Backlog.md resolves its state
directory from the working directory. Consequences:

- A task you create in your worktree is visible in the dashboard (once
  registered) but NOT in the main working copy until the branch carrying it
  is merged into `main`.
- Do not treat the main copy's task list as the whole truth while you work.
  `vcc-wt.sh list` shows which worktrees exist and what each holds.

## The review workflow

1. Take a task from Backlog.md (`backlog task list`) and get a worktree for it:

       $PROJECT_DIR/example-project/vcc-wt.sh add <who> <task-id>
       cd $HOME_DIR/wt-<who>

   That puts you on branch `agent/<who>-<task-id>` with the data symlinks and
   the dashboard entry in place. Commit to `main` directly and you will
   clobber a concurrent agent.

2. Commit as you go, with the task id in the message so the dashboard history
   and the commit history line up:

       git add -A && git commit -m "task-12: add OOD recombination analysis"

3. When the work is ready for review, push the branch:

       git push -u origin agent/<who>-<task-id>

4. The reviewer checks the diff, then merges with an explicit merge commit so
   the review point is visible in the history:

       git checkout main
       git merge --no-ff agent/<who>-<task-id> \
         -m "review: accept task-12 after Opus review"

   Use `--no-ff`. It keeps the reviewer's merge as a distinct point in
   `git log`, which is the whole point of the review gate. A fast-forward would
   erase the boundary between "agent did this" and "someone approved this".

5. Only after that merge, mark the task Done in Backlog.md. Do this in the
   main working copy (`$PROJECT_DIR/example-project`), not in the worktree: the
   merge is what moved the work onto `main`, so that is where the record
   belongs.


6. **Append to CHANGELOG.md**: Before closing out the task, record what you did in `$PLATFORM_ROOT/memory/projects/example-project/CHANGELOG.md`:
   - Date, task ID, agent name.
   - Summary of changes, experiment config/checkpoints, and evaluation metrics/findings.
   - Git commit hash of the merge commit.
   - Commit the updated changelog in the memory repo (`cd $PLATFORM_ROOT/memory && git commit -m "... "`).
   This changelog is automatically indexed and displayed in your board's knowledge panel (port 6422).

Never mark a task Done before its branch is merged into `main`. The
dashboard shows a task's status as a *claim* by the agent that set it, not as
verified work, so the merge is what actually makes it real.

## What is and is not tracked

Tracked (~1 MB): `scripts/`, `paper/`, `figures/`, `.backlog/`, `AGENTS.md`.
The `.backlog/` directory must stay tracked -- it is the shared task state the
dashboard reads, so excluding it would lose the review history.

Not tracked (~16 GB): `ext_data/`, `priors/`, `results/`, `*.log`. These are
binaries and regenerable outputs. See `.gitignore`.

---

<!-- KNOWLEDGE-SECTION-START -->
## Your knowledge lives with the project

Knowledge is mapped per project in the platform registry, so you do not
have to go to a separate site to find it.

- **Your notes**: `$PLATFORM_ROOT/memory/projects/example-project/`
- **Your board**: http://<LAN_IP>:6422/ -- the knowledge panel is
  rendered **inside** the board; this project's notes and wiki appear there
  directly
- **Full knowledge browser**: http://<LAN_IP>:6424/ -- switch between
  projects at the top

Rules that keep attribution correct:

- Put new notes under your project's `note_subdirs` path. Notes placed
  elsewhere show up under `unassigned` rather than in your board -- they are
  not lost, but nobody browsing your project will see them.
- To add a note, write the `.md` file (it is the source of truth) and let
  the index catch up. Do not edit the SQLite index.
- The reader is a separate service (port 6424, unit `vcc-readonly`). If it
  is down, the board still works and the panel is simply omitted -- report
  that as "knowledge unavailable", not as "the platform is broken".

For platform operation rules, read the platform `AGENTS.md` or the
`platform-core-ops` skill.
<!-- KNOWLEDGE-SECTION-END -->