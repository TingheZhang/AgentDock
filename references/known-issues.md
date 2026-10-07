# Known limits and verification boundaries

- The current supervisor heals an unhealthy project individually. The
  2026-10-06 read-only unit/source check found `Type=simple`, the current
  `ExecStart`, and `heal_project` stop/start by project name. This does not
  constitute a fault drill or a guarantee of zero interruption: with
  `Restart=always` and `KillMode=control-group`, unit/supervisor stop can affect
  the whole service group.
- A proxy can be healthy on one bind address and unavailable on another. Probe
  loopback upstream, every configured proxy bind, and Tailscale/public paths
  separately when those paths are part of the deployment contract. A numeric IP
  does not require DNS; a timeout still needs network-path diagnosis.
- Generated dashboard HTML and injected board panels can be stale after source
  changes. In the inspected `bb-ports.sh`, `do_start <project>` still executes
  unconditional `refresh_dashboard` and `start_dashboard` after the selected
  project (lines 304-334 in the live evidence), so refreshing one project may
  restart the shared dashboard. Use the installed refresh mechanism for the
  smallest authorized scope, then inspect the live response.
- Code-graph snapshots are project-level artifacts. The reader's
  `/api/codegraph?name=<project>` route is not proof of a live or complete
  index, and a missing snapshot must remain an explicit unavailable state.
- `scripts/health_check.py` checks transport and selected protocol responses.
  It does not prove browser visibility, task ownership, project attribution,
  data freshness, or acceptance of a UI workflow.
