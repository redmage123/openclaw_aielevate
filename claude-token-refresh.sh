#!/bin/bash
# Claude OAuth Token Refresh Script
# Runs as a cron job to keep the Claude CLI token fresh.
# The token expires after ~8 hours. This script:
# 1. Checks if the token is expiring within 2 hours
# 2. If so, runs a lightweight `claude -p` call to trigger the CLI's internal refresh
# 3. Logs the result

CREDS_FILE="$HOME/.claude/.credentials.json"
LOG_FILE="/tmp/claude-token-refresh.log"

log() {
  echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $1" >> "$LOG_FILE"
}

if [ ! -f "$CREDS_FILE" ]; then
  log "ERROR: Credentials file not found at $CREDS_FILE"
  exit 1
fi

# Extract expiresAt (epoch ms)
EXPIRES_AT=$(python3 -c "import json; print(json.load(open('$CREDS_FILE'))['claudeAiOauth']['expiresAt'])" 2>/dev/null)
if [ -z "$EXPIRES_AT" ]; then
  log "ERROR: Could not read expiresAt from credentials"
  exit 1
fi

# Current time in epoch ms
NOW_MS=$(python3 -c "import time; print(int(time.time() * 1000))")

# Check if token expires within 2 hours (7200000 ms)
THRESHOLD=7200000
REMAINING=$((EXPIRES_AT - NOW_MS))

if [ "$REMAINING" -le 0 ]; then
  log "TOKEN EXPIRED ($REMAINING ms ago). Running claude -p to attempt refresh..."
  RESULT=$(echo "ok" | timeout 60 claude -p "reply with REFRESHED" 2>&1)
  if echo "$RESULT" | grep -q "REFRESHED"; then
    NEW_EXPIRES=$(python3 -c "import json; print(json.load(open('$CREDS_FILE'))['claudeAiOauth']['expiresAt'])" 2>/dev/null)
    log "TOKEN REFRESHED. New expiry: $NEW_EXPIRES"
  else
    log "REFRESH FAILED: $RESULT"
    exit 1
  fi
elif [ "$REMAINING" -le "$THRESHOLD" ]; then
  log "Token expiring soon (${REMAINING}ms remaining). Refreshing..."
  RESULT=$(echo "ok" | timeout 60 claude -p "reply with REFRESHED" 2>&1)
  if echo "$RESULT" | grep -q "REFRESHED"; then
    NEW_EXPIRES=$(python3 -c "import json; print(json.load(open('$CREDS_FILE'))['claudeAiOauth']['expiresAt'])" 2>/dev/null)
    log "TOKEN REFRESHED. New expiry: $NEW_EXPIRES"
  else
    log "REFRESH FAILED: $RESULT"
    exit 1
  fi
else
  HOURS_LEFT=$(python3 -c "print(f'{$REMAINING / 3600000:.1f}')")
  log "Token OK. ${HOURS_LEFT}h remaining. No refresh needed."
fi
