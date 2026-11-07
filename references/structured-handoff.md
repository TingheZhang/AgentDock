# Structured handoff transport

Read this reference only for requests to discover or operate a configured
structured handoff transport. It stores structured session handoff pages in
an isolated ai-memory instance; formal knowledge remains in Basic Memory. A
request to inspect or route handoff data is not permission to send data
externally.

## Configured entrypoints

The deployment supplies one owner-bound enqueue/status wrapper per client.
Use the local wrapper installed by that deployment; do not copy credentials or
put token values in argv or logs. A Linux screen-managed worker is not
container-boot-autostart and requires an operator-controlled start after a
container restart. Check for an existing named session before starting a
second worker.

For a portable client without a wrapper, set a private client root such as
`$HANDOFF_ROOT` (mode 700), create `data/` and `credentials/` (mode 700),
and store a token file as `{"token":"<owner-token>"}` with mode 600. The
gateway root token file uses the same JSON shape and must remain server-only.
Prepare the ai-memory workspace/project on the server before starting the
gateway; keep gateway config, ledger and audit paths inside the private root,
bind the gateway to loopback, and use an operator-managed SSH/TLS tunnel if a
different host must reach it. Do not put tokens in commands.

Example client commands (replace placeholders with local paths and values;
never paste a token):

```bash
python tools/dual-memory-remote-pilot/outbox_client.py enqueue \
  --db "$HANDOFF_ROOT/data/outbox.sqlite" --owner "$OWNER" \
  --endpoint "$GATE_ENDPOINT" --payload payload.json
python tools/dual-memory-remote-pilot/outbox_client.py status \
  --db "$HANDOFF_ROOT/data/outbox.sqlite"
python tools/dual-memory-remote-pilot/outbox_client.py run \
  --db "$HANDOFF_ROOT/data/outbox.sqlite" --owner "$OWNER" \
  --endpoint "$GATE_ENDPOINT" --token "$HANDOFF_ROOT/credentials/token.json" \
  --deadline 300 --poll .5 --backoff .5 --max-backoff 30 --timeout 5
```

Run the bounded worker from an operator-owned process manager or a loop with
an explicit deadline; this client is not a daemon. `enqueue` is the send
operation, `status` exposes local queued/acked/terminal metadata, and the
receiving client performs GET `/handoff?event_id=<id>` with its own configured
credential to verify sender owner and content. A `queued` row is local only;
`acked` is the gateway receipt. The gateway must be started only after its
private root, server token file, and ai-memory workspace/project are prepared.

## Payload and readback

The payload is a JSON object with exactly eight string fields:
`event_id`, `session_id`, `title`, `summary`, `progress`, `next_step`,
`acceptance`, and `recipient`. It is a caller-provided structured summary,
not an automatic transcript or summary generator. Use a new `session_id` for a
new session. A retry keeps the same `event_id` and identical payload; do not
edit outbox state or change owner/endpoint binding.

`queued` means the handoff is waiting in the sender's local outbox; `acked`
means the configured gate confirmed receipt. The receiving client uses its
own deployed credential to read the sender's handoff by `event_id` through the
configured loopback gate GET endpoint, then verifies sender owner and handoff
content. Authenticated clients share the configured project's ID-based read
scope; `recipient` is handoff metadata, not an access-control list. Parse only the `token` field from the owner-only JSON token file and
add it as a Bearer header in the client; never send the whole file or print
the token.

The transport is bounded single-worker per database. It does not provide
native Agent hooks, raw-session upload, automatic extraction, multi-worker
semantics, server/machine crash recovery, or global exactly-once guarantees.
