---
name: platform-core-ops
description: >-
  Operate and diagnose a multi-project research platform with Backlog boards,
  a read-only notes and Wiki reader, project-scoped code-graph snapshots, and
  approved structured session-handoff transport discovery. Use for platform
  health checks, project registration, service wiring, deployment verification,
  or handoff routing; do not use it as permission to mutate production.
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
- The current target is `agentdock-platform.target`. The Backlog supervisor is
  `agentdock-backlog.service`; reader, knowledge, and Basic Memory are separate
  units.
- Current board ports are registry data. Discover names, ports, bind addresses,
  and project paths from `ports.json`; do not hard-code a project name.
- The project context map is derived from the active registry and current
  project roots; it is not a second Backlog or registry source. Keep the task
  directory, platform code root, notes/Wiki roots, and code-graph scope
  distinct.
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
6. For repeatable context checks, use the platform's `project_context.py`
   commands with the actual project discovered from the registry. The CLI
   resolves project/context roots from the registry; do not invent a second
   root flag. `map` reports entry paths; `provenance` separates inherited
   source from decision status; `snapshot` writes a versioned, bounded
   SHA-256 manifest; `check` compares it without writing, ingesting, or
   rebaselining. Missing baseline, permissions, or unavailable GUI evidence
   must remain `NOT_VERIFIED`, never a green result. Do not follow symlinks or
   include runtime state, tokens, large data, results, or lockbox files.
7. For structured handoff discovery, read
   [references/structured-handoff.md](references/structured-handoff.md) only
   when the request concerns the deployed handoff transport. It documents the
   installed entrypoints and safety boundary; it does not authorize enqueue,
   resend, service changes, or external sharing.

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
- Read [references/project-context.md](references/project-context.md) when
  building an entry map, recording historical decision provenance, or running
  the repeatable context snapshot/check workflow.


