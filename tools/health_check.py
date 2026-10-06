#!/usr/bin/env python3
"""Read-only, registry-driven VCC health probe.

The report separates loopback upstream and configured public relay probes. It
does not restart processes or write platform data. ``--deep`` also checks the
reader's project attribution and one real page per declared Wiki.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

REGISTRY = os.environ.get("VCC_REGISTRY", "$PLATFORM_ROOT/platform/ports.json")
BIND = os.environ.get("VCC_BIND_IP", "<LAN_IP>")
READER = int(os.environ.get("VCC_READER_PORT", "6424"))
KNOWLEDGE = int(os.environ.get("VCC_KNOWLEDGE_PORT", "8421"))
BASIC = int(os.environ.get("VCC_BASIC_MEMORY_PORT", "8765"))
MEMORY = os.environ.get("VCC_MEMORY_DIR", "$PLATFORM_ROOT/memory")
# 声明「哪些笔记是哪个 wiki 的输入源」的文件。wiki 与笔记之间**没有自动传导**：
# 改笔记不会更新 wiki，必须手动跑 wiki_sync.py sync 重跑 ingest。
WIKI_BINDINGS = os.environ.get(
    "VCC_WIKI_BINDINGS", "$PLATFORM_ROOT/platform/wiki-source-bindings.json")
WIKI_ENGINES = os.environ.get(
    "VCC_WIKI_ENGINES", "$PLATFORM_ROOT/wiki/_wiki_engines/wiki-sources.json")
IGNORE_DIRS = {".git", ".backlog", "backlog", ".basic-memory"}
REQUIRED_UNITS = {
    "vcc-backlog.service": ("active", "running"),
    "vcc-knowledge.service": ("active", "running"),
    "vcc-basic-memory.service": ("active", "running"),
    "vcc-readonly.service": ("active", "running"),
    "platform-core.target": ("active", "active"),
    "vcc-healthcheck.timer": ("active", "waiting"),
}


def run(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=30).stdout
    except Exception:
        return ""


def listeners(port):
    rows = []
    out = run("ss -H -tlnp 2>/dev/null | grep ':%d '" % port)
    for line in out.splitlines():
        pids = re.findall(r"pid=(\d+)", line)
        if not pids:
            continue
        fields = line.split()
        local = fields[3] if len(fields) > 3 else "?"
        addr = local.rsplit(":", 1)[0].strip("[]")
        rows.append({"pid": pids[0], "addr": addr,
                     "cmd": cmdline(pids[0])})
    return rows


def cmdline(pid):
    try:
        return open("/proc/%s/cmdline" % pid, "rb").read().replace(
            b"\0", b" ").decode("utf-8", "replace").strip()[:120]
    except OSError:
        return ""


def probe(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, method=method, data=body,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return {"url": url, "status": r.status,
                    "body": r.read(65536).decode("utf-8", "replace")}
    except urllib.error.HTTPError as exc:
        return {"url": url, "status": exc.code,
                "body": exc.read(4096).decode("utf-8", "replace")}
    except Exception as exc:
        return {"url": url, "status": "ERR:%s" % type(exc).__name__,
                "error": str(exc)[:200]}


def registry():
    try:
        with open(REGISTRY, encoding="utf-8") as fh:
            d = json.load(fh)
        if not isinstance(d, dict):
            raise ValueError("registry is not an object")
        return d, None
    except Exception as exc:
        return {}, "registry: %s" % exc


def endpoint(report, phase, url, expected=200, method="GET", body=None,
             headers=None, body_check=None):
    r = probe(url, method, body, headers)
    report["probes"].append({"phase": phase, "url": url,
                             "status": r.get("status")})
    if r.get("status") != expected:
        report["problems"].append("%s %s: expected HTTP %s, got %s%s" % (
            phase, url, expected, r.get("status"),
            (": " + r.get("error", "")) if r.get("error") else ""))
        return r
    # Health-body validation is INDEPENDENT of body_check. These used to be
    # an elif behind body_check, which made it dead code for every current
    # caller (mcp/wiki-page pass body_check and none contain "/health").
    # It matters: MemoryKnowledge 8421 /health returns {"status":"ok"} with NO
    # "ok" key, so an ok-only check would silently pass a broken service.
    if "/health" in url:
        try:
            health = json.loads(r.get("body", ""))
            if not isinstance(health, dict):
                raise ValueError("body is not an object")
            if health.get("ok") is False or health.get("error"):
                report["problems"].append("%s %s: unhealthy JSON body" % (phase, url))
            if "status" in health and str(health.get("status")).lower() not in ("ok", "healthy", "ready"):
                report["problems"].append("%s %s: status=%s" % (phase, url, health.get("status")))
            if "ok" not in health and "status" not in health:
                report["problems"].append(
                    "%s %s: health JSON has neither 'ok' nor 'status'" % (phase, url))
        except ValueError as exc:
            report["problems"].append("%s %s: invalid health JSON (%s)" % (phase, url, exc))
    if body_check:
        problem = body_check(r.get("body", ""))
        if problem:
            report["problems"].append("%s %s: %s" % (phase, url, problem))
    return r


def host_probes(report, phase, port, path="/", public=True):
    ls = listeners(port)
    report["listeners"][str(port)] = ls
    local = [x for x in ls if x["addr"] in ("127.0.0.1", "localhost", "::1")]
    wild4 = [x for x in ls if x["addr"] in ("0.0.0.0", "*")]
    wild6 = [x for x in ls if x["addr"] in ("::", "[::]")]
    if not local and not wild4 and not wild6:
        report["problems"].append("%s: no listener on port %s" % (phase, port))
    endpoint(report, phase + "/loopback", "http://127.0.0.1:%d%s" % (port, path))
    if public:
        if wild4 or any(x["addr"] == BIND for x in ls):
            endpoint(report, phase + "/public", "http://%s:%d%s" % (BIND, port, path))
        elif wild6:
            endpoint(report, phase + "/public-ipv6", "http://[::1]:%d%s" % (port, path))
        else:
            report["problems"].append("%s/public: unsupported public bind" % phase)


def listener_only(report, phase, port):
    ls = listeners(port)
    report["listeners"][str(port)] = ls
    if not ls:
        report["problems"].append("%s: no listener on port %s" % (phase, port))


def units(report):
    out = run("systemctl --user list-units 'vcc-*' --all --no-legend --plain --no-pager")
    found = {}
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 4 and p[0].startswith("vcc-"):
            found[p[0]] = {"load": p[1], "active": p[2], "sub": p[3]}
    report["units"] = found
    for name, wanted in REQUIRED_UNITS.items():
        got = found.get(name)
        if not got:
            report["problems"].append("units/%s: missing" % name)
        elif (got["active"], got["sub"]) != wanted:
            report["problems"].append("units/%s: expected %s/%s, got %s/%s" %
                                      (name, wanted[0], wanted[1], got["active"], got["sub"]))


def mcp_initialize(report):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2024-11-05",
                                  "capabilities": {},
                                  "clientInfo": {"name": "vcc-health", "version": "1"}}}).encode()
    def valid_initialize(text):
        messages = []
        try:
            messages.append(json.loads(text))
        except ValueError:
            for line in text.splitlines():
                if line.startswith("data:"):
                    try:
                        messages.append(json.loads(line[5:].strip()))
                    except ValueError:
                        continue
        for msg in messages:
            result = msg.get("result") if isinstance(msg, dict) else None
            if (isinstance(msg, dict) and msg.get("id") == 1 and
                    isinstance(result, dict) and result.get("protocolVersion") and
                    isinstance(result.get("capabilities"), dict) and
                    isinstance(result.get("serverInfo"), dict)):
                return None
            if isinstance(msg, dict) and msg.get("id") == 1 and msg.get("error"):
                return "JSON-RPC initialize returned error"
        return "missing valid JSON-RPC initialize result for id 1"

    r = endpoint(report, "basic-memory/mcp", "http://127.0.0.1:%d/mcp" % BASIC,
                  expected=200, method="POST", body=body,
                  headers={"Content-Type": "application/json",
                           "Accept": "application/json, text/event-stream"},
                  body_check=valid_initialize)


def deep(report, reg):
    projects = reg.get("projects") or []
    registry_by_name = {p.get("name"): p for p in projects}
    for p in projects:
        path = p.get("path") or ""
        if not os.path.isdir(path):
            report["problems"].append("deep/project/%s: missing path %s" % (p.get("name"), path))
        elif not (os.path.isfile(os.path.join(path, "backlog", "config.yml")) or
                  os.path.isfile(os.path.join(path, ".backlog", "config.yml"))):
            report["problems"].append("deep/project/%s: missing Backlog container" % p.get("name"))
        k = p.get("knowledge") or {}
        for sub in k.get("note_subdirs") or []:
            if not os.path.isdir(os.path.join(MEMORY, sub)):
                report["problems"].append("deep/project/%s: missing note path %s" % (p.get("name"), sub))
        for wid in k.get("wikis") or []:
            if not wid:
                report["problems"].append("deep/project/%s: empty Wiki ID" % p.get("name"))
    r = endpoint(report, "reader/knowledge", "http://127.0.0.1:%d/api/knowledge" % READER)
    if r.get("status") != 200:
        return
    try:
        data = json.loads(r.get("body", "{}"))
    except ValueError:
        report["problems"].append("deep/reader/knowledge: invalid JSON")
        return
    notes_dir = data.get("notesDir") or MEMORY
    disk = set()
    for root, dirs, files in os.walk(notes_dir):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for fn in files:
            if fn.endswith(".md"):
                disk.add(os.path.relpath(os.path.join(root, fn), notes_dir).replace(os.sep, "/"))
    reader_projects = {p.get("name"): p for p in data.get("projects") or []}
    if set(reader_projects) != set(registry_by_name):
        report["problems"].append("deep/projects: registry and reader project IDs differ")
    claimed = set()
    expected_claims = {}
    for name, rp in reader_projects.items():
        regp = registry_by_name.get(name)
        if not regp:
            continue
        rk = rp.get("wikis") or []
        declared = set((regp.get("knowledge") or {}).get("wikis") or [])
        actual = set(w.get("wiki_id") for w in rk)
        if declared != actual:
            report["problems"].append("deep/project/%s: declared and reader Wiki IDs differ" % name)
        subdirs = [str(x).strip("/") for x in
                   ((regp.get("knowledge") or {}).get("note_subdirs") or []) if x]
        expected = {path for path in disk if any(path == s or path.startswith(s + "/") for s in subdirs)}
        expected_claims[name] = expected
        for n in rp.get("notes") or []:
            claimed.add(n.get("path"))
            if n.get("path") not in expected:
                report["problems"].append("deep/project/%s: note outside declared prefixes: %s" % (name, n.get("path")))
        if set(n.get("path") for n in rp.get("notes") or []) != expected:
            report["problems"].append("deep/project/%s: reader notes do not match declared prefixes" % name)
        for w in rk:
            if w.get("error"):
                report["problems"].append("deep/wiki/%s: %s" % (w.get("wiki_id"), w["error"]))
            pages = w.get("pages") or []
            if (w.get("page_count") or 0) > 0 and not pages and not w.get("error"):
                report["problems"].append("deep/wiki/%s: metadata has pages but page list is empty" % w.get("wiki_id"))
            if pages:
                ref = pages[0].get("path") or pages[0].get("id")
                if ref:
                    def page_ok(text):
                        try:
                            obj = json.loads(text)
                            if obj.get("error"):
                                return "page response contains error"
                            if not obj.get("html"):
                                return "page response has empty html"
                        except ValueError:
                            return "page response is not JSON"
                        return None
                    endpoint(report, "deep/wiki-page/%s" % w.get("wiki_id"),
                             "http://127.0.0.1:%d/api/wiki/page?ref=%s&project=%s&wiki=%s" %
                             (READER, urllib.parse.quote(str(ref)),
                             urllib.parse.quote(str(name)),
                             urllib.parse.quote(str(w.get("wiki_id")))),
                             body_check=page_ok)
    unassigned = {n.get("path") for n in data.get("unassigned") or []}
    expected_all_claimed = set().union(*expected_claims.values()) if expected_claims else set()
    expected_unassigned = disk - expected_all_claimed
    if (claimed | unassigned) != disk or (claimed & unassigned) or unassigned != expected_unassigned:
        report["problems"].append("deep/notes: attribution differs from reader notesDir inventory")
    report["notes"] = {"disk": len(disk), "claimed_unique": len(claimed),
                        "unassigned_unique": len(unassigned)}


def wiki_freshness(report, reg):
    """Detect notes that are newer than the wiki built from them.

    Background: the wiki layer is NOT auto-updated. No timer or cron drives
    it; the only rebuild path is an explicit POST /v3/wiki/ingest. The
    built-in auto-sync scheduler covers code-graph only. So editing a note
    leaves the wiki silently stale.

    Detection compares the newest mtime of each bound note against the wiki's
    lastSyncAt. This is only possible because `wiki-source-bindings.json`
    declares which notes feed which wiki -- there is no automatic link.

    Reported as a problem only when a note is strictly newer than the sync.
    Equal or older mtimes mean the wiki covers the current notes.
    """
    if not os.path.isfile(WIKI_BINDINGS):
        # No bindings declared: nothing to check, and that is not an error.
        report["wiki_freshness"] = {"checked": 0,
                                    "reason": "no bindings file"}
        return
    try:
        with open(WIKI_BINDINGS, encoding="utf-8") as fh:
            binds = json.load(fh)
    except ValueError as exc:
        report["problems"].append(
            "deep/wiki-freshness: bindings file is invalid JSON (%s)" % exc)
        return

    # lastSyncAt per wiki, from the engine state file.
    sync_at = {}
    if os.path.isfile(WIKI_ENGINES):
        try:
            with open(WIKI_ENGINES, encoding="utf-8") as fh:
                engines = json.load(fh)
            for wid, meta in (engines or {}).items():
                raw = (meta or {}).get("lastSyncAt")
                if raw:
                    # RFC3339 with Z; normalize to epoch seconds.
                    txt = str(raw).replace("Z", "+00:00")
                    try:
                        import datetime
                        sync_at[wid] = datetime.datetime.fromisoformat(
                            txt).timestamp()
                    except ValueError:
                        pass
        except ValueError as exc:
            report["problems"].append(
                "deep/wiki-freshness: engines file is invalid JSON (%s)" % exc)

    checked = 0
    for b in binds.get("bindings") or []:
        wid = b.get("wiki_id")
        if not wid:
            report["problems"].append(
                "deep/wiki-freshness: binding without wiki_id")
            continue
        notes = [n for n in (b.get("note_paths") or []) if n]
        if not notes:
            report["problems"].append(
                "deep/wiki-freshness/%s: binding declares no notes" % wid)
            continue
        newest_note = None
        newest_path = None
        missing = []
        for rel in notes:
            full = os.path.join(MEMORY, rel)
            if not os.path.isfile(full):
                missing.append(rel)
                continue
            checked += 1
            mt = os.path.getmtime(full)
            if newest_note is None or mt > newest_note:
                newest_note = mt
                newest_path = rel
        for rel in missing:
            report["problems"].append(
                "deep/wiki-freshness/%s: bound note is missing: %s" % (wid, rel))
        if newest_note is None:
            continue
        synced = sync_at.get(wid)
        if synced is None:
            report["problems"].append(
                "deep/wiki-freshness/%s: no lastSyncAt recorded; wiki sync "
                "state unknown" % wid)
            continue
        if newest_note > synced + 1:
            # 1s tolerance: filesystems and RFC3339 round to whole seconds.
            import datetime
            report["problems"].append(
                "deep/wiki-freshness/%s: note is newer than wiki sync "
                "(note %s, sync %s) -- run wiki_sync.py sync"
                % (wid, newest_path,
                   datetime.datetime.fromtimestamp(
                       synced).strftime("%Y-%m-%d %H:%M:%S")))
    report["wiki_freshness"] = {"checked": checked, "wikis": len(binds.get("bindings") or [])}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--deep", action="store_true")
    args = ap.parse_args()
    report = {"probes": [], "listeners": {}, "units": {}, "problems": []}
    reg, err = registry()
    if err:
        report["problems"].append(err)
    else:
        global BIND
        if "VCC_BIND_IP" not in os.environ:
            BIND = (reg.get("bind_ip") or "127.0.0.1").split(",")[0].strip()
        for p in reg.get("projects") or []:
            host_probes(report, "board/%s" % p.get("name"), int(p.get("ui_port")), "/")
        host_probes(report, "dashboard", int(reg.get("dashboard_port", 6421)), "/")
    host_probes(report, "reader", READER, "/api/notes/health")
    host_probes(report, "memoryknowledge", KNOWLEDGE, "/health")
    listener_only(report, "basic-memory", BASIC)
    mcp_initialize(report)
    units(report)
    if args.deep and not err:
        deep(report, reg)
        wiki_freshness(report, reg)
    report["ok"] = not report["problems"]
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("VCC health: %s" % ("OK" if report["ok"] else "FAILED"))
        for p in report["probes"]:
            print("  %-28s %-5s %s" % (p["phase"], p["status"], p["url"]))
        for e in report["problems"]:
            print("  ERROR %s" % e)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
