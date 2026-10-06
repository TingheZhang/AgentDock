#!/usr/bin/env python3
"""
Aggregate multiple Backlog.md containers into a single static HTML dashboard.

Data sources are read-only: each source is a directory containing a Backlog.md
project (a `backlog/config.yml`). For every source we shell out to
`backlog task list --json` with BACKLOG_CWD pointed at it, then merge.

Design constraints:
- Read-only. Never writes into a source directory.
- Never trusts a task's `status` string as "done" (see the verify column).
- A source that is missing, unreadable, or whose CLI call fails is reported in
  the UI rather than silently dropped.

Usage:
    aggregate.py --config /path/to/sources.json --out /path/to/index.html
    aggregate.py --config ... --out ... --print   # dump merged JSON to stdout
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

# Where `npm i -g backlog.md` puts the shim on this host. A non-interactive
# SSH session does NOT source the profile that adds ~/.local/bin to PATH, so
# calling the bare name "backlog" fails with FileNotFoundError even though
# the CLI is installed and works fine from an interactive shell. Resolve it
# ourselves instead of trusting the caller's PATH.
BACKLOG_SEARCH_DIRS = (
    os.path.expanduser("~/.local/bin"),
    os.path.expanduser("~/bin"),
    "/usr/local/bin",
    "/usr/bin",
)


def backlog_cli():
    """Absolute path to the backlog CLI, or "backlog" to fall back to PATH."""
    exe = "backlog.cmd" if os.name == "nt" else "backlog"
    for d in BACKLOG_SEARCH_DIRS:
        p = os.path.join(d, exe)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    found = shutil.which("backlog")
    return found or "backlog"

# Statuses that mean "this agent declared it finished". We do NOT trust these
# as verified -- they are shown separately from the verify evidence.
DECLARED_DONE = {"done", "complete", "completed", "closed", "resolved"}

STATUS_ORDER = ["To Do", "In Progress", "Blocked", "In Review", "Done"]


def load_sources(path):
    """Load the project list.

    Accepts either a bare JSON array of sources, or a registry object of the
    form {"bind_ip": ..., "dashboard_port": ..., "projects": [...]}. The
    registry form is preferred because one file then drives both the port
    manager (bb-ports.sh) and this aggregator, so the two cannot disagree.
    """
    with open(path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    if isinstance(cfg, dict):
        cfg = cfg.get("projects", [])
    if not isinstance(cfg, list) or not cfg:
        raise ValueError(
            "config must be a non-empty JSON array of sources, or an object "
            'with a non-empty "projects" array'
        )
    return cfg


def load_registry_meta(path):
    """Return the non-project settings from a registry file ({} if absent)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(cfg, dict):
        return {}
    return {k: v for k, v in cfg.items() if k != "projects"}


def load_extra_links(path):
    """Return the registry's "extra_links" list (non-Backlog web entries).

    These are services that are reachable but are NOT Backlog.md projects,
    so they have no container, no tasks and no ui_port to start. They still
    belong on the index -- a dashboard that hides a running service is
    worse than no dashboard. Each entry:

        name  label shown on the tile
        port  TCP port; the link is built from the registry bind_ip
        desc  one-line description
        group optional heading, defaults to "Other services"

    A malformed entry is skipped rather than fatal: the dashboard must
    still render if one hand-written link is wrong.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(cfg, dict):
        return []
    raw = cfg.get("extra_links") or []
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        try:
            port = int(item.get("port"))
        except (TypeError, ValueError):
            continue
        if not name or not (0 < port < 65536):
            continue
        out.append({
            "name": str(name),
            "port": port,
            "desc": str(item.get("desc") or ""),
            "group": str(item.get("group") or "Other services"),
        })
    return out


def read_one_source(src):
    """Return a normalized source record. Never raises."""
    path = src.get("path")
    name = src.get("name") or (os.path.basename(path.rstrip("/")) if path else "?")
    rec = {
        "name": name,
        "path": path,
        "group": src.get("group", "default"),
        # Port of the Backlog.md Web UI for this project, so the index page
        # can link straight to the official board UI.
        "uiPort": src.get("ui_port"),
        "ok": False,
        "error": None,
        "projectName": None,
        # Optional registry-level override for the tile heading. Needed for
        # worktrees: every worktree of a project carries the same
        # config.yml, so project_name alone renders N tiles all called
        # "Example Recombination Study" with nothing to tell them apart.
        "displayName": src.get("display_name") or None,
        "isWorktree": bool(src.get("worktree")),
        "statuses": [],
        "port": None,
        "tasks": [],
        "elapsedMs": 0,
    }
    if not path or not os.path.isdir(path):
        rec["error"] = "directory not found: %s" % path
        return rec
    if find_backlog_dir(path) is None:
        rec["error"] = "not a Backlog.md project (no backlog/ or .backlog/config.yml)"
        return rec

    env = dict(os.environ)
    env["BACKLOG_CWD"] = path
    t0 = time.time()
    try:
        # Config comes from the file: `backlog config list --json` exits 1.
        cfg = read_config_file(path)
        rec["projectName"] = cfg.get("project_name") or None
        statuses = cfg.get("statuses") or []
        rec["statuses"] = [s for s in statuses if isinstance(s, str)] if isinstance(statuses, list) else []
        rec["port"] = cfg.get("default_port") or 6420

        # Tasks
        proc = subprocess.run(
            [backlog_cli(), "task", "list", "--json"],
            env=env, capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            rec["error"] = "backlog task list failed: %s" % (
                (proc.stderr or proc.stdout).strip()[:300]
            )
            return rec
        payload = json.loads(proc.stdout)
        tasks = payload.get("tasks", []) if isinstance(payload, dict) else payload
        tasks = [t for t in tasks if isinstance(t, dict)]

        # `task list --json` omits acceptance criteria and DoD items, and its
        # rawContent lacks them too. `task view --json` returns both as
        # structured arrays ({index, text, checked}) but an empty rawContent.
        # So enrich per task via task view rather than parsing markdown.
        for t in tasks:
            tid = t.get("id")
            if not tid:
                continue
            detail = fetch_task_detail(env, tid)
            if not detail:
                continue
            ac = detail.get("acceptanceCriteria")
            if isinstance(ac, list) and ac:
                t["acceptanceCriteriaItems"] = [
                    {"index": it.get("index"), "text": it.get("text") or "",
                     "checked": bool(it.get("checked"))}
                    for it in ac if isinstance(it, dict)
                ]
                t["acceptanceCriteriaCount"] = len(t["acceptanceCriteriaItems"])
                t["acceptanceCriteriaCompleted"] = sum(
                    1 for it in t["acceptanceCriteriaItems"] if it["checked"]
                )
            dod = detail.get("definitionOfDone")
            if isinstance(dod, list) and dod:
                t["definitionOfDoneItems"] = [
                    {"index": it.get("index"), "text": it.get("text") or "",
                     "checked": bool(it.get("checked"))}
                    for it in dod if isinstance(it, dict)
                ]
            # Readiness is only present on the detail view.
            for key in ("readiness", "dependencyGraph", "subtasks", "documentation"):
                if detail.get(key) is not None:
                    t[key] = detail[key]

        rec["tasks"] = [normalize_task(t) for t in tasks]
        rec["ok"] = True
    except subprocess.TimeoutExpired:
        rec["error"] = "timeout talking to backlog CLI"
    except json.JSONDecodeError as exc:
        rec["error"] = "bad JSON from backlog CLI: %s" % exc
    except Exception as exc:  # noqa: BLE001 - surface anything to the UI
        rec["error"] = "%s: %s" % (type(exc).__name__, exc)

    rec["elapsedMs"] = int((time.time() - t0) * 1000)
    return rec


def parse_scalar(raw):
    """Minimal YAML scalar reader. Handles quoted and bare values.

    We only read a handful of flat keys out of Backlog.md's config.yml, so a
    full YAML dependency would be overkill. Lists are returned as strings here
    because every list we consume is informational (statuses).
    """
    v = raw.strip()
    if not v:
        return ""
    if v[0] in "\"'" and len(v) > 1 and v[-1] == v[0]:
        v = v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [p.strip().strip("\"'") for p in inner.split(",") if p.strip()]
    return v


def find_backlog_dir(path):
    """Locate the Backlog.md container inside a project directory.

    Backlog.md only ever looks *inside* the project (init rejects any
    `--backlog-dir` that escapes it), so exactly two layouts exist:
    `backlog/` (default) and `.backlog/` (from `--backlog-dir .backlog`).
    Returns the subdirectory name, or None when the project is not initialised.
    """
    for sub in ("backlog", ".backlog"):
        if os.path.isfile(os.path.join(path, sub, "config.yml")):
            return sub
    return None


def read_config_file(path):
    """Read the Backlog.md config.yml directly. There is no `config list --json`
    (it exits non-zero), so parsing the file is the reliable route.
    Accepts both the `backlog/` and `.backlog/` layouts."""
    out = {}
    sub = find_backlog_dir(path)
    if sub is None:
        return out
    cfg_path = os.path.join(path, sub, "config.yml")
    try:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                if ":" not in line:
                    continue
                # Only top-level keys (no leading indent) to avoid nested maps.
                if line[0] in " \t":
                    continue
                key, _, val = line.partition(":")
                out[key.strip()] = parse_scalar(val)
    except OSError:
        return out
    return out


def fetch_task_detail(env, task_id):
    """Fetch one task's detail JSON. Returns None on any failure."""
    try:
        proc = subprocess.run(
            [backlog_cli(), "task", "view", task_id, "--json"],
            env=env, capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            return None
        payload = json.loads(proc.stdout)
        if isinstance(payload, dict):
            return payload.get("task", payload)
    except Exception:  # noqa: BLE001 - enrichment is best-effort
        return None
    return None


def normalize_task(t):
    """Flatten one task into what the dashboard needs.

    `declaredDone` is deliberately kept apart from `verified`: the status field
    is a plain string any agent can edit, so the UI must never render it as
    proof. `verified` is null until a verification step exists.
    """
    ac_total = t.get("acceptanceCriteriaCount") or 0
    ac_done = t.get("acceptanceCriteriaCompleted") or 0
    ac_items = [
        {"text": it.get("text") or "", "checked": bool(it.get("checked"))}
        for it in (t.get("acceptanceCriteriaItems") or [])
        if isinstance(it, dict)
    ]
    dod = [
        {"text": it.get("text") or "", "checked": bool(it.get("checked"))}
        for it in (t.get("definitionOfDoneItems") or [])
        if isinstance(it, dict)
    ]
    readiness = t.get("readiness") or {}
    status = t.get("status") or "To Do"
    return {
        "id": t.get("id"),
        "title": t.get("title") or "(untitled)",
        "status": status,
        "project": t.get("project"),
        "assignees": t.get("assignees") or [],
        "labels": t.get("labels") or [],
        "milestone": t.get("milestone"),
        "dependencies": t.get("dependencies") or [],
        "acDone": ac_done,
        "acTotal": ac_total,
        "acItems": ac_items,
        "dodDone": sum(1 for d in dod if d["checked"]),
        "dodTotal": len(dod),
        "dodItems": dod,
        "isReady": t.get("isReady"),
        "isBlocked": readiness.get("isBlocked"),
        "blockingDeps": readiness.get("blockingDependencies") or [],
        "missingDeps": readiness.get("missingDependencies") or [],
        "declaredDone": status.strip().lower() in DECLARED_DONE,
        "verified": None,   # no verification tool wired up yet
        "path": t.get("path"),
        "updatedAt": t.get("updatedAt"),
    }


def extract_dod(raw):
    """Deprecated: kept only to document why we do not parse markdown.

    `task view --json` exposes `definitionOfDone` as structured objects and
    returns an empty `rawContent`, so markdown scraping would be both fragile
    and unnecessary. Do not reintroduce this.
    """
    raise NotImplementedError("use task view --json definitionOfDone instead")


def _extract_dod_dead_reference(raw):
    items = []
    inside = False
    for line in raw.splitlines():
        if "DOD:BEGIN" in line:
            inside = True
            continue
        if "DOD:END" in line:
            inside = False
            continue
        if not inside:
            continue
        line = line.strip()
        if not line.startswith("- [") or "]" not in line:
            continue
        checked = line[3] in ("x", "X")
        text = line.split("]", 1)[1].strip()
        if text.startswith("#"):
            parts = text.split(None, 1)
            text = parts[1].strip() if len(parts) > 1 else text
        items.append({"checked": checked, "text": text})
    return items


def build_model(sources):
    records = [read_one_source(s) for s in sources]
    all_tasks = []
    for rec in records:
        for t in rec["tasks"]:
            t["sourceName"] = rec["name"]
            t["sourceGroup"] = rec["group"]
            all_tasks.append(t)
    return {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "generatedEpoch": int(time.time()),
        "sources": records,
        "tasks": all_tasks,
        "stats": build_stats(all_tasks, records),
    }


def build_stats(tasks, records):
    healthy = sum(1 for r in records if r["ok"])
    return {
        "sources": len(records),
        "sourcesOk": healthy,
        "sourcesFailed": len(records) - healthy,
        "tasks": len(tasks),
        "blocked": sum(1 for t in tasks if t.get("isBlocked")),
        "declaredDone": sum(1 for t in tasks if t["declaredDone"]),
        "notReady": sum(1 for t in tasks if t.get("isReady") is False),
    }


def esc(s):
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def render_html(model, refresh_minutes=0, extra_links=None):
    tasks = model["tasks"]
    stats = model["stats"]
    sources = model["sources"]
    extra_links = extra_links or []

    # Index page: one card per project, linking to that project's own Web UI.
    entries = []
    for rec in sources:
        entries.append(render_project_entry(rec, tasks))
    if not entries:
        entries.append(
            '<p class="empty">No projects configured. Add an entry to the '
            "sources config file.</p>"
        )

    groups = {}
    for rec in sources:
        groups.setdefault(rec["group"], []).append(rec)
    group_blocks = []
    for g, recs in groups.items():
        group_blocks.append(
            '<div class="pgroup"><h2>{g}<span class="cnt">{n} projects</span>'
            '</h2><div class="pcards">{cards}</div></div>'.format(
                g=esc(g), n=len(recs), cards="".join(render_project_entry(r, tasks) for r in recs)
            )
        )

    # Non-Backlog services (wiki reader, notes, MCP endpoints...). Grouped
    # separately so they never get confused with a real Backlog project.
    extra_groups = {}
    for link in extra_links:
        extra_groups.setdefault(link["group"], []).append(link)
    extra_blocks = []
    for g, links in extra_groups.items():
        cards = "".join(
            '<a class="link xlink" href="http://{host}:{p}" target="_blank" '
            'rel="noopener" title="{name}">'
            '<h3>{name}</h3>'
            '<div class="open">Open<span class="port">:{p}</span></div>'
            '<div class="xdesc">{desc}</div></a>'.format(
                host=UI_HOST, p=esc(ln["port"]), name=esc(ln["name"]),
                desc=esc(ln["desc"]),
            )
            for ln in links
        )
        extra_blocks.append(
            '<div class="pgroup"><h2>{g}<span class="cnt">{n} services</span>'
            '</h2><div class="pcards">{cards}</div></div>'.format(
                g=esc(g), n=len(links), cards=cards
            )
        )

    auto_refresh = ""
    if refresh_minutes > 0:
        secs = int(refresh_minutes * 60)
        auto_refresh = (
            '<meta http-equiv="refresh" content="%d">'
            '<p class="auto-refresh">Auto-refreshing every %d min</p>'
            % (secs, refresh_minutes)
        )

    source_rows = "".join(
        '<tr class="{cls}"><td class="sname">{name}</td>'
        '<td class="spath">{path}</td><td>{count}</td><td>{status}</td></tr>'.format(
            cls="src-ok" if r["ok"] else "src-bad",
            name=esc(r["name"]),
            path=esc(r["path"]),
            count=len(r["tasks"]),
            status="ok" if r["ok"] else esc(r["error"] or "failed"),
        )
        for r in sources
    )

    # The template uses @OPEN@/@CLOSE@ for CSS braces and __TOKEN__ for
    # substitutions, so neither CSS nor the substituted HTML needs escaping
    # through str.format().
    out = INDEX_TEMPLATE.replace("@OPEN@", "{").replace("@CLOSE@", "}")
    out = out.replace("__GENERATED_AT__", esc(model["generatedAt"]))
    out = out.replace("__AUTO_REFRESH__", auto_refresh)
    out = out.replace("__STAT_SOURCES__", "%d / %d" % (stats["sourcesOk"], stats["sources"]))
    out = out.replace("__STAT_TASKS__", str(stats["tasks"]))
    out = out.replace("__STAT_BLOCKED__", str(stats["blocked"]))
    out = out.replace("__STAT_DONE__", str(stats["declaredDone"]))
    out = out.replace("__STAT_NOTREADY__", str(stats["notReady"]))
    out = out.replace("__SOURCE_ROWS__", source_rows)
    out = out.replace("__GROUPS__", "".join(group_blocks))
    out = out.replace("__EXTRA__", "".join(extra_blocks))
    return out


def render_project_entry(rec, all_tasks):
    """One project tile. The whole tile is a link when a ui_port is known."""
    mine = [t for t in all_tasks if t["sourceName"] == rec["name"]]
    n_tasks = len(mine)
    n_done = sum(1 for t in mine if t["declaredDone"])
    n_blocked = sum(1 for t in mine if t.get("isBlocked"))
    ac_done = sum(t["acDone"] for t in mine)
    ac_total = sum(t["acTotal"] for t in mine)

    display = rec["displayName"] or rec["projectName"] or rec["name"]

    stats_bits = []
    stats_bits.append('<span class="pill">{n} tasks</span>'.format(n=n_tasks))
    if ac_total:
        stats_bits.append(
            '<span class="pill">AC {d}/{t}</span>'.format(d=ac_done, t=ac_total)
        )
    if n_blocked:
        stats_bits.append(
            '<span class="pill bad">{n} blocked</span>'.format(n=n_blocked)
        )
    if n_done:
        stats_bits.append(
            '<span class="pill warn">{n} declared done</span>'.format(n=n_done)
        )

    if rec["ok"]:
        pills = "".join('<span class="pwrap">%s</span>' % s for s in stats_bits)
        if rec["uiPort"]:
            open_ui = (
                '<div class="open">Open board &rarr;'
                '<span class="port">:{p}</span></div>'.format(p=esc(rec["uiPort"]))
            )
            body = (
                '<a class="link" href="http://{host}:{p}" target="_blank" '
                'rel="noopener" title="Open {name} in Backlog.md">'
                '<h3>{disp}</h3>{pills}{open}</a>'
            ).format(
                host=UI_HOST, p=esc(rec["uiPort"]), disp=esc(display),
                name=esc(display), pills=pills, open=open_ui,
            )
            state = "proj ok linked"
        else:
            open_ui = (
                '<div class="open muted">no ui_port set &mdash; not linkable</div>'
            )
            # For worktrees there is no UI to click through to, so the
            # titles have to be visible right here. A count alone ("1
            # tasks") says nothing about what is being worked on, which
            # defeats the point of tracking agents on a shared board.
            listing = ""
            if rec.get("isWorktree") and mine:
                items = "".join(
                    '<li><span class="tid">{tid}</span> {title}</li>'.format(
                        tid=esc(t.get("id", "?")), title=esc(t.get("title", "")),
                    )
                    for t in mine[:12]
                )
                more = (
                    '<li class="more">&hellip; and {n} more</li>'.format(n=len(mine) - 12)
                    if len(mine) > 12 else ""
                )
                listing = '<ul class="wt-tasks">{items}{more}</ul>'.format(
                    items=items, more=more
                )
            body = '<div class="link nolink"><h3>{disp}</h3>{pills}{open}{listing}</div>'.format(
                disp=esc(display), pills=pills, open=open_ui, listing=listing,
            )
            state = "proj ok"
        return '<article class="{state}">{body}<div class="path">{p}</div></article>'.format(
            state=state, body=body, p=esc(rec["path"]),
        )

    return (
        '<article class="proj bad"><div class="link nolink"><h3>{disp}</h3>'
        '<span class="pill bad">unavailable</span>'
        '<div class="open muted">{err}</div></div>'
        '<div class="path">{path}</div></article>'
    ).format(
        disp=esc(display), err=esc(rec["error"] or "failed"), path=esc(rec["path"])
    )


# Host used for building links to each project's Web UI. Resolution order:
#   1. --bind-ip argument (or BB_UI_HOST env)
#   2. "bind_ip" in the registry file  <- keeps links correct after a LAN
#      address change without editing code
#   3. the hardcoded default
DEFAULT_UI_HOST = "<LAN_IP>"
UI_HOST = os.environ.get("BB_UI_HOST") or DEFAULT_UI_HOST


def render_card(t):
    """Kept for potential drill-down views. The index no longer renders cards
    inline; each project links to its own Backlog.md Web UI instead."""
    ac_ratio = (t["acDone"] / t["acTotal"]) if t["acTotal"] else None
    dod_ratio = (t["dodDone"] / t["dodTotal"]) if t["dodTotal"] else None

    badges = ['<span class="badge src">{src}</span>'.format(src=esc(t["sourceName"]))]
    if t.get("project"):
        badges.append(
            '<span class="badge proj">{p}</span>'.format(p=esc(t["project"]))
        )
    if t.get("milestone"):
        badges.append(
            '<span class="badge mile">{m}</span>'.format(m=esc(t["milestone"]))
        )
    if t.get("isBlocked"):
        badges.append(
            '<span class="badge blocked">blocked by {n}</span>'.format(
                n=esc(", ".join(t["blockingDeps"]) or "?")
            )
        )
    if t.get("isReady") is False:
        badges.append('<span class="badge wait">waiting on deps</span>')

    ac = '<span class="metric muted">no AC</span>'
    if ac_ratio is not None:
        ac = '<span class="metric">AC {d}/{t}</span>'.format(d=t["acDone"], t=t["acTotal"])
    dod = '<span class="metric muted">no DoD</span>'
    if dod_ratio is not None:
        dod = '<span class="metric">DoD {d}/{t}</span>'.format(
            d=t["dodDone"], t=t["dodTotal"]
        )

    # The declared status is shown as a claim, never as verification.
    if t["declaredDone"]:
        verify = (
            '<div class="verify warn" title="This is only a status string the '
            'agent wrote. No verification has run.">declared done '
            "&mdash; not verified</div>"
        )
    else:
        verify = '<div class="verify">not declared done</div>'

    ac_items = ""
    if t["acItems"]:
        lis = "".join(
            '<li class="{c}">{t}</li>'.format(
                c="ok" if a["checked"] else "todo", t=esc(a["text"])
            )
            for a in t["acItems"]
        )
        ac_items = (
            '<div class="acbox"><div class="aclabel">Acceptance criteria</div>'
            '<ul class="dod">{lis}</ul></div>'.format(lis=lis)
        )

    dod_items = ""
    if t["dodItems"]:
        lis = "".join(
            '<li class="{c}">{t}</li>'.format(
                c="ok" if d["checked"] else "todo", t=esc(d["text"])
            )
            for d in t["dodItems"]
        )
        dod_items = (
            '<div class="acbox"><div class="aclabel">Definition of Done</div>'
            '<ul class="dod">{lis}</ul></div>'.format(lis=lis)
        )

    deps = ""
    if t["dependencies"]:
        deps = '<div class="deps">waits on {d}</div>'.format(
            d=esc(", ".join(t["dependencies"]))
        )

    who = ""
    if t["assignees"]:
        who = '<div class="who">{a}</div>'.format(a=esc(", ".join(t["assignees"])))

    return (
        '<article class="card" data-id="{id}">'
        '<header><span class="tid">{id}</span><h4>{title}</h4></header>'
        '<div class="badges">{badges}</div>'
        '<div class="metrics">{ac}{dod}</div>'
        '{verify}{ac_items}{dod_items}{deps}{who}'
        "</article>"
    ).format(
        id=esc(t["id"]),
        title=esc(t["title"]),
        badges="".join(badges),
        ac=ac,
        dod=dod,
        verify=verify,
        ac_items=ac_items,
        dod_items=dod_items,
        deps=deps,
        who=who,
    )


INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backlog projects</title>
<style>
  :root @OPEN@
    --bg: #ffffff;
    --fg: #1f2328;
    --muted: #656d76;
    --border: #d0d7de;
    --surface: #f6f8fa;
    --accent: #0969da;
    --warn-bg: #fff8c5;
    --warn-fg: #7d4e00;
    --ok: #1a7f37;
    --bad: #cf222e;
  @CLOSE@
  * @OPEN@ box-sizing: border-box; @CLOSE@
  body @OPEN@
    margin: 0; padding: 28px 22px 44px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans CJK SC", sans-serif;
    background: #f5f7fa; color: var(--fg); font-size: 14px; line-height: 1.6;
  @CLOSE@
  .wrap @OPEN@ max-width: 1160px; margin: 0 auto; @CLOSE@
  h1 @OPEN@ font-size: 24px; font-weight: 650; margin: 0 0 4px; letter-spacing: -.02em; @CLOSE@
  .sub @OPEN@ color: var(--muted); font-size: 13px; margin-bottom: 24px; @CLOSE@
  .stats @OPEN@ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 26px; @CLOSE@
  .stat @OPEN@
    border: 1px solid var(--border); border-radius: 9px;
    padding: 8px 13px; background: #fff; min-width: 92px;
  @CLOSE@
  .stat .n @OPEN@ font-size: 18px; font-weight: 600; display: block; @CLOSE@
  .stat .k @OPEN@ font-size: 12px; color: var(--muted); @CLOSE@
  h2 @OPEN@ font-size: 15px; font-weight: 600; margin: 26px 0 12px;
       padding-bottom: 6px; border-bottom: 1px solid var(--border); @CLOSE@
  .cnt @OPEN@ color: var(--muted); font-weight: 400; font-size: 13px; margin-left: 8px; @CLOSE@
  .pcards @OPEN@ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 14px; @CLOSE@
  .proj @OPEN@ border: 1px solid var(--border); border-radius: 10px; background: #fff;
          overflow: hidden; @CLOSE@
  .proj.linked @OPEN@ transition: border-color .12s ease, box-shadow .12s ease; @CLOSE@
  .proj.linked:hover @OPEN@ border-color: var(--accent); box-shadow: 0 1px 6px rgba(9,105,218,.16); @CLOSE@
  .proj.bad @OPEN@ border-color: #ffcecb; background: #fff8f7; @CLOSE@
  .link @OPEN@ display: block; padding: 16px 18px 14px; color: inherit; text-decoration: none; @CLOSE@
  a.link:hover h3 @OPEN@ color: var(--accent); text-decoration: underline; @CLOSE@
  .link.nolink @OPEN@ cursor: default; @CLOSE@
  .link h3 @OPEN@ font-size: 16px; font-weight: 600; margin: 0 0 10px; @CLOSE@
  .pwrap @OPEN@ display: inline-block; margin: 0 6px 6px 0; @CLOSE@
  .pill @OPEN@ font-size: 12px; padding: 2px 9px; border-radius: 20px;
          border: 1px solid var(--border); color: var(--muted); background: var(--surface); @CLOSE@
  .pill.bad @OPEN@ background: #ffebe9; color: #82071e; border-color: #ff8182; @CLOSE@
  .pill.warn @OPEN@ background: var(--warn-bg); color: var(--warn-fg); border-color: #d4a72c; @CLOSE@
  .open @OPEN@ font-size: 13px; color: var(--accent); margin-top: 10px; @CLOSE@
  .open.muted @OPEN@ color: var(--muted); @CLOSE@
  /* Non-Backlog services: same tile language, dashed border to signal
     "this is not a project board". */
  a.xlink @OPEN@ border: 1px dashed var(--border); border-radius: 10px;
            background: #fbfcfe; transition: border-color .12s ease, box-shadow .12s ease; @CLOSE@
  a.xlink:hover @OPEN@ border-color: var(--accent); box-shadow: 0 1px 6px rgba(9,105,218,.16); @CLOSE@
  .xdesc @OPEN@ font-size: 13px; color: var(--muted); margin-top: 8px; line-height: 1.45; @CLOSE@
  .port @OPEN@ font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          color: var(--muted); font-size: 12px; @CLOSE@
  .wt-tasks @OPEN@ list-style:none; margin:.5rem 0 0; padding:0;
    font-size:.82rem; line-height:1.45; color:#4a5568; @CLOSE@
  .wt-tasks li @OPEN@ padding:.15rem 0; border-top:1px solid rgba(0,0,0,.06); @CLOSE@
  .wt-tasks .tid @OPEN@ display:inline-block; min-width:4.5rem;
    font-weight:600; color:#2b6cb0; @CLOSE@
  .wt-tasks .more @OPEN@ color:#718096; font-style:italic; @CLOSE@
  .path @OPEN@ font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: 11px; color: var(--muted); background: var(--surface);
          padding: 7px 18px; border-top: 1px solid var(--border);
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap; @CLOSE@
  .empty @OPEN@ color: var(--muted); padding: 24px 0; @CLOSE@
  .auto-refresh @OPEN@ font-size: 12px; color: var(--muted); margin: 0 0 14px; @CLOSE@
  table.sources @OPEN@ border-collapse: collapse; width: 100%; font-size: 12px; margin-bottom: 10px; @CLOSE@
  table.sources td, table.sources th @OPEN@
    border: 1px solid var(--border); padding: 5px 8px; text-align: left; @CLOSE@
  table.sources th @OPEN@ background: var(--surface); font-weight: 600; @CLOSE@
  td.sname @OPEN@ font-weight: 500; @CLOSE@
  td.spath @OPEN@ font-family: ui-monospace, Menlo, monospace; color: var(--muted); @CLOSE@
  tr.src-ok td:last-child @OPEN@ color: var(--ok); @CLOSE@
  tr.src-bad td:last-child @OPEN@ color: var(--bad); @CLOSE@
  details @OPEN@ margin-top: 30px; @CLOSE@
  summary @OPEN@ cursor: pointer; font-size: 13px; color: var(--accent); @CLOSE@
  .hint @OPEN@ font-size: 12px; color: var(--muted); margin-top: 18px;
          border-left: 2px solid var(--border); padding-left: 10px; @CLOSE@
</style>
</head>
<body>
<div class="wrap">
<h1>项目工作台</h1>
<p class="sub">任务进度按项目分开管理 · 知识面板在 6424 按项目归属展示 · 生成于 __GENERATED_AT__</p>
__AUTO_REFRESH__
<div class="stats">
  <div class="stat"><span class="n">__STAT_SOURCES__</span><span class="k">projects ok</span></div>
  <div class="stat"><span class="n">__STAT_TASKS__</span><span class="k">tasks</span></div>
  <div class="stat"><span class="n">__STAT_BLOCKED__</span><span class="k">blocked</span></div>
  <div class="stat"><span class="n">__STAT_DONE__</span><span class="k">declared done</span></div>
  <div class="stat"><span class="n">__STAT_NOTREADY__</span><span class="k">waiting on deps</span></div>
</div>

__GROUPS__

__EXTRA__

<p class="hint">Click a project to open its own Backlog.md board. Statuses shown
here are agent claims; a task marked &ldquo;declared done&rdquo; has not been
verified unless a check actually ran.</p>

<details>
  <summary>Data sources</summary>
  <table class="sources">
    <tr><th>source</th><th>path</th><th>tasks</th><th>status</th></tr>
    __SOURCE_ROWS__
  </table>
</details>
</div>
<script>
(function(){
  var host = window.location.hostname;
  if (!host) return;
  document.querySelectorAll('a[href]').forEach(function(a){
    try {
      var u = new URL(a.href, window.location.origin);
      if (u.port && u.port >= 6420 && u.port <= 6425) {
        u.hostname = host;
        a.href = u.toString();
      }
    } catch(e){}
  });
})();
</script>
</body>
</html>
"""


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Aggregate multiple Backlog.md projects into one HTML index."
    )
    ap.add_argument("--config", required=True, help="path to sources.json / ports.json")
    ap.add_argument("--out", required=True, help="output HTML path")
    ap.add_argument(
        "--bind-ip",
        dest="bind_ip",
        default=None,
        help="host to use in links to project Web UIs (default: from registry "
        "bind_ip, else BB_UI_HOST, else %s)" % DEFAULT_UI_HOST,
    )
    ap.add_argument(
        "--refresh-minutes",
        type=int,
        default=0,
        help="if >0, emit a <meta refresh> that reloads the page this often",
    )
    ap.add_argument(
        "--print",
        action="store_true",
        dest="do_print",
        help="dump the merged model as JSON to stdout instead of HTML",
    )
    args = ap.parse_args(argv)

    global UI_HOST
    registry = load_registry_meta(args.config)
    if args.bind_ip:
        UI_HOST = args.bind_ip.split(",")[0].strip()
    elif registry.get("bind_ip"):
        UI_HOST = str(registry["bind_ip"]).split(",")[0].strip()
    elif not os.environ.get("BB_UI_HOST"):
        UI_HOST = DEFAULT_UI_HOST

    sources = load_sources(args.config)
    extra_links = load_extra_links(args.config)
    model = build_model(sources)
    model["bindIp"] = UI_HOST
    model["dashboardPort"] = registry.get("dashboard_port")
    model["extraLinks"] = extra_links

    if args.do_print:
        sys.stdout.write(json.dumps(model, indent=2, ensure_ascii=False))
        sys.stdout.write("\n")
        return 0

    html = render_html(
        model, refresh_minutes=args.refresh_minutes, extra_links=extra_links
    )

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    # Write to a temp file then rename, so a reader (the LAN server) never
    # observes a half-written page.
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(html)
    os.replace(tmp, args.out)

    st = model["stats"]
    sys.stderr.write(
        "wrote %s (%d bytes) | sources %d/%d ok | tasks %d\n"
        % (
            args.out,
            len(html),
            st["sourcesOk"],
            st["sources"],
            st["tasks"],
        )
    )
    for r in model["sources"]:
        if not r["ok"]:
            sys.stderr.write("  ! source %s failed: %s\n" % (r["name"], r["error"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
