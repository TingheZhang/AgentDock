#!/usr/bin/env python3
"""Read-only HTTP/MCP diagnostics; finite JSON or SSE responses only."""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SUPPORTED_PROTOCOL_VERSIONS = ("2025-03-26", "2024-11-05")


def positive_timeout(value):
    result = float(value)
    if result <= 0 or not math.isfinite(result):
        raise argparse.ArgumentTypeError("timeout must be positive")
    return result


def http(url, timeout, method="GET", payload=None, headers=None):
    try:
        req = Request(url, data=payload, method=method, headers=headers or {})
        with urlopen(req, timeout=timeout) as response:
            return response.status, response.headers, response.read(1024 * 1024)
    except HTTPError as exc:
        try:
            body = exc.read(1024 * 1024)
        except (OSError, TimeoutError):
            body = b""
        return exc.code, exc.headers, body
    except (OSError, URLError, ValueError) as exc:
        return 0, {}, str(exc).encode()


def body_json(body):
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def rpc_json(body):
    """Accept JSON or one JSON object carried in an SSE data event."""
    value = body_json(body)
    if value is not None:
        return value
    for line in body.decode("utf-8", "replace").splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
    return None


def bad_json(value):
    return isinstance(value, dict) and (
        value.get("ok") is False or bool(value.get("error"))
    )


def probe(url, timeout):
    status, _, body = http(url, timeout)
    value = body_json(body)
    problem = None
    if status != 200:
        problem = f"HTTP {status}"
    elif bad_json(value):
        problem = "JSON response reports error"
    elif not body:
        problem = "empty response"
    return {"url": url, "status": status, "problem": problem}


def mcp_probe(url, timeout):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": SUPPORTED_PROTOCOL_VERSIONS[0],
    }
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": SUPPORTED_PROTOCOL_VERSIONS[0], "capabilities": {},
        "clientInfo": {"name": "platform-core-ops-health", "version": "1"},
    }}
    status, response_headers, body = http(
        url, timeout, "POST", json.dumps(init).encode(), headers
    )
    value = rpc_json(body)
    problem = None
    if status != 200:
        problem = f"initialize HTTP {status}"
    elif (not isinstance(value, dict) or value.get("jsonrpc") != "2.0" or "error" in value
          or value.get("id") != 1 or not isinstance(value.get("result"), dict)):
        problem = "initialize missing JSON-RPC result"
    elif not all(value["result"].get(k) is not None for k in ("protocolVersion", "capabilities", "serverInfo")):
        problem = "initialize result missing protocolVersion/capabilities/serverInfo"
    result = value.get("result") if isinstance(value, dict) else None
    version = result.get("protocolVersion") if isinstance(result, dict) else None
    if problem is None and version not in SUPPORTED_PROTOCOL_VERSIONS:
        problem = f"unsupported negotiated protocol version: {version!r}"
    session = next((v for k, v in response_headers.items() if k.lower() == "mcp-session-id"), None)
    if problem is None and not session:
        problem = "initialize did not return mcp-session-id"
    if problem is None:
        tools_headers = dict(headers, **{"Mcp-Session-Id": session, "MCP-Protocol-Version": version})
        initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        status, _, body = http(url, timeout, "POST", json.dumps(initialized).encode(), tools_headers)
        value = rpc_json(body) if body else None
        if status not in (200, 202) or bad_json(value):
            problem = f"notifications/initialized HTTP {status}"
    if problem is None:
        tools = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        status, _, body = http(url, timeout, "POST", json.dumps(tools).encode(), tools_headers)
        value = rpc_json(body)
        if status != 200:
            problem = f"tools/list HTTP {status}"
        elif (not isinstance(value, dict) or value.get("jsonrpc") != "2.0" or "error" in value or value.get("id") != 2
              or not isinstance(value.get("result"), dict)
              or not isinstance(value["result"].get("tools"), list)):
            problem = "tools/list missing successful JSON-RPC result"
    return {"url": url, "status": status, "problem": problem, "session": bool(session)}


def unit_probe(unit):
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            text=True, capture_output=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"unit": unit, "active": False, "problem": str(exc)}
    active = result.returncode == 0 and result.stdout.strip() == "active"
    return {"unit": unit, "active": active, "problem": None if active else result.stdout.strip() or result.stderr.strip()}


def run(urls, mcp_url, wiki_url, units, timeout):
    probes = [probe(url, timeout) for url in urls]
    if wiki_url:
        probes.append(probe(wiki_url, timeout))
    if mcp_url:
        probes.append(mcp_probe(mcp_url, timeout))
    unit_results = [unit_probe(unit) for unit in units]
    problems = [p for p in probes if p.get("problem")] + [u for u in unit_results if u.get("problem")]
    return {"probes": probes, "units": unit_results, "problems": problems, "ok": not problems}


class MockHandler(BaseHTTPRequestHandler):
    sessions = {}

    def log_message(self, *_args):
        pass

    def do_GET(self):  # noqa: N802
        if self.path == "/ok":
            data = b'{"ok":true}'
            self.send_response(200)
        elif self.path == "/bad":
            data = b'{"error":"upstream"}'
            self.send_response(200)
        elif self.path == "/down":
            data = b"upstream unavailable"
            self.send_response(502)
        else:
            data = b"missing"
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/mcp-bad":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"jsonrpc":"2.0","id":1,"error":{"message":"bad mock"}}')
            return
        if self.path.startswith("/mcp") and request.get("method") == "initialize":
            mode = self.path.removeprefix("/mcp").lstrip("-") or "good"
            if mode == "bad":
                return
            session = "mock-" + mode
            self.sessions[session] = False
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if mode != "no-session":
                self.send_header("Mcp-Session-Id", session)
            self.end_headers()
            rpc = {"jsonrpc": "2.0", "id": 1, "result": {
                "protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {"name": "mock"}
            }}
            if mode == "null-result":
                rpc["result"] = None
            if mode == "bad-jsonrpc":
                rpc["jsonrpc"] = "1.0"
            self.wfile.write(json.dumps(rpc).encode())
            return
        if self.path.startswith("/mcp") and request.get("method") == "notifications/initialized":
            session = self.headers.get("Mcp-Session-Id")
            if session in self.sessions and self.path != "/mcp-no-notify":
                self.sessions[session] = True
            self.send_response(202)
            self.end_headers()
            return
        if self.path.startswith("/mcp") and request.get("method") == "tools/list":
            session = self.headers.get("Mcp-Session-Id")
            if not self.sessions.get(session):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"jsonrpc":"2.0","id":2,"error":{"message":"not initialized"}}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            tools = {} if self.path == "/mcp-empty-tools" else []
            self.wfile.write(json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}}).encode())
            return
        self.send_response(502)
        self.end_headers()


def self_test():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        positive = run([base + "/ok"], base + "/mcp", None, [], 2)
        bad = run([base + "/bad"], None, None, [], 2)
        down = run([base + "/down"], None, None, [], 2)
        mcp_negative = run([base + "/ok"], base + "/mcp-bad", None, [], 2)
        no_session = run([], base + "/mcp-no-session", None, [], 2)
        no_notify = run([], base + "/mcp-no-notify", None, [], 2)
        empty_tools = run([], base + "/mcp-empty-tools", None, [], 2)
        null_result = run([], base + "/mcp-null-result", None, [], 2)
        bad_jsonrpc = run([], base + "/mcp-bad-jsonrpc", None, [], 2)
        assert positive["ok"], positive
        assert not bad["ok"], bad
        assert not down["ok"], down
        assert not mcp_negative["ok"], mcp_negative
        assert not no_session["ok"], no_session
        assert not no_notify["ok"], no_notify
        assert not empty_tools["ok"], empty_tools
        assert not null_result["ok"], null_result
        assert not bad_jsonrpc["ok"], bad_jsonrpc
        return {"positive": positive, "bad": bad, "down": down, "mcp_negative": mcp_negative,
                "no_session": no_session, "no_notify": no_notify, "empty_tools": empty_tools,
                "null_result": null_result, "bad_jsonrpc": bad_jsonrpc, "ok": True}
    finally:
        server.shutdown()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print structured results")
    parser.add_argument("--self-test", action="store_true", help="run local positive/negative mock checks")
    parser.add_argument("--timeout", type=positive_timeout, default=5.0, help="per-request timeout in seconds")
    parser.add_argument("--url", dest="urls", action="append", help="HTTP URL to GET (repeatable)")
    parser.add_argument("--mcp-url", default=os.getenv("MCP_URL"))
    parser.add_argument("--wiki-url", default=os.getenv("WIKI_URL"))
    parser.add_argument("--unit", dest="units", action="append", default=[], help="user unit to check with is-active")
    args = parser.parse_args(argv)
    if args.self_test:
        result = self_test()
    else:
        urls = args.urls or []
        if not (urls or args.mcp_url or args.wiki_url or args.units):
            parser.error("provide at least one --url, --mcp-url, --wiki-url, or --unit target")
        result = run(urls, args.mcp_url, args.wiki_url, args.units, args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json or args.self_test else result["ok"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
