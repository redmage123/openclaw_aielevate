#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGFILE="/tmp/openclaw-gateway.log"
PIDFILE="/tmp/openclaw-gateway.pid"
PORT=18789
BIND="custom"

is_running() {
  [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

wait_for_port() {
  local max_wait=15
  local waited=0
  while [ "$waited" -lt "$max_wait" ]; do
    if ss -ltnp 2>/dev/null | grep -q ":${PORT} "; then
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  return 1
}

case "${1:-}" in
  start)
    if is_running; then
      echo "Gateway already running (PID $(cat "$PIDFILE"))"
      exit 1
    fi
    # Clean stale PID file
    rm -f "$PIDFILE"

    echo "Starting OpenClaw gateway (port=$PORT, bind=$BIND, auth=multi-user)..."
    : > "$LOGFILE"
    nohup openclaw gateway run \
      --port "$PORT" \
      --bind "$BIND" \
      --force \
      >> "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    echo "Gateway PID: $! — log: $LOGFILE"

    # Wait for the port to become available
    if wait_for_port; then
      echo "Gateway is running on port $PORT"
    elif is_running; then
      echo "Gateway process is alive but port $PORT not yet listening (check log)"
    else
      echo "Gateway failed to start. Last 20 lines of log:"
      tail -20 "$LOGFILE"
      rm -f "$PIDFILE"
      exit 1
    fi
    ;;

  stop)
    if [ ! -f "$PIDFILE" ]; then
      # Fall back to process name match
      if pkill -f "openclaw gateway run" 2>/dev/null; then
        echo "Gateway stopped (found by process name)"
      else
        echo "No gateway process found"
      fi
      exit 0
    fi
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      kill "$PID"
      # Wait for clean shutdown
      for _ in $(seq 1 10); do
        kill -0 "$PID" 2>/dev/null || break
        sleep 0.5
      done
      if kill -0 "$PID" 2>/dev/null; then
        echo "Gateway did not exit cleanly, sending SIGKILL..."
        kill -9 "$PID" 2>/dev/null || true
      fi
      echo "Gateway stopped (PID $PID)"
    else
      echo "Gateway not running (stale PID $PID)"
    fi
    rm -f "$PIDFILE"
    ;;

  restart)
    "$0" stop
    sleep 1
    "$0" start
    ;;

  status)
    if is_running; then
      PID=$(cat "$PIDFILE")
      echo "Gateway running (PID $PID)"
      ss -ltnp 2>/dev/null | grep ":$PORT " || echo "  (port $PORT not yet listening)"
      # Show uptime
      if [ -d "/proc/$PID" ]; then
        STARTED=$(stat -c %Y "/proc/$PID" 2>/dev/null || echo "")
        if [ -n "$STARTED" ]; then
          NOW=$(date +%s)
          UPTIME=$(( NOW - STARTED ))
          HOURS=$(( UPTIME / 3600 ))
          MINS=$(( (UPTIME % 3600) / 60 ))
          echo "  Uptime: ${HOURS}h ${MINS}m"
        fi
      fi
    else
      echo "Gateway not running"
      rm -f "$PIDFILE" 2>/dev/null || true
      exit 1
    fi
    ;;

  log|logs)
    if [ "${2:-}" = "-n" ] && [ -n "${3:-}" ]; then
      tail -n "$3" "$LOGFILE"
    else
      tail -f "$LOGFILE"
    fi
    ;;

  *)
    echo "Usage: $0 {start|stop|restart|status|log}"
    echo ""
    echo "  start    Start the gateway in the background"
    echo "  stop     Stop the running gateway"
    echo "  restart  Stop then start the gateway"
    echo "  status   Show whether the gateway is running"
    echo "  log      Tail the gateway log (use 'log -n 50' for last 50 lines)"
    exit 1
    ;;
esac
