# Supervisor verification — 2026-10-06

This is a read-only snapshot from host `174`. It is evidence for the
operations boundary; it is not a fault drill and does not prove that a disk
hash is the exact code loaded by every child process.

## Effective unit and process association

- Effective user unit: `<home-user-root>/.config/systemd/user/vcc-backlog.service`.
- `systemctl --user show` reported `ActiveState=active`, `SubState=running`,
  `Type=simple` in the unit source, `MainPID=1556736`, and
  `ExecStart=<platform-root>/MyTask/platform/vcc-backlog-supervise.sh run`.
- `DropInPaths=` was empty in the inspected output.
- The process listing associated PID `1556736` with
  `bash <platform-root>/MyTask/platform/vcc-backlog-supervise.sh run`; child
  board and relay processes were present for the registered projects.
- Inspected source hashes (for traceability only): supervisor
  `33270f4954647af0a0ded4cb0fb0eb5f135ac977cde5e5ac532e77c7893d1b7f` and
  `bb-ports.sh`
  `71f7f02811d9ffa2f282ef9526c2d7d278a2910a37fbf252423fc2fad08ba400`.

## Supervisor branches

The sanitized live source excerpt is retained in
`outputs/agentdock-governance-live-report.txt`.

- `heal_project` (lines 96–119) calls `bb_run stop "$name"`, sweeps stale
  PIDs for that port, calls `bb_run start "$name"`, and probes the selected
  bind/port. This supports project-scoped intent.
- The run loop (lines 140–218) polls each registered project, tracks retries,
  and invokes `heal_project "$name" "$port"`; it separately checks the
  dashboard and invokes `heal_dashboard`.
- Signal handlers set a trap; the loop then calls `do_stop` and exits 0. The
  `stop` entrypoint also calls `do_stop`, which stops all children.
- The effective unit has `Restart=always` and `KillMode=control-group`. Thus a
  unit/supervisor stop can affect the whole service group even though the
  health loop's normal project branch is scoped. No kill or failure drill was
  run.

## Dashboard coupling

The live `bb-ports.sh` excerpt (lines 304–334) shows `do_start` selecting a
project and running `start_one` for that project, then unconditionally running
`refresh_dashboard` and `start_dashboard`. The inspected `start_dashboard`
path manages the shared dashboard process. Therefore the repository supports
project selection, but the evidence does not support a claim of zero shared
impact: a project start/refresh can also refresh or restart the dashboard.
The stop branch (lines 336–365) is project-specific only when a project name is
provided, and otherwise stops all projects plus dashboard.

## Boundary

This evidence distinguishes current unit/source/PID observations from the
stronger claim that every running child has loaded the same source revision.
It does not establish browser acceptance, task ownership, or zero-downtime
recovery.
