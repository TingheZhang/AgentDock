# platform-core-ops

The current `main` contains the repaired AgentDock/VCC integration and the
installable `platform-core-ops` skill package. The skill installation consists
of `SKILL.md`, `references/`, and `scripts/`; `tools/health_check.py` is a
legacy compatibility entry point and is not part of the skill installation.

## Install this repaired package

Use the extracted package produced with this delivery. From the package root:

```bash
mkdir -p "${HOME}/.codex/skills/platform-core-ops"
cp -a SKILL.md references scripts "${HOME}/.codex/skills/platform-core-ops/"
```

For WorkBuddy, install to `~/.workbuddy/skills/platform-core-ops/` (or the
configured equivalent) with:

```bash
mkdir -p "$HOME/.workbuddy/skills/platform-core-ops"
cp -a SKILL.md references scripts "$HOME/.workbuddy/skills/platform-core-ops/"
```

Do not copy a parent directory into itself. This repository's current `main`
is the repaired version; install from the checked-out package or its released
archive.

Before operating a real platform, set `PLATFORM_ROOT` and derive registry/data
paths from the actual checkout. Read the platform and project `AGENTS.md`.
The package does not authorize service restarts, registry writes, note/Wiki
writes, or ingestion.

## Validation

```bash
python scripts/health_check.py --help
python scripts/health_check.py --self-test
# Supply at least one real target; replace <TAILSCALE_IP> with the actual
# configured address and quote URLs so shell metacharacters cannot redirect.
# Repeat --url for loopback and each configured proxy/Tailscale address.
python scripts/health_check.py --json --url "http://127.0.0.1:6422/" --url "http://<TAILSCALE_IP>:6422/" --mcp-url "http://127.0.0.1:8765/mcp" --unit vcc-readonly.service
```

Older callers may run `python tools/health_check.py --deep`. That entry point
delegates to the same `scripts/health_check.py` implementation, warns that
`--deep` is a legacy alias, and requires explicit targets; it does not retain a
second checker or invent registry/default endpoints.

The health script is a read-only transport/protocol diagnostic. Its success
does not establish browser layout, task-to-project ownership, Wiki freshness,
or completion of an acceptance task. Record exact probe URLs, phase, errors,
and artifact/version evidence in the project's CHANGELOG.

See `references/registry.md`, `references/knowledge.md`, and
`references/known-issues.md` only for the mode being performed.
