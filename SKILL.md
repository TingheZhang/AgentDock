---
name: platform-core-ops
description: >-
  Operate and diagnose a multi-project research platform with Backlog boards,
  a read-only notes and Wiki reader, and project-scoped code-graph snapshots.
  Use for platform health checks, project registration, service wiring, and
  deployment verification; do not use it as permission to mutate production.
---

# Platform core operations

This skill is an operations guide for the integration layer. It is not the
complete platform source tree. Read the applicable project `AGENTS.md` before
editing a project, and treat user-provided authorization as the scope for
mutations. A skill does not grant permission to restart services, edit data, or
write a registry.

## Facts that prevent silent failures

- A listening socket is not service health. Require an HTTP response and check
  the expected project or service identity.
- The effective user unit is under `~/.config/systemd/user/`; a repository unit
  is only a source file until copied and reloaded by an authorized operation.
- The current target is `vcc-platform.target`. The Backlog supervisor is
  `vcc-backlog.service`; reader, knowledge, and Basic Memory are separate
  units.
- Current board ports are registry data. Discover names, ports, bind addresses,
  and project paths from `ports.json`; do not hard-code a project name.
- Recovery is project-scoped in the current supervisor. The 2026-10-06 read-only
  unit/source check found the effective `Type=simple` and current `ExecStart`;
  `heal_project` calls `stop <name>` then `start <name>`. This is source and
  unit evidence, not a fault-drill guarantee. With `Restart=always` and
  `KillMode=control-group`, stopping the supervisor/unit can affect the whole
  service group, so zero interruption cannot be claimed. Inspect the service
  tool and restart only the affected project when that is authorized.
- Notes are Markdown source files. The main knowledge SQLite database is
  `wiki/knowledge.db`; each Wiki also has its own `index.db` in its Wiki data
  directory. These stores are distinct and are not automatically synchronized.

## Safe workflow

1. Read the applicable project and platform `AGENTS.md` and identify the
   owning repository, task ID, and allowed files.
2. Inspect the live unit and registry before choosing a command. Use `--help`
   for the installed `bb-ports.sh` interface; do not assume a release's CLI.
3. For a new project, use one registry path only: run the current
   `bb-ports.sh add <name> <path> <group> <port>` after confirming its help and
   authorization, then add the project's knowledge mapping through the
   approved registry workflow. Do not hand-add the same project first: `add`
   rejects duplicate names.
4. Verify loopback upstream and each configured proxy address separately with
   HTTP checks. A 200 response still needs an identity/content check. Resolve
   listening PIDs by matching the requested address and port; never select an
   arbitrary `head -1` PID when multiple listeners exist.
5. For changes, verify the real endpoint and record task ID, commit, run or
   deployment evidence, produced artifact/version, and browser or API
   acceptance evidence as applicable. A task status or merge alone is not a
   result. Append the project CHANGELOG according to its contract.

## References

- Read [references/registry.md](references/registry.md) before registry or
  project changes.
- Read [references/knowledge.md](references/knowledge.md) before notes, Wiki,
  MCP, or database work.
- Read [references/known-issues.md](references/known-issues.md) before
  recovery, proxy refresh, or deployment claims.
- Use [scripts/health_check.py](scripts/health_check.py) for transport and
  protocol checks. It is read-only and does not replace page, task ownership,
  or browser acceptance.


