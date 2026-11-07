# Changelog

## 2026-10-06 — repaired package

- Fixed invalid YAML frontmatter and removed unsupported `agent_created`.
- Added the missing registry, knowledge, known-issues, and health-check
  resources.
- Corrected the current target name, isolated recovery behavior, project-add
  duplicate handling, SQLite path distinction, and code-graph snapshot scope.
- Added read-only HTTP/MCP checks with negative controls and explicit limits.
- Tightened MCP lifecycle checking, removed guessed default endpoints, and
  rejected non-positive timeouts and incomplete tools/list responses.
- Added JSON-RPC version/error validation, finite-response scope, and invalid
  URL/NaN/Inf timeout handling.

## 2026-10-06 — governance repair on current main

- **Base:** `a2613d3d614812dec612ac1cee4f51c8929be9f3` (GitHub `main` at
  review start).
- Replaced the duplicate `tools/health_check.py` implementation with a
  compatibility delegator to `scripts/health_check.py`; legacy `--deep` now
  warns and requires explicit targets.
- Corrected current target and supervisor wording, added a historical marker to
  superseded review material, and removed tracked usernames, absolute local
  paths, and real IPs in favor of placeholders.
- Verification: quick validation, Python compile, canonical and legacy help /
  self-test, explicit-target negative controls, and read-only 174 MCP protocol
  probe. Live supervisor evidence is retained outside the package in
  `outputs/agentdock-governance-live-report.txt`.

## 2026-10-06 — unit boundary clarification

- Added sanitized read-only supervisor/unit evidence at
  the prior review artifact, including effective
  `Type=simple`/`ExecStart`, MainPID association, stop/start and signal/exit
  branches, and source hashes with the limitation that disk hashes do not
  prove every running child loaded that revision.
- Documented the real unit boundary: `Restart=always` plus
  `KillMode=control-group` means a unit/supervisor stop can affect the whole
  service group; no fault drill was run and zero interruption is not claimed.
- Documented that `bb-ports.sh do_start <project>` still refreshes and starts
  the shared dashboard after the selected project, so project recovery has a
  possible shared-dashboard impact.
- README now gives the concrete WorkBuddy path
  `~/.workbuddy/skills/platform-core-ops/` and quotes placeholder URLs.
## 2026-11-07 — structured handoff export

- Added the generic loopback structured-handoff gate adapter, configuration
  example, focused tests, handoff reference, and sanitized acceptance summary.
- Updated the platform skill to discover structured handoff routing without
  granting external-send or production-mutation permission.
- Export contains no deployment credentials, tokens, databases, raw sessions,
  runtime logs, private host addresses, or absolute user paths.

## 2026-11-07 — portable client follow-up

- Added the durable single-worker outbox client and mock regression coverage
  for enqueue, ACK/retry, status, owner binding, and gate readback workflows.
- Disabled ambient HTTP proxy use for loopback gate calls and documented
  generic token-file, workspace preparation, tunnel, and bounded-worker rules.

## 2026-11-07 - AgentDock debranding follow-up

- Removed the remaining legacy vendor-specific public paths and historical
  artifacts; retained the original LICENSE attribution unchanged.
- Clarified that systemd unit templates must be rendered with real absolute
  paths before installation, and documented the old-unit-to-AgentDock-unit
  migration order.
