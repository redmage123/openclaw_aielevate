#!/usr/bin/env python3
"""Legacy alert script — wraps notify.py for backward compatibility."""
import sys
sys.path.insert(0, "/home/aielevate")
from notify import send

if __name__ == "__main__":
    title = sys.argv[1] if len(sys.argv) > 1 else "Alert"
    body = sys.argv[2] if len(sys.argv) > 2 else "No details"
    # Legacy alerts default to HIGH priority
    result = send(title, body, priority="high")
    channels = result.get("channels", {})
    parts = []
    for ch, status in channels.items():
        parts.append(f"{ch}: {'sent' if status is True or (isinstance(status, int) and status > 0) else status}")
    print(", ".join(parts) if parts else "sent")
