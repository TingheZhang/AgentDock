#!/usr/bin/env python3
"""Compatibility entry point for the canonical scripts/health_check.py."""
from __future__ import annotations

import sys
from pathlib import Path


def main(argv=None):
    """Delegate every check to one implementation.

    Older notes invoked ``tools/health_check.py --deep``. The old deep mode
    had guessed endpoints and a stale target name; keep the flag parseable as
    a migration alias, but require the canonical script's explicit targets.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if "--deep" in args:
        args.remove("--deep")
        print("warning: --deep is a legacy alias; supply explicit --url/--mcp-url/--unit targets", file=sys.stderr)
    scripts = str(Path(__file__).resolve().parents[1] / "scripts")
    sys.path.insert(0, scripts)
    from health_check import main as canonical_main
    return canonical_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
