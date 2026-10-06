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
