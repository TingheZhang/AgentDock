#!/usr/bin/env bash
# vcc-backlog-supervise.sh -- supervise Backlog.md project boards and dashboard
# with isolated, per-project self-healing (eliminating cascading whole-platform restarts).
#
# Architecture:
#   1. Reads projects, ports, and bind IP dynamically from ports.json.
#   2. Periodically probes every project UI (HTTP 200 check + loopback upstream check)
#      and dashboard (HTTP 200 check).
#   3. If any single project board or upstream fails (e.g. 502 or 000), it restarts
#      ONLY that specific project via `bb-ports.sh start|stop <name>`.
#   4. Healthy projects remain completely online with zero disruption.
#   5. Circuit breaker: if a project fails 3 times consecutively, it cools down for
#      60s to prevent rapid flapping/thrashing, keeping the rest of the platform stable.
#   6. Clean shutdown: handles SIGTERM / SIGINT by cleanly stopping all children and
#      exiting with code 0 so systemd recognizes a clean stop.

set -uo pipefail

BB="$PLATFORM_ROOT/platform/bb-ports.sh"
PLATFORM="${BB%/*}"
REGISTRY="$PLATFORM/ports.json"
STATE="$PLATFORM/.state"
PIDS="$STATE/pids"
LOGDIR="$STATE/logs"
POLL_INTERVAL=5
MAX_RETRIES=3
COOL_DOWN_SEC=60

log() { printf '%s supervise: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

# Run bb-ports.sh with output directed to a log file
bb_run() {
  local action="$1"; shift
  local out="$LOGDIR/supervise-$action.log"
  mkdir -p "$LOGDIR"
  "$BB" "$action" "$@" > "$out" 2>&1
  local rc=$?
  sed 's/^/  /' "$out" >&2
  return $rc
}

read_projects() {
  python3 -c '
import json, sys
try:
    cfg = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
for p in cfg.get("projects", []):
    name = p.get("name")
    port = p.get("ui_port")
    if name and port:
        print(f"{name}\t{port}")
' "$REGISTRY"
}

read_dashboard_port() {
  python3 -c '
import json, sys
try:
    cfg = json.load(open(sys.argv[1]))
    print(cfg.get("dashboard_port") or "")
except Exception:
    print("")
' "$REGISTRY"
}

read_bind_ip() {
  python3 -c '
import json, sys
try:
    cfg = json.load(open(sys.argv[1]))
    print(cfg.get("bind_ip") or "127.0.0.1")
except Exception:
    print("127.0.0.1")
' "$REGISTRY"
}

BIND_IP="$(read_bind_ip)"
BIND_IP="${BIND_IP%%,*}"
DPORT="$(read_dashboard_port)"

port_pids() {
  ss -tlnp 2>/dev/null | grep -E ":$1\b" | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u
}

is_loopback_listening() {
  local port="$1"
  ss -tln 2>/dev/null | grep -qE "127\.0\.0\.1:$port\b"
}

code() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 6 "$1" 2>/dev/null || echo 000
}

heal_project() {
  local name="$1" port="$2"
  log "ISOLATED HEAL: project '$name' on port $port is unhealthy. Restarting ONLY this project..."
  bb_run stop "$name"
  sleep 1
  # Sweep any stale processes holding this port
  local stale_pids
  stale_pids="$(port_pids "$port")"
  if [[ -n "$stale_pids" ]]; then
    for pid in $stale_pids; do
      kill -9 "$pid" 2>/dev/null || true
    done
  fi
  bb_run start "$name"
  sleep 3
  local new_code
  new_code="$(code "http://$BIND_IP:$port/")"
  if [[ "$new_code" == "200" ]]; then
    log "ISOLATED HEAL: project '$name' on port $port successfully recovered (HTTP 200)"
    return 0
  else
    log "WARNING: project '$name' on port $port restart returned HTTP $new_code"
    return 1
  fi
}

heal_dashboard() {
  local dport="$1"
  log "ISOLATED HEAL: dashboard on port $dport is unhealthy. Restarting dashboard..."
  bb_run stop dashboard
  sleep 1
  local stale_pids
  stale_pids="$(port_pids "$dport")"
  if [[ -n "$stale_pids" ]]; then
    for pid in $stale_pids; do
      kill -9 "$pid" 2>/dev/null || true
    done
  fi
  bb_run start dashboard
  sleep 3
  local new_code
  new_code="$(code "http://$BIND_IP:$dport/")"
  if [[ "$new_code" == "200" ]]; then
    log "ISOLATED HEAL: dashboard on port $dport successfully recovered (HTTP 200)"
    return 0
  else
    log "WARNING: dashboard restart returned HTTP $new_code"
    return 1
  fi
}

do_stop() {
  log "stopping all children"
  bb_run stop
}

case "${1:-run}" in
  stop)
    do_stop
    exit 0
    ;;

  run)
    log "starting via $BB start"
    if ! bb_run start; then
      log "ERROR: bb-ports.sh start failed"
      exit 1
    fi

    sleep 3
    log "platform started, entering isolated supervision loop (poll every ${POLL_INTERVAL}s)"

    TRAP=0
    on_term() { TRAP=1; log "received SIGTERM, will clean up and exit 0"; }
    on_int()  { TRAP=1; log "received SIGINT, will clean up and exit 0"; }
    trap on_term TERM
    trap on_int  INT

    declare -A FAIL_COUNT
    declare -A LAST_HEAL_TIME

    while [[ "$TRAP" -eq 0 ]]; do
      _waited=0
      while [[ "$_waited" -lt "$POLL_INTERVAL" && "$TRAP" -eq 0 ]]; do
        sleep 1
        _waited=$((_waited + 1))
      done
      [[ "$TRAP" -ne 0 ]] && break

      now=$(date +%s)

      # Check each project individually
      while IFS=$'\t' read -r name port; do
        [[ -n "$name" && -n "$port" ]] || continue
        [[ "$TRAP" -ne 0 ]] && break

        local_c="$(code "http://$BIND_IP:$port/")"
        local_loopback=0
        if is_loopback_listening "$port"; then
          local_loopback=1
        fi

        # Healthy when HTTP 200 and loopback upstream listening
        if [[ "$local_c" == "200" && "$local_loopback" -eq 1 ]]; then
          FAIL_COUNT["$name"]=0
          continue
        fi

        [[ "$TRAP" -ne 0 ]] && break

        # Failure detected
        retries="${FAIL_COUNT[$name]:-0}"
        last_time="${LAST_HEAL_TIME[$name]:-0}"
        elapsed=$(( now - last_time ))

        if [[ "$retries" -ge "$MAX_RETRIES" && "$elapsed" -lt "$COOL_DOWN_SEC" ]]; then
          continue
        fi

        if [[ "$retries" -ge "$MAX_RETRIES" && "$elapsed" -ge "$COOL_DOWN_SEC" ]]; then
          FAIL_COUNT["$name"]=0
          retries=0
        fi

        log "DEFECT DETECTED on project '$name' (port $port): HTTP $local_c, upstream_listening=$local_loopback (attempt $((retries + 1))/$MAX_RETRIES)"
        FAIL_COUNT["$name"]=$(( retries + 1 ))
        LAST_HEAL_TIME["$name"]=$now

        heal_project "$name" "$port"

        if [[ "${FAIL_COUNT[$name]}" -ge "$MAX_RETRIES" ]]; then
          log "WARNING: project '$name' reached max retries ($MAX_RETRIES). Cooling down for ${COOL_DOWN_SEC}s to prevent thrashing. Other projects remain active."
        fi
      done < <(read_projects)

      [[ "$TRAP" -ne 0 ]] && break

      # Check dashboard
      if [[ -n "$DPORT" ]]; then
        dash_c="$(code "http://$BIND_IP:$DPORT/")"
        if [[ "$dash_c" == "200" ]]; then
          FAIL_COUNT["dashboard"]=0
        else
          if [[ "$TRAP" -eq 0 ]]; then
            d_retries="${FAIL_COUNT[dashboard]:-0}"
            d_last="${LAST_HEAL_TIME[dashboard]:-0}"
            d_elapsed=$(( now - d_last ))

            if [[ "$d_retries" -lt "$MAX_RETRIES" || "$d_elapsed" -ge "$COOL_DOWN_SEC" ]]; then
              if [[ "$d_retries" -ge "$MAX_RETRIES" ]]; then
                FAIL_COUNT["dashboard"]=0
                d_retries=0
              fi
              log "DEFECT DETECTED on dashboard (port $DPORT): HTTP $dash_c (attempt $((d_retries + 1))/$MAX_RETRIES)"
              FAIL_COUNT["dashboard"]=$(( d_retries + 1 ))
              LAST_HEAL_TIME["dashboard"]=$now
              heal_dashboard "$DPORT"
            fi
          fi
        fi
      fi
    done

    do_stop
    log "exited cleanly on signal"
    exit 0
    ;;

  *)
    echo "Usage: $0 {run|stop}" >&2
    exit 2
    ;;
esac
