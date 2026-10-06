# Known limits and verification boundaries

- The current supervisor heals an unhealthy project individually. Do not
  describe recovery as a fixed-duration or all-board restart guarantee.
- A proxy can be healthy on one bind address and unavailable on another. Probe
  loopback upstream, every configured proxy bind, and Tailscale/public paths
  separately when those paths are part of the deployment contract. A numeric IP
  does not require DNS; a timeout still needs network-path diagnosis.
- Generated dashboard HTML and injected board panels can be stale after source
  changes. Use the installed refresh/restart mechanism for the smallest
  affected scope, then inspect the live response.
- Code-graph snapshots are project-level artifacts. The reader's
  `/api/codegraph?name=<project>` route is not proof of a live or complete
  index, and a missing snapshot must remain an explicit unavailable state.
- `scripts/health_check.py` checks transport and selected protocol responses.
  It does not prove browser visibility, task ownership, project attribution,
  data freshness, or acceptance of a UI workflow.
