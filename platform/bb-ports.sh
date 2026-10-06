#!/usr/bin/env bash
# bb-ports.sh -- manage Backlog.md Web UI ports and the aggregate dashboard.
#
# One registry (ports.json) is the single source of truth. It is read by:
#   - this script, to know which project serves on which port
#   - bb-aggregate.py, to build the dashboard's project list and links
# So the dashboard can never disagree with what is actually running.
#
# Usage:
#   bb-ports.sh start          # start every project UI + the dashboard
#   bb-ports.sh start <name>   # start one project (name from ports.json)
#   bb-ports.sh stop [<name>]  # stop one, or all
#   bb-ports.sh restart [<name>]
#   bb-ports.sh status         # show registry + what is listening
#   bb-ports.sh refresh        # regenerate the dashboard only
#   bb-ports.sh add <name> <path> <group> <port>
#   bb-ports.sh remove <name>
#
# Everything it creates lives under $STATE (pids, logs, relay scripts) so that
# nothing important is written to /tmp, which is wiped on reboot.

set -uo pipefail

export PATH="$HOME/.local/bin:$PATH"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="$HERE/ports.json"
AGGREGATE="$HERE/bb-aggregate.py"
KB_PROXY="$HERE/kb-proxy.py"
# Where the knowledge panel is rendered. Kept in one place so the boards and
# the reader can never drift; override to point at a different instance.
READER_URL="${VCC_READER_URL:-http://127.0.0.1:6424}"
WWW="$HERE/www"
STATE="$HERE/.state"
LOGDIR="$STATE/logs"
PIDS="$STATE/pids"

BB_SERVE="${BB_SERVE:-/tmp/bb-serve.sh}"

log() { printf '  %s\n' "$*" >&2; }

# Whether a project's board should get the knowledge panel injected.
# Reads knowledge.panel from ports.json, so the decision lives with the rest
# of the project definition rather than in this script.
project_panel_enabled() {
  local name="$1"
  python3 - "$REGISTRY" "$name" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as fh:
        cfg = json.load(fh)
except Exception:
    sys.exit(1)
for p in cfg.get("projects", []):
    if p.get("name") == sys.argv[2]:
        sys.exit(0 if (p.get("knowledge") or {}).get("panel") else 1)
sys.exit(1)
PY
}

# Is the knowledge reader up? A board with no panel is fine; a board whose
# proxy 502s because the reader died is not.
reader_up() {
  [[ "$(http_code "$READER_URL/api/projects")" == "200" ]]
}

# ---------------------------------------------------------------- registry ---

registry_get() {
  # registry_get <key> -- print a top-level scalar from ports.json
  python3 - "$REGISTRY" "$1" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as fh:
        cfg = json.load(fh)
except Exception as exc:
    sys.stderr.write("cannot read registry: %s\n" % exc)
    sys.exit(1)
val = cfg.get(sys.argv[2], "")
print("" if val is None else val)
PY
}

list_projects() {
  # Emit one TSV row per project: name \t path \t group \t port
  python3 - "$REGISTRY" <<'PY'
import json, sys
with open(sys.argv[1]) as fh:
    cfg = json.load(fh)
for p in cfg.get("projects", []):
    print("\t".join([
        str(p.get("name", "")),
        str(p.get("path", "")),
        str(p.get("group", "")),
        str(p.get("ui_port", "")),
    ]))
PY
}

require_registry() {
  [[ -f "$REGISTRY" ]] || {
    echo "ERROR: no registry at $REGISTRY" >&2
    echo "  create it, or run: bb-ports.sh add <name> <path> <group> <port>" >&2
    exit 1
  }
}

# ------------------------------------------------------------------ helpers ---

port_pids() {
  # All pids holding a TCP port (listeners included).
  ss -tlnp 2>/dev/null | grep -E ":$1\b" | grep -oP 'pid=\K[0-9]+' | sort -u
}

is_listening() {
  ss -tln 2>/dev/null | grep -qE ":$1\b"
}

free_port() {
  # First free port at or above $1.
  local p="$1"
  while is_listening "$p"; do
    if [[ "$p" -ge 65000 ]]; then
      echo "ERROR: no free port at or above $1" >&2
      return 1
    fi
    p=$((p + 1))
  done
  echo "$p"
}

http_code() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$1" 2>/dev/null || echo 000
}

# Start the LAN relay for a port that already has a loopback listener.
#
# Two modes, and the difference matters:
#   raw TCP  - byte-for-byte splice. Correct for the dashboard.
#   HTTP     - parses the response so it can inject the knowledge panel.
#              Required for project boards, which are closed-source UIs we
#              cannot modify from the inside.
start_relay() {
  local port="$1" bind="$2" project="${3:-}"
  local first_bind="${bind%%,*}"
  local run="$STATE/$port"
  mkdir -p "$run"

  if [[ -n "$project" ]] && [[ -f "$KB_PROXY" ]] && project_panel_enabled "$project"; then
    log "relay $port: HTTP proxy with knowledge panel (project=$project, bind=$bind)"
    nohup python3 "$KB_PROXY" \
      --port "$port" --bind "$bind" --project "$project" \
      --reader "$READER_URL" \
      > "$LOGDIR/relay-$port.log" 2>&1 &
    echo $! > "$PIDS/relay-$port.pid"
    local i
    for i in $(seq 1 30); do
      ss -tln 2>/dev/null | grep -q "$first_bind:$port " && return 0
      sleep 1
    done
    log "relay $port: proxy did not bind $first_bind:$port after 30s; see $LOGDIR/relay-$port.log"
    return 1
  fi

  # No panel wanted: raw TCP splice supporting multi-bind.
  [[ -n "$project" ]] && log "relay $port: raw TCP (panel disabled for $project, bind=$bind)"
  cat > "$run/relay.py" <<PYEOF
import socket, threading, sys
BINDS = [b.strip() for b in "$bind".split(",") if b.strip()]
TARGET = ("127.0.0.1", $port)

def pipe(a, b):
    try:
        while True:
            chunk = a.recv(65536)
            if not chunk:
                break
            b.sendall(chunk)
    except Exception:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass

def handle(client):
    try:
        upstream = socket.create_connection(TARGET, timeout=10)
    except Exception:
        client.close()
        return
    threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
    pipe(upstream, client)

def listen_ip(ip):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((ip, $port))
    srv.listen(64)
    sys.stderr.write("relay %s:%d -> %s:%d\\n" % (ip, $port, TARGET[0], TARGET[1]))
    sys.stderr.flush()
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()

for ip in BINDS[:-1]:
    threading.Thread(target=listen_ip, args=(ip,), daemon=True).start()
if BINDS:
    listen_ip(BINDS[-1])
PYEOF
  nohup python3 "$run/relay.py" > "$run/relay.log" 2>&1 &
  echo $! > "$PIDS/relay-$port.pid"
  sleep 2
}

# ------------------------------------------------------------------ actions ---

start_one() {
  local name="$1" path="$2" group="$3" port="$4" bind="$5"
  local first_bind="${bind%%,*}"

  if [[ ! -d "$path" ]]; then
    printf '  %-16s SKIP  (no such directory: %s)\n' "$name" "$path"
    return 0
  fi
  if [[ ! -f "$path/backlog/config.yml" && ! -f "$path/.backlog/config.yml" ]]; then
    printf '  %-16s SKIP  (not a Backlog.md project: no backlog/ or .backlog/config.yml)\n' "$name"
    return 0
  fi

  if is_listening "$port"; then
    local code
    code="$(http_code "http://$first_bind:$port/")"
    if [[ "$code" == "200" ]]; then
      printf '  %-16s UP    port %s  http %s\n' "$name" "$port" "$code"
      return 0
    fi
    if ! ss -tln 2>/dev/null | grep -q "127.0.0.1:$port "; then
      log "$name: port $port bound but http $code and no loopback board; clearing stale proxy"
      local stale
      stale="$(ss -tlnp 2>/dev/null | grep -E " ($first_bind|$bind):$port " \
               | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)"
      [[ -n "$stale" ]] && kill "$stale" 2>/dev/null
      rm -f "$PIDS/relay-$port.pid"
      sleep 1
    else
      printf '  %-16s UP    port %s  http %s\n' "$name" "$port" "$code"
      return 0
    fi
  fi

  port="$(free_port "$port")"
  mkdir -p "$LOGDIR" "$PIDS"

  ( cd "$path" && BACKLOG_CWD="$path" nohup backlog browser \
      --port "$port" --no-open --non-interactive \
      > "$LOGDIR/$name.browser.log" 2>&1 & echo $! > "$PIDS/$name.browser.pid" )
  sleep 4

  if ! ss -tln 2>/dev/null | grep -q "127.0.0.1:$port"; then
    printf '  %-16s FAIL  browser did not start; see %s\n' "$name" "$LOGDIR/$name.browser.log"
    return 1
  fi

  if project_panel_enabled "$name" && [[ -f "$KB_PROXY" ]] && reader_up; then
    if ! start_relay "$port" "$bind" "$name"; then
      printf '  %-16s FAIL  panel proxy did not start on %s:%s\n' "$name" "$bind" "$port"
      return 1
    fi
  else
    project_panel_enabled "$name" && [[ -f "$KB_PROXY" ]] \
      && log "reader $READER_URL is down; starting $name without a knowledge panel"
    start_relay "$port" "$bind"
  fi
  if ! ss -tln 2>/dev/null | grep -q "$first_bind:$port "; then
    printf '  %-16s FAIL  relay did not bind %s:%s\n' "$name" "$first_bind" "$port"
    return 1
  fi

  local code
  code="$(http_code "http://$first_bind:$port/")"
  printf '  %-16s UP    port %s  http %s\n' "$name" "$port" "$code"
}

stop_one() {
  local name="$1" port="$2"
  for f in "$PIDS/$name.browser.pid" "$PIDS/relay-$port.pid"; do
    [[ -f "$f" ]] || continue
    local pid; pid="$(cat "$f")"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null && printf '  %-16s stopped pid %s\n' "$name" "$pid"
    fi
    rm -f "$f"
  done
  # Sweep anything still holding the port (e.g. after a crash without cleanup).
  local pid
  for pid in $(port_pids "$port"); do
    kill "$pid" 2>/dev/null && printf '  %-16s swept pid %s\n' "$name" "$pid"
  done
}

do_start() {
  require_registry
  mkdir -p "$LOGDIR" "$PIDS" "$WWW"
  local bind_ip; bind_ip="$(registry_get bind_ip)"
  [[ -n "$bind_ip" ]] || bind_ip="127.0.0.1"
  echo "bind ip: $bind_ip"
  echo "starting project UIs:"

  if [[ $# -gt 0 ]]; then
    local only="$1"
    if [[ "$only" == "dashboard" ]]; then
      refresh_dashboard
      start_dashboard
      return 0
    fi
    local found=0
    while IFS=$'\t' read -r n p g port; do
      [[ "$n" == "$only" ]] || continue
      found=1
      start_one "$n" "$p" "$g" "$port" "$bind_ip"
    done < <(list_projects)
    [[ "$found" -eq 1 ]] || { echo "ERROR: no project named '$only' in $REGISTRY" >&2; exit 1; }
  else
    while IFS=$'\t' read -r n p g port; do
      start_one "$n" "$p" "$g" "$port" "$bind_ip"
    done < <(list_projects)
  fi

  refresh_dashboard
  start_dashboard
}

do_stop() {
  require_registry
  if [[ $# -gt 0 ]]; then
    local only="$1"
    if [[ "$only" == "dashboard" ]]; then
      local dport; dport="$(registry_get dashboard_port)"
      if [[ -n "$dport" ]]; then
        for pid in $(port_pids "$dport"); do
          kill "$pid" 2>/dev/null && printf '  %-16s stopped pid %s\n' dashboard "$pid"
        done
        rm -f "$PIDS/dashboard.pid" "$PIDS/relay-$dport.pid"
      fi
      return 0
    fi
    while IFS=$'\t' read -r n p g port; do
      [[ "$n" == "$only" ]] || continue
      stop_one "$n" "$port"
    done < <(list_projects)
  else
    while IFS=$'\t' read -r n p g port; do
      stop_one "$n" "$port"
    done < <(list_projects)
    local dport; dport="$(registry_get dashboard_port)"
    if [[ -n "$dport" ]]; then
      for pid in $(port_pids "$dport"); do
        kill "$pid" 2>/dev/null && printf '  %-16s stopped pid %s\n' dashboard "$pid"
      done
    fi
  fi
}

refresh_dashboard() {
  [[ -f "$AGGREGATE" ]] || { echo "  (aggregate script missing: $AGGREGATE)" >&2; return 1; }
  require_registry
  mkdir -p "$WWW"
  local out="$WWW/index.html"
  python3 "$AGGREGATE" --config "$REGISTRY" --out "$out" --refresh-minutes 5
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "  dashboard generation FAILED (rc=$rc)" >&2
    return $rc
  fi
  cp "$out" "$HERE/aggregate.html" 2>/dev/null
  echo "  dashboard written: $out"
}

start_dashboard() {
  local dport; dport="$(registry_get dashboard_port)"
  [[ -n "$dport" ]] || return 0
  local bind; bind="$(registry_get bind_ip)"
  [[ -n "$bind" ]] || bind="127.0.0.1"
  local first_bind="${bind%%,*}"

  if ss -tln 2>/dev/null | grep -q "$first_bind:$dport"; then
    local code
    code="$(http_code "http://$first_bind:$dport/")"
    if [[ "$code" == "200" ]]; then
      printf '  %-16s UP    port %s  http %s\n' "dashboard" "$dport" "$code"
      return 0
    fi
    if ! ss -tln 2>/dev/null | grep -q "127.0.0.1:$dport "; then
      log "dashboard: port $dport bound but http $code and no loopback server; clearing stale relay"
      local stale
      stale="$(ss -tlnp 2>/dev/null | grep -E " ($first_bind|$bind):$dport " \
               | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)"
      [[ -n "$stale" ]] && kill "$stale" 2>/dev/null
      rm -f "$PIDS/relay-$dport.pid"
      sleep 1
    else
      printf '  %-16s UP    port %s  http %s\n' "dashboard" "$dport" "$code"
      return 0
    fi
  fi

  if ! is_listening "$dport"; then
    mkdir -p "$LOGDIR" "$PIDS"
    ( cd "$WWW" && nohup python3 -m http.server "$dport" --bind 127.0.0.1 \
        > "$LOGDIR/dashboard.log" 2>&1 & echo $! > "$PIDS/dashboard.pid" )
    sleep 2
  fi
  start_relay "$dport" "$bind"
  printf '  %-16s UP    port %s  http %s\n' "dashboard" "$dport" "$(http_code "http://$first_bind:$dport/")"
}

do_status() {
  require_registry
  local bind; bind="$(registry_get bind_ip)"
  [[ -n "$bind" ]] || bind="127.0.0.1"
  local first_bind="${bind%%,*}"
  local dport; dport="$(registry_get dashboard_port)"

  printf '%-18s %-9s %-9s %-7s %s\n' NAME GROUP PORT STATE HTTP
  while IFS=$'\t' read -r n p g port; do
    local state="down" code="000"
    if is_listening "$port"; then
      if ss -tln 2>/dev/null | grep -q "$first_bind:$port"; then
        state="up"; code="$(http_code "http://$first_bind:$port/")"
      else
        state="loopback"
      fi
    fi
    printf '%-18s %-9s %-9s %-7s %s\n' "$n" "$g" "$port" "$state" "$code"
  done < <(list_projects)

  if [[ -n "$dport" ]]; then
    local state="down" code="000"
    if is_listening "$dport"; then
      if ss -tln 2>/dev/null | grep -q "$first_bind:$dport"; then
        state="up"; code="$(http_code "http://$first_bind:$dport/")"
      else
        state="loopback"
      fi
    fi
    printf '%-18s %-9s %-9s %-7s %s\n' "(dashboard)" "-" "$dport" "$state" "$code"
    echo
    echo "Open: http://$first_bind:$dport/"
  fi
}

do_add() {
  require_registry
  local name="${1:-}" path="${2:-}" group="${3:-}" port="${4:-}"
  if [[ -z "$name" || -z "$path" || -z "$port" ]]; then
    echo "Usage: bb-ports.sh add <name> <path> <group> <port>" >&2
    exit 2
  fi
  [[ -d "$path" ]] || { echo "ERROR: no such directory: $path" >&2; exit 1; }
  python3 - "$REGISTRY" "$name" "$path" "$group" "$port" <<'PY'
import json, sys
path, name, pdir, group, port = sys.argv[1:6]
with open(path) as fh:
    cfg = json.load(fh)
if not isinstance(cfg, dict):
    cfg = {"projects": []}
cfg.setdefault("projects", [])
for p in cfg["projects"]:
    if p.get("name") == name:
        sys.exit("ERROR: a project named %r already exists (remove it first)" % name)
cfg["projects"].append({
    "name": name, "path": pdir,
    "group": group or name, "ui_port": int(port),
})
with open(path, "w") as fh:
    json.dump(cfg, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
print("added %s -> %s (port %s)" % (name, pdir, port))
PY
}

do_remove() {
  require_registry
  local name="${1:-}"
  [[ -n "$name" ]] || { echo "Usage: bb-ports.sh remove <name>" >&2; exit 2; }
  python3 - "$REGISTRY" "$name" <<'PY'
import json, sys
path, name = sys.argv[1:3]
with open(path) as fh:
    cfg = json.load(fh)
before = len(cfg.get("projects", []))
cfg["projects"] = [p for p in cfg.get("projects", []) if p.get("name") != name]
if len(cfg["projects"]) == before:
    sys.exit("ERROR: no project named %r" % name)
with open(path, "w") as fh:
    json.dump(cfg, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
print("removed %s" % name)
PY
}

case "${1:-status}" in
  start)   shift; if [[ $# -gt 0 ]]; then do_start "$1"; else do_start; fi ;;
  stop)    shift; if [[ $# -gt 0 ]]; then do_stop "$1";  else do_stop;  fi ;;
  restart) shift
           if [[ $# -gt 0 ]]; then do_stop "$1"; do_start "$1"; else do_stop; do_start; fi ;;
  status)  do_status ;;
  refresh) refresh_dashboard ;;
  add)     shift; do_add "$@" ;;
  remove)  shift; do_remove "$@" ;;
  *) echo "Usage: $0 {start [<name>] | stop [<name>] | restart [<name>] | status | refresh | add <name> <path> <group> <port> | remove <name>}" >&2; exit 2 ;;
esac
