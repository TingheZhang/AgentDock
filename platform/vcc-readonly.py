#!/usr/bin/env python3
"""vcc-readonly -- a small read-only web UI for the two data stores that
have no web front end of their own.

Why this exists
---------------
The aggregate dashboard on 6421 renders Backlog.md projects, and the
registry it reads (ports.json) is explicitly the source of truth for
"Backlog.md ports". The other two stores on this host are not Backlog.md
projects and therefore never appear there:

  * 8421  Knowledge Service -- the LLM wiki. 28 POST endpoints under /v3.
           It serves Swagger UI at /docs but has no page you can read.
  * 8765  Basic Memory -- MCP only, no HTTP UI at all.

So a user landing on 6421 had no way to see either. This fills that gap
with one page that lists and renders both, and the dashboard links to it.

Design notes
------------
* Read-only, deliberately. Every call is a POST (the knowledge API has no
  GETs) but only /wiki/get, /wiki/page/ls, /wiki/page/read and /wiki/search
  are ever touched. No write, no delete, no ingest, no sync.
* Talks to the API rather than reading the .md files off disk. The files
  under MyTask/wiki are the wiki's storage, but the API is what the rest of
  the platform uses, and going through it keeps one code path.
* API_PREFIX is /v3. The spec at /openapi.json is YAML despite the name.
  Auth is Bearer KNOWLEDGE_SERVICE_KEY, read from the engine's .env -- this
  process is on the same host and the key is not exposed to the browser;
  the page fetches through this server, never straight from the browser.
* No build step and no framework: one file, stdlib only, same style as the
  other platform scripts.
"""
import errno
import argparse
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote
import threading
import time

KNOWLEDGE = "http://127.0.0.1:8421"
API_PREFIX = "/v3"
ENGINE_DIR = "$TDAM_DIR"
NOTES_DIR = "$PLATFORM_ROOT/memory"
CODEGRAPH_DIR = os.environ.get("VCC_CODEGRAPH_DIR", "$PLATFORM_ROOT/state/codegraph")
REGISTRY = os.environ.get("VCC_REGISTRY", "$PLATFORM_ROOT/platform/ports.json")
BACKLOG = os.environ.get("VCC_BACKLOG_BIN", "$HOME_DIR/.local/bin/backlog")
BIND = os.environ.get("VCC_READONLY_BIND", "0.0.0.0")
PORT = int(os.environ.get("VCC_READONLY_PORT", "6424"))
SERVICE_ID = os.environ.get("VCC_TEAM_SERVICE_ID", "")
TEAM_ID = os.environ.get("VCC_TEAM_ID", "")
# `backlog task list` is a subprocess and costs ~0.25s per project, so a
# page refresh used to fork one per project on every request. Memoise for a few
# seconds: long enough to collapse a burst of refreshes, short enough that a
# newly created task shows up quickly.
TASK_CACHE_TTL = float(os.environ.get("VCC_TASK_CACHE_TTL", "5"))
_TASK_CACHE = {}          # project name -> (expires_at, tasks, error)
_TASK_CACHE_LOCK = threading.Lock()
_TASK_GATES = {}          # project name -> Lock, for single-flight fetches
# Public host used in links. Kept as a constant because both the page header
# and the injected panel link out to sibling services; overridable so a
# test or a future move does not need a code change.
BIND_IP = os.environ.get("VCC_BIND_IP", "<LAN_IP>").split(",")[0].strip()


def load_projects():
    """Return the project -> knowledge mapping from the shared registry.

    ports.json is the single source of truth for which project owns which
    notes and wikis. Reading it here (instead of hardcoding a wiki_id and a
    notes dir) is what makes knowledge follow the project: adding a project
    to the registry is enough, with no code change here.

    Returns a list of:
        {name, group, port, note_subdirs, wikis, panel, projectName}
    """
    try:
        with open(REGISTRY, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return []
    out = []
    for p in (cfg.get("projects") or []):
        if not isinstance(p, dict):
            continue
        k = p.get("knowledge") or {}
        if not isinstance(k, dict):
            k = {}
        subdirs = k.get("note_subdirs") or []
        if not isinstance(subdirs, list):
            subdirs = []
        wikis = k.get("wikis") or []
        if not isinstance(wikis, list):
            wikis = []
        out.append({
            "name": str(p.get("name") or ""),
            "path": str(p.get("path") or ""),
            "group": str(p.get("group") or ""),
            "port": p.get("ui_port"),
            "projectName": _config_name(p.get("path")),
            "note_subdirs": [str(s) for s in subdirs],
            "wikis": [str(w) for w in wikis],
            "panel": bool(k.get("panel")),
        })
    return [p for p in out if p["name"]]


def _config_name(path):
    """project_name from a Backlog container, or '' if unreadable."""
    if not path:
        return ""
    for sub in (".backlog", "backlog"):
        cfg = os.path.join(str(path), sub, "config.yml")
        try:
            with open(cfg, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("project_name:"):
                        return line.split(":", 1)[1].strip().strip("\"'")
        except OSError:
            continue
    return ""

# Wiki page types get a colour chip; anything else falls back to neutral.
TYPE_LABEL = {
    "concept": "概念",
    "entity": "实体",
    "source": "来源",
    "overview": "概览",
    "purpose": "purpose",
    "schema": "schema",
    "index": "索引",
    "log": "日志",
    "comparison": "对比",
    "synthesis": "综述",
}


def service_key():
    """Read the knowledge service key from the engine's .env.

    Cached in a module global because it is read on every proxied call.
    """
    global _SVC_KEY
    if _SVC_KEY is not None:
        return _SVC_KEY
    _SVC_KEY = ""
    try:
        with open(os.path.join(ENGINE_DIR, ".env"), encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("KNOWLEDGE_SERVICE_KEY="):
                    _SVC_KEY = line.split("=", 1)[1].strip()
                    break
    except OSError:
        pass
    return _SVC_KEY


_SVC_KEY = None


def api(path, payload, timeout=20):
    """POST to the knowledge API. Returns (status, parsed_body_or_text)."""
    url = f"{KNOWLEDGE}{API_PREFIX}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("x-tdai-service-id", SERVICE_ID)
    key = service_key()
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


class BadRequest(Exception):
    """Client-side input error (bad ref, path traversal) -> HTTP 400."""


class UpstreamError(Exception):
    """The knowledge API misbehaved -> HTTP 502."""


def api_data(path, payload, timeout=20):
    """Call the API and return just the data field, or raise UpstreamError."""
    status, body = api(path, payload, timeout)
    if status != 200:
        raise UpstreamError(f"HTTP {status}: {str(body)[:200]}")
    if isinstance(body, dict):
        if body.get("code") not in (0, None):
            raise UpstreamError(f"code={body.get('code')}: {body.get('message')}")
        return body.get("data")
    raise UpstreamError(f"unexpected body: {str(body)[:200]}")


def api_health():
    try:
        req = urllib.request.Request(f"{KNOWLEDGE}/health")
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- markdown

_FENCE = re.compile(r"^```", re.M)


def md_to_html(md, project="", wiki=""):
    """A deliberately small markdown subset renderer.

    Handles what the wiki and the notes actually use: headings, fenced code,
    tables, lists, blockquotes, [[wiki links]], bold, inline code, and
    paragraphs. Anything else is escaped and passed through as text, which
    is the safe default for content we did not write.
    """
    if not md:
        return ""
    # Strip YAML front matter but keep the title if present.
    title = ""
    body = md
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            fm = body[3:end]
            m = re.search(r"^title:\s*(.+)$", fm, re.M)
            if m:
                title = m.group(1).strip().strip("\"'")
            body = body[end + 4 :]

    out = []
    lines = body.split("\n")
    i = 0
    in_list = None  # "ul" | "ol" | None

    def close_list():
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>")
            in_list = None

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # fenced code
        if stripped.startswith("```"):
            close_list()
            lang = stripped[3:].strip()
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            cls = f' class="lang-{html.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(code))}</code></pre>")
            continue

        # heading
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            close_list()
            lvl = len(m.group(1))
            txt = inline(m.group(2), project, wiki)
            anchor = re.sub(r"[^\w一-鿿-]+", "-", m.group(2).strip()).strip("-")
            out.append(f'<h{lvl} id="{html.escape(anchor)}">{txt}</h{lvl}>')
            i += 1
            continue

        # table: a pipe row followed by a separator row
        if (
            stripped.startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip())
        ):
            close_list()
            head = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(f"<th>{inline(c, project, wiki)}</th>" for c in head)
            trs = []
            for r in rows:
                trs.append("<tr>" + "".join(f"<td>{inline(c, project, wiki)}</td>" for c in r) + "</tr>")
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>")
            continue

        # blockquote
        if stripped.startswith(">"):
            close_list()
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(f"<blockquote>{inline(' '.join(buf), project, wiki)}</blockquote>")
            continue

        # horizontal rule
        if re.match(r"^([-*_])\1{2,}$", stripped):
            close_list()
            out.append("<hr>")
            i += 1
            continue

        # lists
        m = re.match(r"^([-*+])\s+(.*)$", stripped)
        if m:
            if in_list != "ul":
                close_list()
                out.append("<ul>")
                in_list = "ul"
            out.append(f"<li>{inline(m.group(2), project, wiki)}</li>")
            i += 1
            continue
        m = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        if m:
            if in_list != "ol":
                close_list()
                out.append("<ol>")
                in_list = "ol"
            out.append(f"<li>{inline(m.group(2), project, wiki)}</li>")
            i += 1
            continue

        if not stripped:
            close_list()
            i += 1
            continue

        # paragraph: gather until a blank line or a block starter
        close_list()
        buf = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if not nxt:
                break
            if (
                nxt.startswith("#")
                or nxt.startswith("```")
                or nxt.startswith("|")
                or nxt.startswith(">")
                or re.match(r"^([-*+])\s+", nxt)
                or re.match(r"^(\d+)[.)]\s+", nxt)
            ):
                break
            buf.append(nxt)
            i += 1
        out.append(f"<p>{inline(' '.join(buf), project, wiki)}</p>")

    close_list()
    return f'<h1 class="doc-title">{html.escape(title)}</h1>' + "".join(out) if title else "".join(out)


_INTERNAL_WIKI_ROOTS = {
    "entities", "sources", "concepts", "overviews", "purposes",
    "schemas", "indexes", "logs", "comparisons", "syntheses",
}


def inline(text, project="", wiki=""):
    """Inline markdown: escape first, then re-introduce the safe tags."""
    s = html.escape(text, quote=False)
    # [[wiki link]] -> span with class so CSS can style it
    def wiki_link(m):
        ref = m.group(1)
        label = html.escape(m.group(2) or ref)
        if project:
            href = "#/wiki/p/%s/%s" % (quote(project), quote(ref))
            if wiki:
                href += "?wiki=" + quote(wiki)
            return f'<a class="wikilink" href="{html.escape(href, quote=True)}">{label}</a>'
        return f'<span class="wikilink" title="{html.escape(ref)}">{label}</span>'
    s = re.sub(
        r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]",
        wiki_link,
        s,
    )
    # [text](url): external URLs stay external; known Wiki roots are scoped.
    def markdown_link(m):
        label, target = m.group(1), m.group(2)
        external = re.match(r"^(?:https?://|mailto:)", target, re.I)
        clean = target.lstrip("/")
        root = clean.split("/", 1)[0].lower()
        internal = bool(project) and not external and (
            not target.startswith("/") or root in _INTERNAL_WIKI_ROOTS
        )
        if internal:
            href = "#/wiki/p/%s/%s" % (quote(project), quote(clean))
            if wiki:
                href += "?wiki=" + quote(wiki)
            return '<a href="%s">%s</a>' % (html.escape(href, quote=True), label)
        return '<a href="%s" target="_blank" rel="noopener">%s</a>' % (
            html.escape(target, quote=True), label
        )
    s = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        markdown_link,
        s,
    )
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", s)
    return s


# ---------------------------------------------------------------- notes


def list_notes():
    """List the Basic Memory notes on disk.

    The .md files are the source of truth for Basic Memory (the index DB is
    a derived cache), so read them directly rather than going through the
    MCP endpoint -- there is no HTTP read API on 8765 at all.
    """
    out = []
    if not os.path.isdir(NOTES_DIR):
        return out
    for root, dirs, files in os.walk(NOTES_DIR):
        # NOTE: "projects" is the per-project note home, so it must NOT be
        # skipped here -- skipping it is what made every project's notes
        # invisible once notes started being filed per project.
        dirs[:] = [d for d in dirs if d not in (".git", ".backlog", ".basic-memory")]
        for fn in sorted(files):
            if not fn.endswith(".md"):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, NOTES_DIR)
            try:
                st = os.stat(full)
            except OSError:
                continue
            first_head = ""
            desc = ""
            try:
                with open(full, encoding="utf-8") as fh:
                    for line in fh:
                        ls = line.strip()
                        if ls.startswith("# ") and not first_head:
                            first_head = ls[2:].strip()
                        elif ls and not ls.startswith(("#", "-", "---", "title:", "type:")) and not desc:
                            desc = ls[:160]
            except OSError:
                pass
            out.append({
                "path": rel,
                "title": first_head or os.path.splitext(fn)[0],
                "size": st.st_size,
                "mtime": st.st_mtime,
                "desc": desc,
                "folder": os.path.dirname(rel) or "(root)",
            })
    out.sort(key=lambda r: (r["folder"], r["path"]))
    return out


def read_note(rel):
    full = os.path.normpath(os.path.join(NOTES_DIR, rel))
    # Path traversal guard: the resolved path must stay under NOTES_DIR.
    if not full.startswith(os.path.normpath(NOTES_DIR) + os.sep):
        raise BadRequest("path escapes notes dir")
    if not os.path.isfile(full):
        raise FileNotFoundError(rel)
    with open(full, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------- data


def all_wikis():
    """Every wiki visible to this team, via the API rather than a fixed id.

    The engine's /wiki/list requires team_id; passing none returns
    code=400 ("x-tdai-service-id header and team_id are required").
    """
    data = api_data("/wiki/list", {"team_id": TEAM_ID}) or {}
    items = data.get("items", []) if isinstance(data, dict) else []
    return items


def wiki_meta(wiki_id):
    return api_data("/wiki/get", {"wiki_id": wiki_id}) or {}


def wiki_pages(wiki_id):
    data = api_data("/wiki/page/ls", {"wiki_id": wiki_id}) or {}
    items = data.get("items", []) if isinstance(data, dict) else []
    return sorted(items, key=lambda p: (TYPE_LABEL.get(p.get("type"), p.get("type") or ""), p.get("title") or ""))


def wiki_overview():
    """Back-compat single-wiki view. Kept so existing callers/tests work."""
    projects = load_projects()
    wiki_ids = [w for p in projects for w in p["wikis"]]
    if not wiki_ids:
        return {}, [], {}
    wid = wiki_ids[0]
    meta = wiki_meta(wid)
    items = wiki_pages(wid)
    by_type = {}
    for p in items:
        by_type.setdefault(p.get("type") or "other", []).append(p)
    return meta, items, by_type


def wiki_page(ref, wiki_id=None):
    wid = wiki_id or _default_wiki_id()
    data = api_data("/wiki/page/read", {"wiki_id": wid, "refs": [ref]})
    items = (data or {}).get("items", [])
    if not items:
        raise UpstreamError(f"no content for ref={ref}")
    return items[0].get("content", "")


def resolve_wiki_ref(ref, wiki_id):
    """Resolve a title-only Wiki link to its configured page path."""
    clean = (ref or "").lstrip("/")
    if not wiki_id or "/" in clean:
        return clean
    try:
        wanted = clean.rsplit(".", 1)[0].casefold()
        for page in wiki_pages(wiki_id):
            path = str(page.get("path") or "").removeprefix("wiki/")
            title = str(page.get("title") or "")
            stem = os.path.splitext(os.path.basename(path))[0]
            if title.casefold() == clean.casefold() or stem.casefold() == wanted:
                return path
    except (UpstreamError, BadRequest):
        pass
    return clean


def wiki_search(q, limit=20, wiki_id=None):
    wid = wiki_id or _default_wiki_id()
    data = api_data("/wiki/search", {"wiki_id": wid, "query": q, "limit": limit})
    return (data or {}).get("results", [])


def _default_wiki_id():
    ids = [w for p in load_projects() for w in p["wikis"]]
    return ids[0] if ids else ""


def notes_for_project(proj):
    """Notes claimed by one project, per its registry note_subdirs.

    A note is claimed when its path lies under one of the declared
    subdirs. Subdirs are relative to NOTES_DIR; a project claiming
    "shared" gets everything under memory/shared.
    """
    subdirs = [s.strip("/") for s in (proj.get("note_subdirs") or []) if s and s.strip("/")]
    if not subdirs:
        return []
    all_notes = list_notes()
    out = []
    for n in all_notes:
        for s in subdirs:
            if n["path"] == s or n["path"].startswith(s + "/"):
                out.append(n)
                break
    return out


def tasks_for_project(proj):
    """Read the project's existing Backlog tasks, memoised per project.

    The cache key is the project name, deliberately: a whole-model cache would
    hand project A's tasks to a caller who asked for project B.
    """
    name = proj.get("name") or ""
    now = time.monotonic()
    with _TASK_CACHE_LOCK:
        hit = _TASK_CACHE.get(name)
        if hit is not None and hit[0] > now:
            return hit[1], hit[2]
        # Single-flight per project. Without holding a per-project lock across
        # the subprocess call, a cold cache lets every waiting thread fork its
        # own `backlog` process -- measured 10 concurrent processes for 8
        # requests. Threads queue here instead.
        gate = _TASK_GATES.get(name)
        if gate is None:
            gate = threading.Lock()
            _TASK_GATES[name] = gate

    with gate:
        # Another thread may have finished while we waited on the gate.
        now = time.monotonic()
        with _TASK_CACHE_LOCK:
            hit = _TASK_CACHE.get(name)
            if hit is not None and hit[0] > now:
                return hit[1], hit[2]
        tasks, error = _tasks_for_project_uncached(proj)
        with _TASK_CACHE_LOCK:
            _TASK_CACHE[name] = (now + TASK_CACHE_TTL, tasks, error)
        return tasks, error


def _tasks_for_project_uncached(proj):
    """Read the project's existing Backlog tasks for a compact workspace preview."""
    path = proj.get("path") or ""
    if not path or not os.path.isdir(path):
        return [], "project directory unavailable"
    env = dict(os.environ)
    env["BACKLOG_CWD"] = path
    try:
        proc = subprocess.run(
            [BACKLOG, "task", "list", "--json"],
            cwd=path, env=env, capture_output=True, text=True, timeout=12,
        )
        if proc.returncode != 0:
            return [], (proc.stderr or proc.stdout or "backlog task list failed").strip()[:240]
        payload = json.loads(proc.stdout)
        items = payload.get("tasks", []) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return [], "unexpected task list response"
        # Keep the aggregate counters the overview needs (AC progress, declared
        # done, dependency-blocked) in the same single `task list --json` call.
        # The 6421 dashboard gets `isBlocked` from a per-task `task view
        # --json` enrichment; that costs one fork per task, and this endpoint
        # is served live per request, so we deliberately do NOT do it here.
        # `dependencies` is present in the list payload, which is enough to
        # tell a task that waits on unfinished work from one that does not.
        out = []
        for t in items:
            if not isinstance(t, dict):
                continue
            deps = t.get("dependencies") or []
            out.append({
                "id": str(t.get("id") or ""),
                "title": str(t.get("title") or ""),
                "status": str(t.get("status") or ""),
                "assignee": t.get("assignee") or [],
                "acDone": int(t.get("acceptanceCriteriaCompleted") or 0),
                "acTotal": int(t.get("acceptanceCriteriaCount") or 0),
                "isReady": t.get("isReady"),
                "dependencies": [str(x) for x in deps if isinstance(x, (str, int))],
            })
        return out, None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return [], f"{type(exc).__name__}: {exc}"


def codegraph_available(name):
    safe = "".join(ch for ch in str(name or "") if ch.isalnum() or ch in "-_")
    return bool(safe) and os.path.isfile(os.path.join(CODEGRAPH_DIR, safe + ".html"))


def project_knowledge(only=None):
    """The whole model behind both the 6424 page and the injected panel.

    One call, so the reader and a board's panel can never disagree about
    which notes and wikis belong to which project.

    `only` limits which projects pay for the `backlog task list` subprocess.
    Every project is still present in the result -- with empty tasks when
    skipped -- because the frontend reads `allProjects` for the project
    switcher and would break if the list changed shape.
    """
    projects = load_projects()
    all_notes = list_notes()
    claimed = set()
    out = []
    for proj in projects:
        notes = notes_for_project(proj)
        if only and proj.get("name") != only:
            tasks, task_error = [], None
        else:
            tasks, task_error = tasks_for_project(proj)
        for n in notes:
            claimed.add(n["path"])
        wikis = []
        for wid in proj["wikis"]:
            try:
                m = wiki_meta(wid)
            except (UpstreamError, BadRequest) as exc:
                wikis.append({"wiki_id": wid, "error": str(exc)})
                continue
            try:
                pages = wiki_pages(wid)
            except (UpstreamError, BadRequest) as exc:
                pages = []
                m = dict(m)
                m["error"] = str(exc)
            wikis.append({
                "wiki_id": wid,
                "name": m.get("name") or wid,
                "status": m.get("status"),
                "team_id": m.get("team_id"),
                "page_count": m.get("page_count", len(pages)),
                "pages": pages,
                "error": m.get("error"),
            })
        out.append({
            "name": proj["name"],
            "group": proj["group"],
            "port": proj["port"],
            "projectName": proj["projectName"],
            "tasks": tasks,
            "taskError": task_error,
            "note_subdirs": proj["note_subdirs"],
            "notes": notes,
            "wikis": wikis,
            "panel": proj["panel"],
            "codegraph": codegraph_available(proj["name"]),
        })
    # Anything no project claimed still has to be reachable, otherwise a note
    # filed before the mapping existed would silently vanish from the UI.
    orphans = [n for n in all_notes if n["path"] not in claimed]
    return {"projects": out, "unassigned": orphans, "notesDir": NOTES_DIR}


# ---------------------------------------------------------------- panel

# Self-contained markup spliced into a Backlog.md board by the reverse proxy.
# Scoped with a `vk-` prefix and a closed shadow root so it cannot collide
# with Backlog.md's own class names or CSS custom properties.
PANEL_CSS = """
<style id="vk-panel-style">
  #vk-panel{all:initial;font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",
    "Noto Sans CJK SC","Microsoft YaHei",sans-serif;color:#1c2128;
    margin:18px auto;max-width:1100px;padding:0 16px;position:relative;z-index:2}
  @media (prefers-color-scheme:dark){#vk-panel{color:#e6edf3}}
  #vk-panel *{box-sizing:border-box}
  #vk-panel .vk-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
    border:1px solid #d0d7de;border-radius:8px;padding:10px 14px;background:#f6f8fa;
    position:sticky;top:0;z-index:3;box-shadow:0 2px 8px rgba(31,41,55,.08)}
  @media (prefers-color-scheme:dark){#vk-panel .vk-head{background:#161b22;border-color:#30363d}}
  #vk-panel .vk-head b{font-size:14px}
  #vk-panel .vk-tag{font-size:11px;padding:1px 8px;border-radius:20px;
    border:1px solid #d0d7de;color:#57606a}
  @media (prefers-color-scheme:dark){#vk-panel .vk-tag{color:#8b949e;border-color:#30363d}}
  #vk-panel .vk-more{margin-left:auto;font-size:12px}
  #vk-panel .vk-float{position:fixed;right:18px;bottom:18px;z-index:9999;
    padding:8px 12px;border-radius:999px;background:#0969da;color:#fff;
    box-shadow:0 4px 14px rgba(31,41,55,.24);font-size:12px;text-decoration:none}
  #vk-panel details{margin-top:10px}
  #vk-panel summary{cursor:pointer;font-size:13px;color:#0969da;user-select:none}
  #vk-panel .vk-body{display:flex;gap:18px;flex-wrap:wrap;margin-top:10px}
  #vk-panel .vk-col{flex:1 1 300px;min-width:260px}
  #vk-panel .vk-col h4{margin:0 0 8px;font-size:13px;color:#57606a;font-weight:600}
  @media (prefers-color-scheme:dark){#vk-panel .vk-col h4{color:#8b949e}}
  #vk-panel .vk-item{display:block;padding:6px 9px;border:1px solid #e1e4e8;
    border-radius:6px;margin-bottom:6px;text-decoration:none;color:inherit;font-size:13px}
  #vk-panel .vk-item:hover{border-color:#0969da}
  @media (prefers-color-scheme:dark){#vk-panel .vk-item{border-color:#30363d}}
  #vk-panel .vk-item i{display:block;font-style:normal;font-size:11px;color:#6a737d;margin-top:2px}
  @media (prefers-color-scheme:dark){#vk-panel .vk-item i{color:#8b949e}}
  #vk-panel .vk-empty{font-size:13px;color:#6a737d;padding:4px 2px}
  @media (prefers-color-scheme:dark){#vk-panel .vk-empty{color:#8b949e}}
  #vk-panel .vk-note{color:#6a737d}
</style>
"""


def esc(s, quote=True):
    """HTML-escape for server-rendered markup.

    quote=True by default because the injected panel puts note titles into
    element content AND the panel is spliced into a third-party page, where an
    unescaped quote would break out of an attribute. md_to_html uses
    html.escape directly; this exists for the panel.
    """
    return html.escape("" if s is None else str(s), quote=quote)


def panel_html(proj):
    """Render one project's knowledge as standalone HTML.

    Rendered server-side on purpose: the proxy does a single string splice
    into </body>, so a panel that needed client-side fetching would break
    whenever the reader is down -- exactly when you most want to know.
    """
    n_notes = len(proj.get("notes") or [])
    n_wiki = sum(len(w.get("pages") or []) for w in (proj.get("wikis") or []))

    # Links MUST be absolute. This HTML is spliced into a board served on a
    # different port (6420/6422/6423), so a root-relative "/api/note?..."
    # would resolve against the board's own origin and 404 -- the reader's
    # routes are not the board's routes.
    READER = "http://%s:%d" % (BIND_IP, PORT)

    note_items = "".join(
        '<a class="vk-item" href="{r}/#/notes/p/{project}/{p}" target="_blank" rel="noopener">{t}'
        '<i>{f}</i></a>'.format(r=READER, project=quote(proj["name"]),
                               p=quote(n["path"]), t=esc(n["title"] or n["path"]),
                               f=esc(n["path"]))
        for n in (proj.get("notes") or [])
    ) or '<div class="vk-empty">这个项目还没有笔记</div>'

    wiki_items = "".join(
        '<div style="margin-bottom:10px"><a class="vk-item" href="{r}/#/wiki/p/{project}/{q}?wiki={w}" '
        'target="_blank" rel="noopener">{t}<i>{ty}</i></a></div>'.format(
            r=READER, project=quote(proj["name"]),
            q=quote(str(p.get("path") or p.get("id") or "")),
            w=quote(w["wiki_id"]),
            t=esc(p.get("title") or p.get("path") or ""),
            ty=esc(TYPE_LABEL.get(p.get("type"), p.get("type") or "page")),
        )
        for w in (proj.get("wikis") or [])
        for p in (w.get("pages") or [])[:12]
    )
    if not wiki_items:
        errors = [str(w.get("error")) for w in (proj.get("wikis") or []) if w.get("error")]
        wiki_items = ('<div class="vk-empty">Wiki 已配置，但当前不可读：'+esc(errors[0])+'</div>'
                      if errors else '<div class="vk-empty">这个项目还没有 wiki</div>')

    title = proj.get("projectName") or proj["name"]
    return (
        PANEL_CSS
        + '<div id="vk-panel">'
        + '<div class="vk-head"><b>{}</b>'.format(esc(title))
        + '<span class="vk-tag">{} 篇笔记</span>'.format(n_notes)
        + '<span class="vk-tag">{} 页 wiki</span>'.format(n_wiki)
        + '<a class="vk-more" href="{r}/#/knowledge/p/{p}" target="_blank" '
          'rel="noopener">在知识库中查看 →</a>'.format(
              r=READER, p=quote(proj["name"]))
        + '</div>'
        + '<details open><summary>本项目知识（随项目归属）</summary><div class="vk-body">'
        + '<div class="vk-col"><h4>笔记</h4>{}</div>'.format(note_items)
        + '<div class="vk-col"><h4>Wiki</h4>{}</div>'.format(wiki_items)
        + '</div></details>'
        + '<a class="vk-float" href="{r}/#/knowledge/p/{p}" target="_blank" rel="noopener">打开本项目知识</a>'.format(r=READER, p=quote(proj['name']))
        + '</div>'
        + '<script>(function(){'
        + 'var p=document.getElementById("vk-panel");if(!p)return;'
        + 'var h=window.location.hostname;'
        + 'if(h){p.querySelectorAll("a[href]").forEach(function(a){'
        + 'try{var u=new URL(a.href);if(u.port=="6424"){u.hostname=h;a.href=u.toString();}}catch(e){}'
        + '});}'
        + '})();</script>'
    )


# ---------------------------------------------------------------- page

CSS = """
:root{
  --bg:#f6f7f9; --panel:#fff; --ink:#1c2128; --muted:#6a737d;
  --line:#e1e4e8; --accent:#0969da; --accent-soft:#ddf4ff;
  --chip:#eef1f4; --ok:#1a7f37; --warn:#9a6700;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0d1117; --panel:#161b22; --ink:#e6edf3; --muted:#8b949e;
    --line:#30363d; --accent:#4493f8; --accent-soft:#121d2f;
    --chip:#21262d; --ok:#3fb950; --warn:#d29922;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans CJK SC","Microsoft YaHei",sans-serif}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
header{background:var(--panel);border-bottom:1px solid var(--line);padding:14px 22px;
  display:flex;align-items:center;gap:18px;flex-wrap:wrap;position:sticky;top:0;z-index:10}
header h1{font-size:16px;margin:0;font-weight:600}
header nav{display:flex;gap:14px;flex-wrap:wrap}
header nav a{color:var(--muted);font-size:14px}
header nav a.on,header nav a:hover{color:var(--accent);text-decoration:none;font-weight:600}
.spacer{flex:1}
.pill{font-size:12px;padding:3px 9px;border-radius:99px;background:var(--chip);color:var(--muted)}
.pill.ok{color:var(--ok)}
.pill.bad{color:#cf222e}
main{max-width:1180px;margin:0 auto;padding:28px 24px 48px}
.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:16px}
input[type=search]{flex:1;min-width:220px;padding:8px 12px;border:1px solid var(--line);
  border-radius:6px;background:var(--panel);color:var(--ink);font-size:14px}
button{padding:8px 14px;border:1px solid var(--line);border-radius:6px;
  background:var(--panel);color:var(--ink);cursor:pointer;font-size:14px}
button:hover{border-color:var(--accent);color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:17px 18px;box-shadow:0 1px 2px rgba(27,31,36,.04)}
.graph-card{margin-top:16px}
.graph-card .graph-action{display:inline-block;padding:8px 14px;border:1px solid var(--accent);border-radius:6px;background:var(--accent);color:#fff;font-size:14px}
.graph-card .graph-action:hover{text-decoration:none;filter:brightness(.94)}
.graph-card .graph-action.disabled{background:var(--chip);border-color:var(--line);color:var(--muted);cursor:default}
.graph-card .graph-url{margin-top:10px;font-size:14px;overflow-wrap:anywhere;word-break:break-word}
.card h3{margin:0 0 6px;font-size:15px}
.card p{margin:0;color:var(--muted);font-size:13px;line-height:1.55}
.card .meta{margin-top:8px;font-size:12px;color:var(--muted)}
.chip{display:inline-block;font-size:11px;padding:2px 8px;border-radius:99px;
  background:var(--accent-soft);color:var(--accent);margin-right:6px}
.chip.n{background:var(--chip);color:var(--muted)}
.group{margin:26px 0 10px;font-size:14px;color:var(--muted);font-weight:600;
  text-transform:uppercase;letter-spacing:.4px}
.doc{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:22px 26px}
.doc-title{margin-top:0;font-size:22px;border-bottom:1px solid var(--line);padding-bottom:12px}
.doc h1,.doc h2,.doc h3,.doc h4{margin:22px 0 10px;line-height:1.3}
.doc h1{font-size:21px}.doc h2{font-size:18px}.doc h3{font-size:16px}
.doc p{margin:10px 0}
.doc ul,.doc ol{margin:10px 0;padding-left:24px}
.doc li{margin:4px 0}
.doc code{background:var(--chip);padding:1px 5px;border-radius:4px;font-size:13px;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.doc pre{background:var(--chip);padding:13px 15px;border-radius:6px;overflow:auto;
  border:1px solid var(--line)}
.doc pre code{background:none;padding:0;font-size:13px;line-height:1.55}
.doc table{border-collapse:collapse;margin:14px 0;width:100%;font-size:14px}
.doc th,.doc td{border:1px solid var(--line);padding:7px 11px;text-align:left}
.doc th{background:var(--chip);font-weight:600}
.ownership{width:100%;border-collapse:separate;border-spacing:0;background:var(--panel);
  border:1px solid var(--line);border-radius:8px;overflow:hidden;font-size:14px}
.ownership th,.ownership td{padding:11px 14px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
.ownership th{background:var(--chip);font-weight:600;color:var(--text)}
.ownership tr:last-child td{border-bottom:0}
.doc blockquote{margin:12px 0;padding:6px 14px;border-left:3px solid var(--accent);
  color:var(--muted);background:var(--accent-soft);border-radius:0 4px 4px 0}
.wikilink{color:var(--accent);border-bottom:1px dotted var(--accent);cursor:help}
.empty{color:var(--muted);padding:40px;text-align:center;background:var(--panel);
  border:1px dashed var(--line);border-radius:8px}
.hint{color:var(--muted);font-size:13px;margin:0 0 14px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 20px}
.stat{flex:1 1 130px;background:var(--panel);border:1px solid var(--line);
  border-radius:10px;padding:12px 14px}
.stat .n{display:block;font-size:20px;font-weight:600;line-height:1.25}
.stat .k{display:block;font-size:11px;color:var(--muted);text-transform:uppercase;
  letter-spacing:.06em;margin-top:2px}
.stat.warn .n{color:var(--ok)}
.pwrap{display:inline-flex;gap:6px;flex-wrap:wrap;margin:0 0 8px}
.pill.warn{color:var(--warn);background:color-mix(in srgb,var(--warn) 14%,transparent)}
@supports not (background:color-mix(in srgb,red 10%,transparent)){
  .pill.warn{background:var(--chip)}
}
.card .acbar{height:5px;border-radius:99px;background:var(--chip);margin:9px 0 3px;overflow:hidden}
.card .acbar i{display:block;height:100%;background:var(--ok);border-radius:99px}
.card .acnote{font-size:11px;color:var(--muted)}
.boardlink{display:inline-block;margin-top:10px;font-size:12px;color:var(--muted)}
.boardlink:hover{color:var(--accent)}
.back{display:inline-block;margin-bottom:14px;font-size:14px}
.rel{font-size:13px;color:var(--muted);margin-top:6px}
.workspace{display:grid;grid-template-columns:210px minmax(0,1fr);gap:28px;align-items:start}
.workspace-rail{position:sticky;top:78px;background:#263342;color:#e8eef5;border:0;border-radius:12px;padding:16px 12px;box-shadow:0 8px 20px rgba(31,41,55,.12)}
.workspace-rail h2{font-size:14px;margin:4px 8px 12px;line-height:1.4}
.workspace-rail .rail-label{display:block;padding:0 8px 8px;color:#9fb0c1;font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.workspace-rail a{display:block;padding:9px 10px;border-radius:7px;color:#cbd5e1;font-size:13px}
.workspace-rail a:hover,.workspace-rail a.on{background:#3a4b5f;color:#fff;text-decoration:none;font-weight:600}
.workspace-main{min-width:0}
.workspace-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-bottom:18px}
.workspace-head h2{margin:0;font-size:28px;line-height:1.2;letter-spacing:-.02em}
.workspace-head p{margin:5px 0 0;color:var(--muted);font-size:13px}
.workspace-actions{display:flex;gap:8px;flex-wrap:wrap}
.workspace-actions a{border:1px solid var(--line);border-radius:6px;padding:7px 10px;font-size:13px;background:var(--panel)}
.section-title{margin:22px 0 9px;font-size:13px;color:var(--muted);font-weight:600;letter-spacing:.02em}
.task-card{grid-column:1/-1}
@media(max-width:720px){main{padding:18px 14px 36px}.workspace{grid-template-columns:1fr;gap:14px}.workspace-rail{position:static;display:flex;gap:5px;overflow:auto;align-items:center}.workspace-rail h2,.workspace-rail .rail-label{display:none}.workspace-rail a{white-space:nowrap}.workspace-head{display:block}.workspace-head h2{font-size:24px}.workspace-actions{margin-top:12px}.grid{grid-template-columns:1fr}}
"""

JS = r"""
function q(s){return document.querySelector(s)}
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML}
async function j(u){const r=await fetch(u);if(!r.ok)throw new Error('HTTP '+r.status);return r.json()}

const TABS=[['projects','/projects','项目总览'],['manage','/manage','知识归属管理']];
let view='projects', searchTimer=null;

function nav(){
  q('header nav').innerHTML = TABS.map(function(t){
    return '<a href="#'+t[1]+'" data-k="'+t[0]+'" class="'+(view===t[0]?'on':'')+'">'+t[2]+'</a>';
  }).join('');
  q('header nav').querySelectorAll('a').forEach(function(a){
    a.onclick=function(e){e.preventDefault();location.hash=a.getAttribute('href')};
  });
}

async function health(){
  if (!q('.pills')) return;
  const pills=[['wiki','/api/wiki/health'],['notes','/api/notes/health']];
  const out=await Promise.all(pills.map(async function(t){
    try{const d=await j(t[1]);return '<span class="pill '+(d.ok?'ok':'bad')+'">'+t[0]+' '+(d.ok?'ok':'down')+'</span>'}
    catch(e){return '<span class="pill bad">'+t[0]+' down</span>'}
  }));
  q('.pills').innerHTML=out.join(' ');
}

const TYPELAB={concept:'概念',entity:'实体',source:'来源',overview:'概览',purpose:'目的',
  schema:'结构',index:'索引',log:'日志',comparison:'对比',synthesis:'综述',other:'其它'};

// Strip the "wiki/" prefix the API returns in item.path.
function refOf(p){
  let s = (p && p.path) ? String(p.path) : String((p&&p.id)||'');
  return s.indexOf('wiki/')===0 ? s.slice(5) : s;
}

function card(p, project, wiki){
  const ref = refOf(p);
  const label = TYPELAB[p.type] || p.type || '其它';
  const href = project ? '#/wiki/p/'+encodeURIComponent(project)+'/'+encodeURIComponent(ref)+(wiki?'?wiki='+encodeURIComponent(wiki):'') : '#/wiki/p/'+encodeURIComponent(ref);
  return '<div class="card">'
    + '<h3><a href="'+href+'">'+esc(p.title||ref)+'</a></h3>'
    + '<span class="chip'+(p.type?'':' n')+'">'+esc(label)+'</span>'
    + '<p>'+esc((p.description||p.snippet||'').slice(0,180))+'</p>'
    + '<div class="meta">'+esc(ref)+'</div></div>';
}

function visibleWikiPage(p){
  const key = String(p.id||p.path||'').toLowerCase();
  const desc = String(p.description||'');
  return !(key.indexOf('basic-memory-mcp-8765')>=0 &&
    (desc.indexOf('历史残留别名')>=0 || desc.indexOf('已废弃别名')>=0));
}

async function wikiList(query, proj){
  nav();
  const pname = proj || curProject();
  let url;
  if (query && !pname) url = '/api/wiki/search?q='+encodeURIComponent(query);
  else if (pname) url = '/api/knowledge?project='+encodeURIComponent(pname);
  else url = '/api/wiki/list';
  const d = await j(url);
  // /api/knowledge returns per-project data; /api/wiki/list returns all wikis.
  let byType={}, meta={}, pages=[];
  if (pname && d.project){
    const w = (d.project.wikis||[])[0]||{};
    meta = {name:w.name, team_id:w.team_id, status:w.status, updated_at:w.last_sync_at};
    pages = (w.pages||[]).filter(visibleWikiPage);
    if (query) pages = pages.filter(function(p){ return (p.title+' '+(p.description||'')+' '+(p.snippet||'')).toLowerCase().indexOf(query.toLowerCase())>=0; });
    pages.forEach(function(p){ (byType[p.type||'other']=byType[p.type||'other']||[]).push(p); });
  } else {
    byType = d.byType||{}; meta = d.meta||{}; pages = (d.pages||[]).filter(visibleWikiPage);
  }
  let h = '<div class="bar">'
    + '<input type="search" id="q" placeholder="搜索 wiki（标题/正文/实体）" value="'+esc(query||'')+'">'
    + '<button id="go">搜索</button>'
    + (pname?'<span class="chip">项目：'+esc(pname)+'</span>':'')+'</div>';
  const wikiErrors = pname ? (d.project.wikis||[]).filter(function(w){return w.error;}) : [];
  if (pname && wikiErrors.length){
    h += '<div class="empty"><b>Wiki 已配置，但当前不可读</b><br><span class="hint">'+esc(wikiErrors[0].error)+'</span></div>';
  } else if (query && pname && !(d.project.wikis||[]).length){
    h += '<div class="empty">该项目尚未添加 Wiki</div>';
  } else if (query){
    const rs = pname ? pages : (d.results||[]);
    h += '<p class="hint">搜索 “'+esc(query)+'” 命中 '+rs.length+' 条</p>';
    h += rs.length
      ? '<div class="grid">'+rs.map(function(p){return card(p,pname,pname && d.project && d.project.wikis[0] && d.project.wikis[0].wiki_id);}).join('')+'</div>'
      : '<div class="empty">没有匹配 “'+esc(query)+'” 的页面</div>';
  } else if (pname && !(d.project.wikis||[]).length){
    h += '<div class="empty">尚未添加项目 Wiki</div>';
  } else {
    h += '<p class="hint">'+esc(meta.name||'wiki')+' · '+pages.length+' 页 · team '
      + esc(meta.team_id||'?')+' · '+esc(meta.status||'?')
      + ' · 更新于 '+esc(String(meta.updated_at||'').replace('T',' ').replace('Z',''))+'</p>';
    const keys = Object.keys(byType);
    if (!keys.length) h += '<div class="empty">没有页面</div>';
    for (const t of keys){
      const items = byType[t];
      h += '<div class="group">'+esc(TYPELAB[t]||t)+' · '+items.length+'</div>'
        + '<div class="grid">'+items.map(function(p){return card(p,pname,pname && d.project && d.project.wikis[0] && d.project.wikis[0].wiki_id);}).join('')+'</div>';
    }
  }
  if (pname && d.project){
    d.project.allProjects = d.projects||[];
    h = projectShell(d.project, 'wiki', h);
  }
  q('main').innerHTML = h;
  const box=q('#q');
  const apply=function(){
    const v=box.value.trim();
    location.hash = '/wiki'+(pname?'/p/'+encodeURIComponent(pname):'')+(v?'?q='+encodeURIComponent(v):'');
  };
  box.oninput=function(){clearTimeout(searchTimer);searchTimer=setTimeout(apply,320)};
  q('#go').onclick=apply;
  box.focus();
}

async function wikiPage(ref, proj, wiki){
  nav();
  const pname = proj || curProject();
  let url = '/api/wiki/page?ref='+encodeURIComponent(ref);
  if (pname) url += '&project='+encodeURIComponent(pname);
  if (wiki) url += '&wiki='+encodeURIComponent(wiki);
  const d = await j(url);
  const back = pname ? '#/wiki/p/'+encodeURIComponent(pname) : '#/wiki';
  const body = '<a class="back" href="'+back+'">← 返回 wiki 列表</a>'
    + '<div class="doc">'+(d.html||'<p class="empty">（空内容）</p>')+'</div>';
  if (pname){
    const pd = await j('/api/knowledge?project='+encodeURIComponent(pname));
    q('main').innerHTML = projectShell(pd.project, 'wiki', body);
  } else q('main').innerHTML = body;
  window.scrollTo(0,0);
}

async function notesList(proj){
  nav();
  const pname = proj || curProject();
  const url = pname ? '/api/notes?project='+encodeURIComponent(pname) : '/api/notes';
  const d = await j(url);
  const items = d.notes||[];
  let h = '<div class="bar">'
    + '<input type="search" id="q" placeholder="按标题/内容过滤笔记" value=""></div>'
    + (pname?' · 项目 <b>'+esc(pname)+'</b>':'')+'</p>';
  if (!items.length) h += '<div class="empty">'+(pname?'该项目还没有笔记':'没有笔记')+'</div>';
  else {
    h += '<div class="grid">'+items.map(function(n){
      return '<div class="card">'
        + '<h3><a href="'+(pname?'#/notes/p/'+encodeURIComponent(pname)+'/'+encodeURIComponent(n.path):'#/notes/'+encodeURIComponent(n.path))+'">'+esc(n.title)+'</a></h3>'
        + '<span class="chip n">'+esc(n.folder)+'</span>'
        + '<p>'+esc((n.desc||'').slice(0,180))+'</p>'
        + '<div class="meta">更新于 '+esc(new Date(n.mtime*1000).toLocaleString('zh-CN'))+'</div></div>';
    }).join('')+'</div>';
  }
  if (pname){
    const pd = await j('/api/knowledge?project='+encodeURIComponent(pname));
    h = projectShell(pd.project, 'notes', h);
  }
  q('main').innerHTML = h;
  const box=q('#q');
  box.oninput=function(){
    const v=box.value.trim().toLowerCase();
    document.querySelectorAll('.card').forEach(function(c){
      c.style.display = !v || c.textContent.toLowerCase().indexOf(v)>=0 ? '' : 'none';
    });
  };
  box.focus();
}

async function projectHome(name){
  nav();
  const d = await j('/api/knowledge?project='+encodeURIComponent(name));
  const p = d.project;
  let h = '<div class="section-title">项目知识</div><div class="grid">';
  h += '<div class="card task-card"><h3>任务 · '+(p.tasks||[]).length+'</h3>'
    + (p.taskError?'<p class="empty">任务暂时不可读：'+esc(p.taskError)+'</p>':((p.tasks||[]).length
      ? '<div class="task-list">'+p.tasks.slice(0,8).map(function(t){return '<div class="meta"><b>'+esc(t.id)+'</b> · '+esc(t.title)+' <span class="chip n">'+esc(t.status)+'</span></div>';}).join('')+'</div>'
      : '<p class="empty">该项目还没有任务</p>'))
    + '<div class="meta"><a href="http://'+HOST+':'+p.port+'/">在 Backlog 中继续管理 →</a></div></div>';
  h += '<div class="card"><h3>笔记 · '+p.notes.length+'</h3>'
    + '<p class="hint">归属目录：'+esc((p.note_subdirs||[]).join(', ')||'（未声明）')+'</p>'
    + (p.notes.length?p.notes.map(function(n){
        return '<div class="meta"><a href="#/notes/p/'+encodeURIComponent(p.name)+'/'+encodeURIComponent(n.path)+'">'
          + esc(n.title||n.path)+'</a></div>'; }).join('')
      :'<p class="empty">该项目还没有笔记</p>')+'</div>';
  p.wikis.forEach(function(w){
    h += '<div class="card"><h3>'+esc(w.name)+' · '+(w.page_count||0)+' 页</h3>'
      + '<p class="hint">'+esc(w.status||'')+' · '+esc(w.team_id||'')+'</p>'
      + (w.error?'<p class="empty"><b>Wiki 已配置，但当前不可读</b><br>'+esc(w.error)+'</p>':'')
      + (w.pages||[]).filter(visibleWikiPage).slice(0,10).map(function(pg){
          return '<div class="meta"><a href="#/wiki/p/'+encodeURIComponent(p.name)
            +'/'+encodeURIComponent(refOf(pg))+'?wiki='+encodeURIComponent(w.wiki_id||'')+'">'+esc(pg.title||pg.path||'')+'</a></div>';
        }).join('')+'</div>';
  });
  h += graphLink(p);
  h += '</div>';
  q('main').innerHTML = projectShell(p, 'home', h);
  window.scrollTo(0,0);
}

async function projectKnowledge(name){
  nav();
  const d = await j('/api/knowledge?project='+encodeURIComponent(name));
  const p = d.project;
  let h = '<div class="section-title">项目笔记 · '+p.notes.length+'</div>';
  h += p.notes.length ? '<div class="grid">'+p.notes.map(function(n){
    return '<div class="card"><h3><a href="#/notes/p/'+encodeURIComponent(p.name)+'/'+encodeURIComponent(n.path)+'">'+esc(n.title||n.path)+'</a></h3>'
      +(n.desc?'<p>'+esc(n.desc)+'</p>':'')+'</div>';
  }).join('')+'</div>' : '<div class="empty">该项目还没有笔记</div>';
  h += '<div class="section-title">项目 Wiki</div>';
  if (!p.wikis.length) h += '<div class="empty">尚未添加项目 Wiki</div>';
  p.wikis.forEach(function(w){
    h += '<div class="card"><h3>'+esc(w.name||w.wiki_id)+' · '+(w.page_count||0)+' 页</h3>';
    if (w.error) h += '<p class="empty"><b>Wiki 已配置，但当前不可读</b><br>'+esc(w.error)+'</p>';
    else if (!(w.pages||[]).filter(visibleWikiPage).length) h += '<p class="empty">该 Wiki 暂无页面</p>';
    else h += '<div class="grid">'+w.pages.filter(visibleWikiPage).slice(0,12).map(function(pg){
      return '<div class="card"><h3><a href="#/wiki/p/'+encodeURIComponent(p.name)+'/'+encodeURIComponent(refOf(pg))+'?wiki='+encodeURIComponent(w.wiki_id)+'">'+esc(pg.title||pg.path||'')+'</a></h3>'
        +(pg.description?'<p>'+esc(pg.description)+'</p>':'')+'</div>';
    }).join('')+'</div>';
    h += '</div>';
  });
  h += graphLink(p);
  q('main').innerHTML = projectShell(p, 'knowledge', h);
}

async function tasksList(name){
  nav();
  const d = await j('/api/knowledge?project='+encodeURIComponent(name));
  const p = d.project, items = p.tasks||[];
  let h = '<div class="section-title">项目任务</div>';
  if (p.taskError) h += '<div class="empty"><b>任务暂时不可读</b><br>'+esc(p.taskError)+'</div>';
  else if (!items.length) h += '<div class="empty">该项目还没有任务</div>';
  else h += '<div class="grid">'+items.map(function(t){return '<article class="card"><h3>'+esc(t.id)+' · '+esc(t.title)+'</h3><span class="chip">'+esc(t.status)+'</span>'+(t.assignee&&t.assignee.length?'<p class="hint">负责人：'+esc(t.assignee.join(', '))+'</p>':'')+'</article>';}).join('')+'</div>';
  h += '<p class="hint">这里展示项目 Backlog 的只读摘要；需要创建或更新任务时使用右上角“编辑任务”。</p>';
  q('main').innerHTML = projectShell(p, 'tasks', h);
}

const DONE = {done:1, complete:1, completed:1, closed:1, resolved:1};

// One project's rollup, computed from the same task payload the card shows.
// Kept client-side and derived (never a separate field from the server) so a
// card's pills can never disagree with the number printed next to it.
function rollup(p){
  const ts = p.tasks||[];
  const r = {n:ts.length, done:0, waiting:0, acDone:0, acTotal:0, err:p.taskError||null};
  ts.forEach(function(t){
    if (DONE[String(t.status||'').trim().toLowerCase()]) r.done++;
    // isReady===false is Backlog's own "has unfinished dependencies" verdict.
    // Only an explicit false counts; null/absent means "not stated", which is
    // different from "ready" and must not be shown as a blocker.
    if (t.isReady === false) r.waiting++;
    r.acDone += t.acDone||0;
    r.acTotal += t.acTotal||0;
  });
  return r;
}

function statsBar(ps){
  const s = {projects:ps.length, ok:0, tasks:0, done:0, waiting:0};
  ps.forEach(function(p){
    const r = rollup(p);
    if (!r.err) s.ok++;
    s.tasks += r.n; s.done += r.done; s.waiting += r.waiting;
  });
  const cell = function(n,k,cls){return '<div class="stat'+(cls?' '+cls:'')+'"><span class="n">'+esc(n)+'</span><span class="k">'+esc(k)+'</span></div>';};
  return '<div class="stats">'
    + cell(s.ok+' / '+s.projects, 'projects ok')
    + cell(s.tasks, 'tasks')
    + cell(s.done, 'declared done', 'warn')
    + cell(s.waiting, 'waiting on deps')
    + '</div>';
}

function projectCard(p){
  const r = rollup(p);
  const nwiki = (p.wikis||[]).reduce(function(n,w){return n+(w.pages||[]).length;},0);
  const nnotes = (p.notes||[]).length;
  let pills = '<div class="pwrap">'
    + '<span class="pill">'+r.n+' tasks</span>';
  if (r.acTotal) pills += '<span class="pill">AC '+r.acDone+'/'+r.acTotal+'</span>';
  if (r.done)  pills += '<span class="pill warn">'+r.done+' declared done</span>';
  if (r.waiting) pills += '<span class="pill bad">'+r.waiting+' waiting</span>';
  pills += '</div>';
  let ac = '';
  if (r.acTotal){
    const pct = Math.round(r.acDone*100/r.acTotal);
    ac = '<div class="acbar" title="AC '+r.acDone+'/'+r.acTotal+'"><i style="width:'+pct+'%"></i></div>'
       + '<div class="acnote">验收标准 '+r.acDone+'/'+r.acTotal+'（'+pct+'%）</div>';
  }
  // The card title goes to the knowledge workspace; the board is a separate
  // small link. Nesting the whole card in an <a> would make the port link
  // unreachable, and pointing the card at the board would skip the knowledge.
  const board = p.port
    ? '<a class="boardlink" href="http://'+HOST+':'+esc(p.port)+'/" target="_blank" '
      + 'rel="noopener">看板 :'+esc(p.port)+' ↗</a>'
    : '';
  return '<article class="card">'
    + '<h3><a href="#/project/'+encodeURIComponent(p.name)+'">'+esc(p.projectName||p.name)+'</a></h3>'
    + '<span class="chip n">'+esc(p.group||'')+'</span>'
    + pills
    + '<p>'+nnotes+' 篇笔记 · '+nwiki+' 页 wiki</p>'
    + ac
    + '<p class="hint">'+(r.err? esc(r.err) : '状态已同步')+'</p>'
    + '<div class="open">进入项目工作区 →</div>'
    + board
    + '</article>';
}

function graphLink(p){
  const name = encodeURIComponent(p.name);
  const url = 'http://'+HOST+':'+READONLY_PORT+'/api/codegraph?name='+name;
  const ready = p.codegraph === true;
  return '<div class="card graph-card"><h3>代码图谱</h3>'
    + '<p class="hint">查看当前项目的静态代码图谱快照。</p>'
    + (ready
      ? '<a class="graph-action" href="/api/codegraph?name='+name+'" target="_blank" rel="noopener">打开代码图谱 ↗</a>'
      : '<span class="graph-action disabled" aria-disabled="true">尚未生成代码图谱</span>')
    + '<div class="meta graph-url"><code>'+esc(url)+'</code></div></div>';
}

async function projectsHome(manage){
  nav();
  const d = await j('/api/knowledge');
  const ps = d.projects||[];
  let h = '';
  if (!manage) {
    h = '<p class="hint">知识跟随项目：每个项目拥有自己的笔记与 Wiki，看板内也会显示同一份内容</p>'
      + statsBar(ps);
    // Grouped like the 6421 workbench so the same three projects read the
    // same way on both pages, but the tile itself is 6424's knowledge card.
    const groups = {};
    ps.forEach(function(p){ (groups[p.group||'其他'] = groups[p.group||'其他']||[]).push(p); });
    Object.keys(groups).forEach(function(g){
      h += '<div class="group">'+esc(g)+' · '+groups[g].length+' projects</div><div class="grid">'
        + groups[g].map(projectCard).join('') + '</div>';
    });
    if (!ps.length) h += '<div class="empty">注册表里还没有项目，先在 ports.json 中登记</div>';
  } else {
    const unassigned = d.unassigned||[];
    h += '<div class="section-title">未归属笔记 · '+unassigned.length+'</div>';
    h += unassigned.length ? '<div class="grid">'+unassigned.map(function(n){
      const summary = (n.desc||'').replace(/^tags:\s*$/i,'').trim();
      const folder = n.folder && n.folder !== '(root)' ? '<div class="meta">'+esc(n.folder)+'</div>' : '';
      return '<div class="card"><h3><a href="#/notes/'+encodeURIComponent(n.path)+'">'+esc(n.title||n.path)+'</a></h3>'
        +(summary?'<p>'+esc(summary)+'</p>':'')+folder+'</div>';
    }).join('')+'</div>' : '<div class="empty">当前没有未归属笔记</div>';
    h += '<div class="section-title">知识归属规则</div><table class="ownership"><thead><tr><th>项目</th><th>笔记目录</th><th>Wiki</th></tr></thead><tbody>';
    h += ps.map(function(p){
      const wikis = (p.wikis||[]).map(function(w){return (w.name||w.wiki_id||'未命名 Wiki');}).join('、') || '未配置';
      return '<tr><td>'+esc(p.projectName||p.name)+'</td><td>'+esc((p.note_subdirs||[]).join('、')||'未声明')+'</td><td>'+esc(wikis)+'</td></tr>';
    }).join('')+'</tbody></table>';
  }
  const shell = {name:'', projectName:manage?'知识管理':'所有项目', group:'', allProjects:ps, notes:[], wikis:[], tasks:[], port:null};
  q('main').innerHTML = projectShell(shell, manage?'manage':'home', h);
}

async function notePage(rel, proj){
  nav();
  const d = await j('/api/note?path='+encodeURIComponent(rel));
  const body = '<a class="back" href="'+(proj?'#/notes/p/'+encodeURIComponent(proj):'#/notes')+'">← 返回笔记列表</a>'
    + '<div class="doc">'+(d.html||'<p class="empty">（空文件）</p>')+'</div>';
  if (proj){
    const pd = await j('/api/knowledge?project='+encodeURIComponent(proj));
    q('main').innerHTML = projectShell(pd.project, 'notes', body);
  } else q('main').innerHTML = body;
  window.scrollTo(0,0);
}

function projectRail(p, active){
  const projects = p.allProjects||[p];
  let h = '<aside class="workspace-rail"><h2>MyTask<br><span style="font-weight:400;color:#9fb0c1">科研工作台</span></h2><span class="rail-label">所有项目</span>';
  projects.forEach(function(x){
    const name = encodeURIComponent(x.name);
    h += '<a class="'+(x.name===p.name?'on':'')+'" href="#/project/'+name+'">'+esc(x.projectName||x.name)+'</a>';
  });
  return h+'<span class="rail-label" style="margin-top:18px">管理</span><a href="#/manage">知识归属管理</a></aside>';
}

function projectShell(p, active, body){
  const homeHref = p.name ? '#/project/'+encodeURIComponent(p.name) : '#/projects';
  q('header nav').innerHTML = p.name
    ? ('<a href="'+homeHref+'" class="'+(active==='home'?'on':'')+'">项目概览</a>'
      + '<a href="#/tasks/p/'+encodeURIComponent(p.name)+'" class="'+(active==='tasks'?'on':'')+'">任务</a>'
      + '<a href="#/notes/p/'+encodeURIComponent(p.name)+'" class="'+(active==='notes'?'on':'')+'">笔记</a>'
      + '<a href="#/wiki/p/'+encodeURIComponent(p.name)+'" class="'+(active==='wiki'?'on':'')+'">Wiki</a>'
      + '<a href="#/knowledge/p/'+encodeURIComponent(p.name)+'" class="'+(active==='knowledge'?'on':'')+'">知识管理</a>')
    : '<a href="#/projects" class="'+(active==='home'?'on':'')+'">项目总览</a><a href="#/manage" class="'+(active==='manage'?'on':'')+'">知识归属管理</a>';
  const board = p.port ? '<a href="http://'+HOST+':'+p.port+'/">编辑任务 ↗</a>' : '';
  const subtitle = p.name ? [p.group, p.name].filter(Boolean).join(' · ') : '';
  return '<div class="workspace"><div>'+projectRail(p, active)+'</div><section class="workspace-main">'
    + '<div class="workspace-head"><div><h2>'+esc(p.projectName||p.name)+'</h2>'
    + (subtitle?'<p>'+esc(subtitle)+'</p>':'')+'</div><div class="workspace-actions">'+board+'</div></div>'
    + body+'</section></div>';
}

function curProject(){
  const m = /[?&]project=([^&]+)/.exec(location.search);
  return m ? decodeURIComponent(m[1]) : '';
}

function route(){
  const h = location.hash.replace(/^#/,'') || (curProject() ? '/knowledge/p/'+encodeURIComponent(curProject()) : '/projects');
  const parts = h.split('?');
  const path = parts[0];
  const query = new URLSearchParams(parts[1]||'').get('q')||'';
  const seg = path.split('/').filter(Boolean);
  try{
    if (seg[0]==='wiki'){
      view='wiki';
      // /wiki/p/<project>[/<ref>]  -- knowledge scoped to one project
      if (seg[1]==='p' && seg[2]){
        const proj = decodeURIComponent(seg[2]);
        if (seg[3]) return wikiPage(decodeURIComponent(seg.slice(3).join('/')), proj, new URLSearchParams(parts[1]||'').get('wiki')||'');
        return wikiList(query, proj);
      }
      return wikiList(query);
    }
    if (seg[0]==='notes'){
      view='notes';
      if (seg[1]==='p' && seg[2]){
        const proj = decodeURIComponent(seg[2]);
        if (seg[3]) return notePage(decodeURIComponent(seg.slice(3).join('/')), proj);
        return notesList(proj);
      }
      if (seg.length>1) return notePage(decodeURIComponent(seg.slice(1).join('/')));
      return notesList();
    }
    if (seg[0]==='tasks' && seg[1]==='p' && seg[2]){
      view='tasks';
      return tasksList(decodeURIComponent(seg[2]));
    }
    if (seg[0]==='knowledge' && seg[1]==='p' && seg[2]){
      view='knowledge';
      return projectKnowledge(decodeURIComponent(seg[2]));
    }
    if (seg[0]==='project' && seg[1]){
      view='wiki';
      return projectHome(decodeURIComponent(seg[1]));
    }
    if (seg[0]==='manage'){
      view='manage';
      return projectsHome(true);
    }
    if (seg[0]==='projects'){
      view='wiki';
      return projectsHome();
    }
    return projectsHome();
  }catch(e){
    q('main').innerHTML = '<div class="empty">出错了：'+esc(e.message)+'</div>';
    console.error(e);
  }
}

async function boot(){
  health();
  window.addEventListener('hashchange', route);
  await route();
}
boot();
"""


def page_html(title, body):
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · MyTask 科研工作台</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>MyTask <span style="font-weight:400;color:var(--muted)">科研工作台</span></h1>
  <nav></nav>
  <span class="spacer"></span>
</header>
<main>{body}</main>
<script>const HOST=(window.location.hostname || {BIND_IP!r}), READONLY_PORT={PORT};</script>
<script>{JS}</script>
</body>
</html>"""


# ---------------------------------------------------------------- handler


class Handler(BaseHTTPRequestHandler):
    server_version = "vcc-readonly/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        from urllib.parse import urlparse, parse_qs

        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        qs = parse_qs(u.query)

        def arg(name, default=""):
            return (qs.get(name) or [default])[0]

        try:
            if path == "/":
                self._send(200, page_html("首页", "<div class='empty'>载入中…</div>"), "text/html; charset=utf-8")
                return

            if path == "/api/wiki/health":
                self._send(200, json.dumps({"ok": api_health()}, ensure_ascii=False))
                return

            if path == "/api/notes/health":
                ok = os.path.isdir(NOTES_DIR) and bool(list_notes())
                self._send(200, json.dumps({"ok": ok, "dir": NOTES_DIR}, ensure_ascii=False))
                return

            if path == "/api/wiki/list":
                # Every wiki the team can see, straight from the API. This is
                # the "all wikis" view; /api/knowledge is the per-project one.
                try:
                    wikis = all_wikis()
                    err = None
                except UpstreamError as exc:
                    wikis, err = [], str(exc)
                self._send(200, json.dumps({
                    "team_id": TEAM_ID, "wikis": wikis, "error": err,
                }, ensure_ascii=False))
                return

            if path == "/api/knowledge":
                # Per-project knowledge, straight from the shared registry.
                # This is the endpoint a board's injected panel reads.
                want = arg("project")
                # Only pay for the task subprocess of the project asked for.
                model = project_knowledge(only=want)
                if want:
                    sel = [p for p in model["projects"] if p["name"] == want]
                    if not sel:
                        self._send(404, json.dumps(
                            {"error": f"no such project: {want}"}, ensure_ascii=False))
                        return
                    self._send(200, json.dumps(
                        {"project": dict(sel[0], allProjects=model["projects"]), "projects": model["projects"], "unassigned": model["unassigned"],
                         "notesDir": model["notesDir"]}, ensure_ascii=False))
                    return
                self._send(200, json.dumps(model, ensure_ascii=False))
                return

            if path == "/api/projects":
                self._send(200, json.dumps(
                    {"projects": load_projects(), "notesDir": NOTES_DIR,
                     "registry": REGISTRY}, ensure_ascii=False))
                return

            if path == "/api/wiki/search":
                q = arg("q")
                if not q:
                    self._send(400, json.dumps({"error": "q required"}, ensure_ascii=False))
                    return
                wid = arg("wiki") or None
                self._send(200, json.dumps(
                    {"query": q, "wiki": wid or _default_wiki_id(),
                     "results": wiki_search(q, wiki_id=wid)}, ensure_ascii=False))
                return

            if path == "/api/wiki/page":
                ref = arg("ref")
                if not ref:
                    self._send(400, json.dumps({"error": "ref required"}, ensure_ascii=False))
                    return
                wid = arg("wiki") or None
                project = arg("project")
                if not wid and project:
                    mapped = next((p for p in load_projects() if p["name"] == project), None)
                    if mapped and len(mapped.get("wikis") or []) == 1:
                        wid = mapped["wikis"][0]
                resolved_ref = resolve_wiki_ref(ref, wid)
                rendered = md_to_html(
                    wiki_page(resolved_ref, wiki_id=wid), project=project, wiki=wid or ""
                )
                self._send(200, json.dumps(
                    {"ref": resolved_ref, "wiki": wid or _default_wiki_id(),
                     "html": rendered}, ensure_ascii=False))
                return

            if path == "/api/notes":
                notes = list_notes()
                want = arg("project")
                if want:
                    proj = next((p for p in load_projects() if p["name"] == want), None)
                    if proj is None:
                        self._send(404, json.dumps(
                            {"error": f"no such project: {want}"}, ensure_ascii=False))
                        return
                    notes = notes_for_project(proj)
                self._send(200, json.dumps(
                    {"dir": NOTES_DIR, "notes": notes, "project": want or None},
                    ensure_ascii=False))
                return

            if path == "/api/note":
                rel = arg("path")
                if not rel:
                    self._send(400, json.dumps({"error": "path required"}, ensure_ascii=False))
                    return
                self._send(200, json.dumps(
                    {"path": rel, "html": md_to_html(read_note(rel))}, ensure_ascii=False))
                return

# --- code graph route (added by add_codegraph.py) ---
            if path == "/api/codegraph" or path == "/api/codegraph/":
                name = (arg("name") or arg("project") or
                        os.environ.get("VCC_CODEGRAPH_DEFAULT", "platform-core"))
                safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_")
                if not safe:
                    self._send(400, json.dumps({"error": "bad name"}))
                    return
                f = os.path.join(CODEGRAPH_DIR, safe + ".html")
                if not os.path.isfile(f):
                    self._send(404, json.dumps(
                        {"error": "no generated graph: " + safe}, ensure_ascii=False))
                    return
                with open(f, encoding="utf-8") as fh:
                    graph_html = fh.read()
                generated = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(f)))
                stamp = ('<div style="margin-top:4px;color:#64748b;font-size:12px">'
                         '静态代码图谱快照 · 文件更新时间 '+html.escape(generated)+
                         ' · 不代表实时索引</div>')
                graph_html = graph_html.replace("</header>", stamp + "</header>", 1)
                self._send(200, graph_html, "text/html; charset=utf-8")
                return

            if path == "/api/panel":
                # Ready-to-inject HTML for one project's board. The reverse
                # proxy in front of each Backlog.md UI fetches this and splices
                # it in, so the panel markup lives in exactly one place and
                # cannot drift from the data model.
                proj = arg("project")
                if not proj:
                    self._send(400, json.dumps({"error": "project required"}, ensure_ascii=False))
                    return
                found = next((p for p in project_knowledge(only=proj)["projects"]
                              if p["name"] == proj), None)
                if found is None:
                    self._send(404, json.dumps(
                        {"error": f"no such project: {proj}"}, ensure_ascii=False))
                    return
                self._send(200, panel_html(found), "text/html; charset=utf-8")
                return

            self._send(404, json.dumps({"error": f"no route {path}"}, ensure_ascii=False))
        except FileNotFoundError as exc:
            self._send(404, json.dumps({"error": f"not found: {exc}"}, ensure_ascii=False))
        except BadRequest as exc:
            self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False))
        except UpstreamError as exc:
            self._send(502, json.dumps({"error": str(exc)}, ensure_ascii=False))
        except ValueError as exc:
            self._send(400, json.dumps({"error": f"bad input: {exc}"}, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            self._send(500, json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))

    def do_HEAD(self):  # noqa: N802
        self.do_GET()


def parse_args(argv=None):
    """Command line flags. Environment variables still take precedence.

    argparse defaults are None on purpose: the real defaults live in the
    BIND/PORT constants, which the systemd unit also overrides. Using
    non-None argparse defaults would silently outrank the unit's env.
    """
    ap = argparse.ArgumentParser(
        prog="vcc-readonly.py",
        description="Read-only knowledge browser for the VCC platform "
                    "(default %s:%d)." % (BIND, PORT),
    )
    ap.add_argument("--bind", default=None,
                    help="address to bind (default: $VCC_READONLY_BIND or %s)" % BIND)
    ap.add_argument("--port", type=int, default=None,
                    help="port to listen on (default: $VCC_READONLY_PORT or %d)" % PORT)
    ap.add_argument("--check", action="store_true",
                    help="validate configuration, print the resolved settings, "
                         "and exit without binding the port")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    # Env wins over CLI so the systemd unit keeps authority.
    bind = os.environ.get("VCC_READONLY_BIND") or args.bind or BIND
    port = int(os.environ.get("VCC_READONLY_PORT") or args.port or PORT)

    if args.check:
        projects = load_projects()
        print("bind=%s port=%d" % (bind, port))
        print("registry=%s (%d projects)" % (REGISTRY, len(projects)))
        print("notes=%s" % NOTES_DIR)
        print("knowledge=%s%s" % (KNOWLEDGE, API_PREFIX))
        return 0

    try:
        srv = ThreadingHTTPServer((bind, port), Handler)
    except OSError as exc:
        # Port-in-use must be legible and must fail the exit code, otherwise a
        # caller cannot detect that the service never started.
        if exc.errno == errno.EADDRINUSE:
            sys.stderr.write(
                "ERROR: cannot bind %s:%d -- port already in use\n" % (bind, port))
            sys.stderr.write(
                "  another vcc-readonly is probably running; check:\n"
                "    ss -tlnp | grep ':%d ' | grep -o 'pid=[0-9]*'\n" % port)
        elif exc.errno == errno.EACCES:
            # Ports below 1024 need privileges. Telling the user that another
            # instance is running would be plainly wrong here.
            sys.stderr.write(
                "ERROR: cannot bind %s:%d -- permission denied "
                "(ports below 1024 need root or CAP_NET_BIND_SERVICE)\n"
                % (bind, port))
        else:
            sys.stderr.write("ERROR: cannot bind %s:%d -- %s\n" % (bind, port, exc))
        sys.stderr.flush()
        return 1
    projects = load_projects()
    sys.stderr.write(f"vcc-readonly listening on http://{bind}:{port}\n")
    sys.stderr.write(f"  wiki   <- {KNOWLEDGE}{API_PREFIX}  (team_id={TEAM_ID})\n")
    sys.stderr.write(f"  notes  <- {NOTES_DIR}\n")
    sys.stderr.write(f"  registry <- {REGISTRY}  ({len(projects)} projects)\n")
    for p in projects:
        sys.stderr.write(
            f"    {p['name']:<16} port={p['port']} panel={p['panel']} "
            f"notes={p['note_subdirs']} wikis={p['wikis']}\n"
        )
    if not projects:
        sys.stderr.write("  WARNING: no projects resolved from registry; "
                         "knowledge pages will be empty\n")
    sys.stderr.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
