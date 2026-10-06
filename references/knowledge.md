# Notes, Wiki, and MCP boundaries

The Markdown notes directory is the source of truth for notes. The main
TencentDB Agent Memory SQLite database is `wiki/knowledge.db`; the Wiki engine
keeps a separate `index.db` in each Wiki data directory for page/search index
state. Do not substitute one for the other or repair a stale copy by writing
to an unverified path.

The reader is read-only. A note or Wiki change is a separate authorized
operation and may require an explicit ingest or index step. Do not claim that
notes, Wiki pages, code snapshots, and tasks automatically propagate.

For Basic Memory MCP, initialize first and retain the returned session header.
Then send the `notifications/initialized` notification and call `tools/list`
with the negotiated `MCP-Protocol-Version` and session. A bare `tools/list`
request is not a valid health check. For JSON-RPC responses, require a
successful `result` with the expected protocol/capability fields; an HTTP 200
error envelope is a failure. See the [MCP lifecycle specification](https://modelcontextprotocol.io/specification/2025-03-26/basic/lifecycle)
for the protocol sequence and version negotiation. The bundled probe accepts
one finite JSON response or finite SSE data response; it is not a long-lived
stream client.

For Wiki or reader APIs, check the project and Wiki identity in the response.
Transport success does not prove attribution, freshness, or page correctness.
Do not expose service keys or bearer tokens in logs.

When notes overlap or ownership is disputed, follow the applicable project
`AGENTS.md` and existing ownership contract. Do not invent a locking service;
coordinate writes through the existing repository/agent workflow.
