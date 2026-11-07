# Parameterized structured gate

The existing gate keeps its legacy pilot defaults when started without flags.
For an isolated runtime, use an explicit JSON configuration:

```bash
python3 tools/dual-memory-remote-pilot/structured_gate.py --config /srv/ai-memory-handoff/gate.json
```

The config fixes root/data/credential/ledger/audit paths, backend URL, loopback
bind/port, workspace/project scope, and owner-to-token-file mappings. It fails
closed on missing paths, duplicate owners/tokens, non-loopback bind, invalid
ports, or non-HTTP backend URLs. Tokens remain in owner-only files and are not
written to HTTP responses or logs. The server is one process; no worker or
registry layer is introduced. The example config contains no credentials.
All authenticated clients for the configured project use the gate's ID-based
read contract; a payload's `recipient` field is metadata, not an ACL.

Before starting, create the configured root and its `data`, `credentials`,
and audit/ledger parent directories with owner-only permissions. Prepare the
named ai-memory workspace/project and a server-only JSON token file of the
form `{"token":"<server-token>"}`; do not commit or print its value. Start
the gateway only on its configured loopback bind. A remote client must use an
operator-managed SSH/TLS tunnel; this adapter does not create or authorize
network credentials.
