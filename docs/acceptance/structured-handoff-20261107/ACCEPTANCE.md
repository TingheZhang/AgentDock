# Structured handoff acceptance summary

The prior private deployment adapter was validated with four synthetic Chinese
structured summaries across two owner-bound clients. Each payload used the
exact eight-field contract, was acknowledged once, and was read back with
owner, session, event, body, and page identity checks. Duplicate submission of
the same event and identical payload returned the expected duplicate result.

The public package's new verification is mock localhost coverage of the gate
and outbox client. Authenticated clients use the configured project's
ID-based read scope; `recipient` is metadata and is not an ACL.

The public package contains the generic gate adapter, configuration example,
tests, and this sanitized summary only. It excludes credentials, tokens,
SQLite databases, raw sessions, runtime logs, private host addresses,
absolute user paths, and page-level evidence from the deployment environment.
