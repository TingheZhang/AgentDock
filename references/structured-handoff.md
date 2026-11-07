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
content. Parse only the `token` field from the owner-only JSON token file and
add it as a Bearer header in the client; never send the whole file or print
the token.

The transport is bounded single-worker per database. It does not provide
native Agent hooks, raw-session upload, automatic extraction, multi-worker
semantics, server/machine crash recovery, or global exactly-once guarantees.
