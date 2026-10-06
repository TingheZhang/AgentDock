# platform-core-ops

This directory is the repaired operations skill package for the AgentDock/VCC
platform. It is a skill and operations guide, not a complete platform source
checkout. It does not include `ports.json`, systemd units, task data, notes,
Wiki data, or the platform integration code.

## Install this repaired package

Use the extracted package produced with this delivery. From the package root:

```bash
mkdir -p "${HOME}/.codex/skills/platform-core-ops"
cp -a SKILL.md references scripts "${HOME}/.codex/skills/platform-core-ops/"
```

For WorkBuddy, use its configured skill directory and copy the same three
entries into a directory named `platform-core-ops`. Do not copy a parent
directory into itself and do not install the older GitHub `main` checkout as
the repaired version; that remote commit predates this fix. If a future
release publishes this package, follow that release's own install layout.

Before operating a real platform, set `PLATFORM_ROOT` and derive registry/data
paths from the actual checkout. Read the platform and project `AGENTS.md`.
The package does not authorize service restarts, registry writes, note/Wiki
writes, or ingestion.

## Validation

```bash
python scripts/health_check.py --help
python scripts/health_check.py --self-test
# Supply at least one real target; repeat --url for loopback and each
# configured proxy/Tailscale address.
python scripts/health_check.py --json --url http://127.0.0.1:6422/ --url http://100.82.18.91:6422/ --mcp-url http://127.0.0.1:8765/mcp --unit vcc-readonly.service
```

The health script is a read-only transport/protocol diagnostic. Its success
does not establish browser layout, task-to-project ownership, Wiki freshness,
or completion of an acceptance task. Record exact probe URLs, phase, errors,
and artifact/version evidence in the project's CHANGELOG.

See `references/registry.md`, `references/knowledge.md`, and
`references/known-issues.md` only for the mode being performed.
