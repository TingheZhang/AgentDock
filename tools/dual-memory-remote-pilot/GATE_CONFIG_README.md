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
