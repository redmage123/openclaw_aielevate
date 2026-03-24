#!/bin/bash
# Claude Usage Tracker & Alert System
# Monitors proxy usage and sends alerts at 75% and 95% thresholds
#
# Claude Max 20x approximate limits:
#   - 5-hour rolling window: ~800 requests (conservative)
#   - Weekly: ~50M tokens (Opus-weighted)

USAGE_DIR="$HOME/.openclaw/usage"
USAGE_LOG="$USAGE_DIR/requests.jsonl"
ALERT_STATE="$USAGE_DIR/alert-state.json"
ALERT_SCRIPT="$HOME/send-alert.py"

SESSION_LIMIT=800
WEEKLY_TOKEN_LIMIT=50000000

mkdir -p "$USAGE_DIR"

[ ! -f "$ALERT_STATE" ] && echo '{"session_75":false,"session_95":false,"weekly_75":false,"weekly_95":false}' > "$ALERT_STATE"
[ ! -f "$USAGE_LOG" ] && touch "$USAGE_LOG"

send_alert() {
    local title="$1"
    local body="$2"
    local key="$3"

    already=$(python3 -c "import json; print(json.load(open('$ALERT_STATE')).get('$key', False))" 2>/dev/null)
    [ "$already" = "True" ] && return

    python3 "$ALERT_SCRIPT" "$title" "$body" 2>/dev/null
    
    python3 -c "
import json
s = json.load(open('$ALERT_STATE'))
s['$key'] = True
json.dump(s, open('$ALERT_STATE', 'w'), indent=2)
" 2>/dev/null

    echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] ALERT: $title" >> "$USAGE_DIR/alerts.log"
}

# Count requests in last 5 hours
FIVE_H_AGO=$(python3 -c "import time; print(int((time.time() - 5*3600) * 1000))")
SESSION_COUNT=$(python3 -c "
count = 0
try:
    for line in open('$USAGE_LOG'):
        import json
        d = json.loads(line.strip())
        if d.get('ts', 0) >= $FIVE_H_AGO: count += 1
except: pass
print(count)
" 2>/dev/null)

# Count tokens in last 7 days
SEVEN_D_AGO=$(python3 -c "import time; print(int((time.time() - 7*86400) * 1000))")
WEEKLY_TOKENS=$(python3 -c "
import json
total = 0
try:
    for line in open('$USAGE_LOG'):
        d = json.loads(line.strip())
        if d.get('ts', 0) >= $SEVEN_D_AGO:
            total += d.get('input_tokens', 0) + d.get('output_tokens', 0) + d.get('cache_read', 0) + d.get('cache_write', 0)
except: pass
print(total)
" 2>/dev/null)

SESSION_PCT=$(python3 -c "print(round($SESSION_COUNT / $SESSION_LIMIT * 100, 1))")
WEEKLY_PCT=$(python3 -c "print(round($WEEKLY_TOKENS / $WEEKLY_TOKEN_LIMIT * 100, 1))")

echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] Session: $SESSION_COUNT/$SESSION_LIMIT ($SESSION_PCT%) | Weekly: $WEEKLY_TOKENS/$WEEKLY_TOKEN_LIMIT ($WEEKLY_PCT%)" >> "$USAGE_DIR/usage.log"

# Session alerts
if [ "$(python3 -c "print($SESSION_PCT >= 95)")" = "True" ]; then
    send_alert "[CRITICAL] Claude Max Session at ${SESSION_PCT}%" \
        "Session usage: ${SESSION_COUNT}/${SESSION_LIMIT} requests (${SESSION_PCT}%) in last 5 hours. Switch to second Claude Max account NOW or pause agents. Server: 78.47.104.139" \
        "session_95"
elif [ "$(python3 -c "print($SESSION_PCT >= 75)")" = "True" ]; then
    send_alert "[WARNING] Claude Max Session at ${SESSION_PCT}%" \
        "Session usage: ${SESSION_COUNT}/${SESSION_LIMIT} requests (${SESSION_PCT}%) in last 5 hours. Consider pausing non-critical agents. Server: 78.47.104.139" \
        "session_75"
fi

# Weekly alerts
if [ "$(python3 -c "print($WEEKLY_PCT >= 95)")" = "True" ]; then
    send_alert "[CRITICAL] Claude Max Weekly at ${WEEKLY_PCT}%" \
        "Weekly token usage: $(python3 -c "print(f'{$WEEKLY_TOKENS:,}')") tokens (${WEEKLY_PCT}%). Switch to second Claude Max account. Server: 78.47.104.139" \
        "weekly_95"
elif [ "$(python3 -c "print($WEEKLY_PCT >= 75)")" = "True" ]; then
    send_alert "[WARNING] Claude Max Weekly at ${WEEKLY_PCT}%" \
        "Weekly token usage: $(python3 -c "print(f'{$WEEKLY_TOKENS:,}')") tokens (${WEEKLY_PCT}%). Consider switching accounts for heavy workloads. Server: 78.47.104.139" \
        "weekly_75"
fi

# Reset alerts when usage drops below 70%
python3 -c "
import json
s = json.load(open('$ALERT_STATE'))
c = False
if $SESSION_PCT < 70:
    if s.get('session_75'): s['session_75'] = False; c = True
    if s.get('session_95'): s['session_95'] = False; c = True
if $WEEKLY_PCT < 70:
    if s.get('weekly_75'): s['weekly_75'] = False; c = True
    if s.get('weekly_95'): s['weekly_95'] = False; c = True
if c: json.dump(s, open('$ALERT_STATE', 'w'), indent=2)
" 2>/dev/null

# Prune entries older than 8 days
python3 -c "
import json
eight_d = $(python3 -c "import time; print(int((time.time() - 8*86400) * 1000))")
lines = []
try:
    for line in open('$USAGE_LOG'):
        d = json.loads(line.strip())
        if d.get('ts', 0) >= eight_d: lines.append(line)
except: pass
with open('$USAGE_LOG', 'w') as f: f.writelines(lines)
" 2>/dev/null
