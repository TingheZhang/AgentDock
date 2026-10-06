#!/usr/bin/env python3
"""HTTP-aware reverse proxy that injects a knowledge panel into Backlog.md.

Why this exists
---------------
Each project's Backlog.md UI is a closed third-party app we cannot modify,
so the panel cannot be added from inside it. But every board is already
fronted by a relay we own (it exists to expose 127.0.0.1:PORT on the public
IP). The old relay was a raw TCP splice, which cannot touch the response
body. This is the same idea one layer up: parse the HTTP response, and for
HTML documents splice a panel in before </body>.

Design constraints
------------------
- The panel is SERVER-rendered by vcc-readonly /api/panel and injected as
  one string. No client-side fetch, so the panel still appears if the reader
  is down... except it cannot, since the HTML comes from the reader. The
  fallback is that a reader outage degrades to "no panel", never to a broken
  board: on ANY error we pass the upstream response through untouched.
- Only text/html gets touched. JS, CSS, images and the API pass through
  byte-for-byte.
- Content-Length is recomputed after injection, and we drop hop-by-hop and
  encoding headers we would otherwise have to rewrite. We ask the upstream
  for identity encoding so the body is not gzipped underneath us.
- If the reader is unreachable the panel is simply omitted. A board without
  knowledge is strictly better than a board that errors.

Usage:
    kb-proxy.py --port 6422 --bind 0.0.0.0 --project vcc-example-project
                [--reader http://127.0.0.1:6424]
"""
import argparse
import http.client
import http.server
import socket
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PANEL_CACHE_TTL = 60.0
_panel_cache = {}
_panel_lock = threading.Lock()


def fetch_panel(reader, project):
    """Return the panel HTML for a project, cached briefly.

    A failure returns "" rather than raising: the caller must still be able
    to serve the board.
    """
    key = (reader, project)
    now = time.time()
    with _panel_lock:
        hit = _panel_cache.get(key)
        if hit and now - hit[0] < PANEL_CACHE_TTL:
            return hit[1]
    url = "%s/api/panel?project=%s" % (reader.rstrip("/"), urllib.parse.quote(project))
    body = ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "vcc-kb-proxy/1"})
        with urllib.request.urlopen(req, timeout=6) as r:
            if r.status == 200:
                body = r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("panel fetch failed for %s: %s: %s\n"
                         % (project, type(e).__name__, e))
        body = ""
    with _panel_lock:
        _panel_cache[key] = (now, body)
    return body


# Headers that describe a single hop and must not be forwarded.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}

# WebSocket handshake. Backlog.md's own health banner reconnects every 5s
# against ws://<host>/ (the site root), so the proxy has to answer on that
# path or the board shows a red "Server disconnected" forever.
# The handshake is relayed verbatim rather than recomputed, so no accept-key
# derivation is needed here.


def _is_ws_upgrade(headers):
    """True when the client is asking to switch to the WebSocket protocol.

    Both headers matter: an Upgrade token list without Connection: Upgrade
    is not a handshake (RFC 6455 §4.1), and a Connection: Upgrade without a
    recognised token is not either.
    """
    if "upgrade" not in {v.strip().lower() for v in
                         headers.get("Connection", "").split(",")}:
        return False
    tokens = {v.strip().lower() for v in headers.get("Upgrade", "").split(",")}
    return "websocket" in tokens


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "vcc-kb-proxy/1.1"
    protocol_version = "HTTP/1.1"

    # Injected by the server object.
    upstream_port = None
    reader = None
    project = None

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _headers_forwardable(self):
        out = {}
        for k, v in self.headers.items():
            if k.lower() in HOP_BY_HOP or k.lower() == "host":
                continue
            if k.lower() == "accept-encoding":
                # Ask for identity so we can splice without re-encoding.
                out["Accept-Encoding"] = "identity"
                continue
            out[k] = v
        out["X-Forwarded-For"] = self.address_string()
        return out

    def _headers_ws(self):
        """Forward the handshake headers verbatim, including hop-by-hop ones.

        Unlike _headers_forwardable(), Connection and Upgrade must survive:
        they *are* the handshake. A raw socket is used downstream, so there
        is no hop-by-hop rewriting to protect.

        Host is *rewritten*, not dropped. The inbound Host is the public
        address, but the upstream board validates it against the address it
        is actually reachable on and answers `400 Bad Request` when it does
        not match. Pointing it at the loopback address it is listening on
        keeps the handshake valid.
        """
        out = {}
        for k, v in self.headers.items():
            if k.lower() == "host":
                continue
            out[k] = v
        out["Host"] = "127.0.0.1:%d" % self.upstream_port
        out["X-Forwarded-For"] = self.address_string()
        return out

    def _proxy_ws(self):
        """Splice a WebSocket connection straight through to the upstream.

        The generic _proxy() path cannot work here: it hands the request to
        http.client, and resp.read() consumes the 101 body and closes the
        connection before any frame can flow. A handshake needs the socket to
        stay open and become a pipe, so this does the upgrade by hand.
        """
        upstream = socket.create_connection(("127.0.0.1", self.upstream_port),
                                            timeout=60)
        try:
            lines = ["GET %s HTTP/1.1" % self.path]
            lines += ["%s: %s" % (k, v)
                      for k, v in self._headers_ws().items()]
            lines.append("")
            lines.append("")
            upstream.sendall("\r\n".join(lines).encode("latin-1"))

            # Read the upstream's response headers, nothing more.
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = upstream.recv(4096)
                if not chunk:
                    # Upstream closed before completing the handshake.
                    self.send_error(502, "upstream closed during websocket "
                                          "handshake")
                    return
                head += chunk
                if len(head) > 65536:
                    self.send_error(502, "upstream handshake headers too large")
                    return

            head, _, rest = head.partition(b"\r\n\r\n")
            status_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            self.wfile.write(head + b"\r\n\r\n")
            self.wfile.flush()
            if " 101 " not in status_line:
                # Not a websocket upstream (e.g. a wrong --upstream-port).
                # Relay the body so the client sees the real reason.
                while rest:
                    self.wfile.write(rest)
                    rest = upstream.recv(4096)
                self.wfile.flush()
                return

            if rest:
                self.wfile.write(rest)
                self.wfile.flush()

            # The connection is live now: pump bytes both ways until either
            # side closes. A thread per direction keeps this non-blocking.
            client = self.connection
            client.settimeout(None)
            upstream.settimeout(None)

            def pump(src, dst):
                try:
                    while True:
                        data = src.recv(65536)
                        if not data:
                            break
                        dst.sendall(data)
                except OSError:
                    pass
                finally:
                    # Unblock the peer so neither pump hangs on a half-close.
                    # shutdown() the *source* half only: shutting down the
                    # destination read side would race the other pump's
                    # sendall(). Each socket is closed exactly once, by the
                    # thread that owns it (dst here == the other side's src).
                    try:
                        dst.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

            t_up = threading.Thread(target=pump, args=(upstream, client),
                                   daemon=True)
            t_down = threading.Thread(target=pump, args=(client, upstream),
                                     daemon=True)
            t_up.start()
            t_down.start()
            t_up.join()
            t_down.join()
            # Both pumps have returned, so no thread can touch these sockets
            # any more. Closing here is what keeps the fd count flat across
            # many reconnects; BaseHTTPRequestHandler does not close them for
            # us once we have taken over the connection.
            try:
                client.close()
            except OSError:
                pass
        except OSError as e:
            sys.stderr.write("websocket relay failed: %s: %s\n"
                             % (type(e).__name__, e))
            try:
                self.send_error(502, "websocket relay failed")
            except OSError:
                pass
        finally:
            try:
                upstream.close()
            except OSError:
                pass

    def _proxy(self, method):
        if method == "GET" and _is_ws_upgrade(self.headers):
            self._proxy_ws()
            return
        conn = http.client.HTTPConnection("127.0.0.1", self.upstream_port, timeout=60)
        try:
            body = None
            length = self.headers.get("Content-Length")
            if length and method in ("POST", "PUT", "PATCH"):
                body = self.rfile.read(int(length))
            conn.request(method, self.path, body=body, headers=self._headers_forwardable())
            resp = conn.getresponse()
            data = resp.read()
            ctype = (resp.getheader("Content-Type") or "").lower()

            if "text/html" in ctype and method == "GET":
                panel = fetch_panel(self.reader, self.project)
                if panel:
                    data = self._inject(data, panel)
                else:
                    # Reader down: serve the board unchanged.
                    self.log_message("panel empty for %s; passing through",
                                     self.project)

            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() in HOP_BY_HOP or k.lower() == "content-length":
                    continue
                if k.lower() == "content-encoding" and "identity" not in (
                        (v or "").lower()):
                    # We asked for identity; if it ignored us we must not claim
                    # the body is encoded when we spliced into it.
                    continue
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(data)
        except Exception as e:  # noqa: BLE001
            # A broken proxy must not take the board down with it.
            self.log_message("proxy error: %s: %s", type(e).__name__, e)
            msg = ("upstream unavailable: %s" % type(e).__name__).encode()
            try:
                self.send_response(502)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                if method != "HEAD":
                    self.wfile.write(msg)
            except Exception:  # noqa: BLE001
                pass
        finally:
            conn.close()

    @staticmethod
    def _inject(data, panel):
        """Splice the panel in before the last </body>.

        The panel carries its own <style>, and is namespaced (vk- prefix,
        #vk-panel scope, all:initial) so it cannot restyle Backlog.md.
        """
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return data
        idx = text.lower().rfind("</body>")
        if idx == -1:
            idx = len(text)
        return (text[:idx] + panel + text[idx:]).encode("utf-8")

    def do_GET(self):  # noqa: N802
        self._proxy("GET")

    def do_HEAD(self):  # noqa: N802
        self._proxy("HEAD")

    def do_POST(self):  # noqa: N802
        self._proxy("POST")

    def do_PUT(self):  # noqa: N802
        self._proxy("PUT")

    def do_PATCH(self):  # noqa: N802
        self._proxy("PATCH")

    def do_DELETE(self):  # noqa: N802
        self._proxy("DELETE")

    def do_OPTIONS(self):  # noqa: N802
        self._proxy("OPTIONS")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True,
                    help="public port to listen on")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--upstream-port", type=int, default=None,
                    help="Backlog.md port (default: same as --port)")
    ap.add_argument("--reader", default="http://127.0.0.1:6424")
    ap.add_argument("--project", required=True,
                    help="project name; must match ports.json")
    args = ap.parse_args()

    Handler.upstream_port = args.upstream_port or args.port
    Handler.reader = args.reader
    Handler.project = args.project

    bind_ips = [b.strip() for b in args.bind.split(",") if b.strip()]
    if not bind_ips:
        bind_ips = ["0.0.0.0"]

    servers = []
    threads = []
    for b in bind_ips:
        srv = Server((b, args.port), Handler)
        servers.append(srv)
        sys.stderr.write(
            "kb-proxy %s:%d -> 127.0.0.1:%d  panel(project=%s, reader=%s)\n"
            % (b, args.port, Handler.upstream_port, args.project, args.reader)
        )
    sys.stderr.flush()

    for srv in servers[:-1]:
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        threads.append(t)

    try:
        servers[-1].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for srv in servers:
            try:
                srv.server_close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())