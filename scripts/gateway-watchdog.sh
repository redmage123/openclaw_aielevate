#!/bin/bash
# Gateway Watchdog
# Checks if the OpenClaw gateway is healthy and restarts it if unresponsive.
#
# Run via cron or systemd timer:
#   */3 * * * * /home/bbrelin/openclaw/scripts/gateway-watchdog.sh
#
# Environment variables:
#   GATEWAY_PORT   - Gateway port (default: 18789)
#   HEALTH_TIMEOUT - Seconds to wait for health response (default: 10)
#   NOTIFY_NTFY    - ntfy.sh topic for push notifications (optional)
#   MAX_RESTARTS   - Max restarts per hour before giving up (default: 3)

set -euo pipefail

GATEWAY_PORT="${GATEWAY_PORT:-18789}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-10}"
NOTIFY_NTFY="${NOTIFY_NTFY:-}"
MAX_RESTARTS="${MAX_RESTARTS:-3}"

STATE_DIR="$HOME/.openclaw"
STATE_FILE="$STATE_DIR/gateway-watchdog-state"
LOG_FILE="/tmp/openclaw-gateway-watchdog.log"

mkdir -p "$STATE_DIR"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

send_notification() {
    local message="$1"
    local priority="${2:-default}"

    if [ -n "$NOTIFY_NTFY" ]; then
        curl -s -o /dev/null \
            -H "Title: OpenClaw Gateway Watchdog" \
            -H "Priority: $priority" \
            -H "Tags: warning,server" \
            -d "$message" \
            "https://ntfy.sh/$NOTIFY_NTFY" 2>/dev/null || true
    fi
}

# Throttle: track restarts in the last hour
NOW=$(date +%s)
HOUR_AGO=$((NOW - 3600))

# Read recent restart timestamps (one per line)
RECENT_RESTARTS=0
if [ -f "$STATE_FILE" ]; then
    RECENT_RESTARTS=$(awk -v cutoff="$HOUR_AGO" '$1 > cutoff' "$STATE_FILE" | wc -l)
fi

# Check 1: Is the gateway process running?
GATEWAY_PID=$(ss -ltnp "sport = :$GATEWAY_PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1 || true)

if [ -z "$GATEWAY_PID" ]; then
    log "Gateway not listening on port $GATEWAY_PORT"
    NEEDS_RESTART=true
    REASON="not-listening"
else
    # Check 2: Can we get a health response?
    HEALTH_OK=false
    if command -v openclaw &>/dev/null; then
        if timeout "$HEALTH_TIMEOUT" openclaw health --json &>/dev/null; then
            HEALTH_OK=true
        fi
    else
        # Fallback: just check if port responds to a TCP connection
        if timeout "$HEALTH_TIMEOUT" bash -c "echo > /dev/tcp/127.0.0.1/$GATEWAY_PORT" 2>/dev/null; then
            HEALTH_OK=true
        fi
    fi

    if $HEALTH_OK; then
        log "Gateway healthy (pid=$GATEWAY_PID, port=$GATEWAY_PORT)"
        exit 0
    else
        log "Gateway unresponsive (pid=$GATEWAY_PID, port=$GATEWAY_PORT)"
        NEEDS_RESTART=true
        REASON="unresponsive"
    fi
fi

# Throttle check
if [ "$RECENT_RESTARTS" -ge "$MAX_RESTARTS" ]; then
    log "THROTTLED: $RECENT_RESTARTS restarts in last hour (max=$MAX_RESTARTS). Skipping restart."
    send_notification "Gateway watchdog throttled — $RECENT_RESTARTS restarts in last hour. Manual intervention needed." "urgent"
    exit 1
fi

# Restart the gateway
log "Restarting gateway (reason=$REASON, restarts_this_hour=$RECENT_RESTARTS)"
send_notification "Restarting OpenClaw gateway ($REASON)" "high"

# Kill existing process
if [ -n "${GATEWAY_PID:-}" ]; then
    kill "$GATEWAY_PID" 2>/dev/null || true
    sleep 2
    kill -9 "$GATEWAY_PID" 2>/dev/null || true
    sleep 1
fi

# Start fresh
nohup openclaw gateway run --bind loopback --port "$GATEWAY_PORT" --force > /tmp/openclaw-gateway.log 2>&1 &
NEW_PID=$!

# Wait for startup
sleep 5

# Verify
if ss -ltnp "sport = :$GATEWAY_PORT" 2>/dev/null | grep -q "pid="; then
    log "Gateway restarted successfully (new pid=$(ss -ltnp "sport = :$GATEWAY_PORT" | grep -oP 'pid=\K[0-9]+' | head -1))"
    send_notification "Gateway recovered successfully" "default"
else
    log "FAILED to restart gateway"
    send_notification "Gateway restart FAILED — manual intervention needed" "urgent"
fi

# Record this restart
echo "$NOW" >> "$STATE_FILE"

# Trim old entries from state file (keep last 24 hours)
DAY_AGO=$((NOW - 86400))
if [ -f "$STATE_FILE" ]; then
    awk -v cutoff="$DAY_AGO" '$1 > cutoff' "$STATE_FILE" > "${STATE_FILE}.tmp"
    mv "${STATE_FILE}.tmp" "$STATE_FILE"
fi
