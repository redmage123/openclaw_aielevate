#!/usr/bin/env python3
"""Agent Queue v2 — concurrent agent calls with rate limit protection.

Allows up to MAX_CONCURRENT agent calls simultaneously.
Still does exponential backoff on rate limits.
File-based semaphore instead of exclusive lock.

Usage (same CLI as v1):
  agent-queue --agent X --message Y [--timeout N] [--thinking LEVEL]

Library:
  from agent_queue import queue_agent_call
  result = queue_agent_call(agent_id, message, timeout=300)
"""

import argparse
import fcntl
import json
import logging
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from logging_config import get_logger  # Structured JSON logging
from exceptions import AiElevateError  # TODO: Use specific exception types, AgentError

MAX_CONCURRENT = 4       # Max simultaneous agent calls
MAX_RETRIES = 5
BASE_BACKOFF = 2
MAX_BACKOFF = 120
LOCK_DIR = Path("/tmp/agent-queue-locks")
LOG_FILE = Path("/var/log/openclaw/shared/agent-queue.log")

# logging.basicConfig removed — using get_logger() from logging_config
logger = logging.getLogger("agent-queue")


def _log(msg):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} -- {msg}\n")


def _is_rate_limited(output: str) -> bool:
    markers = ["rate limit", "429", "too many requests", "throttl", "rate_limit"]
    lower = output.lower()
    return any(m in lower for m in markers)


def _backoff_delay(attempt: int) -> float:
    slot = random.randint(0, 2 ** attempt)
    jitter = random.uniform(0, 1)
    return min(BASE_BACKOFF * slot + jitter, MAX_BACKOFF)


def _count_active() -> int:
    """Count how many agent calls are currently running."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for lock_file in LOCK_DIR.glob("slot-*.lock"):
        try:
            fd = open(lock_file, "r")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # If we got the lock, it's free
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
        except (IOError, OSError):
            # Lock is held — slot is active
            count += 1
    return count


def _acquire_slot() -> tuple:
    """Acquire a concurrency slot. Returns (fd, slot_num) or (None, -1) if all slots busy."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(MAX_CONCURRENT):
        lock_path = LOCK_DIR / f"slot-{i}.lock"
        try:
            fd = open(lock_path, "w")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd, i
        except (IOError, OSError):
            continue
    return None, -1


def queue_agent_call(agent_id: str, message: str, timeout: int = 300, thinking: str = "low") -> dict:
    """Run an agent call with concurrency limiting and rate limit retry."""

    _log(f"QUEUED: {agent_id}")

    # Wait for a slot
    attempt = 0
    while True:
        fd, slot = _acquire_slot()
        if fd is not None:
            break
        # All slots busy — wait briefly and retry
        wait = 3 + random.uniform(0, 2)
        time.sleep(wait)

    try:
        for attempt in range(MAX_RETRIES + 1):
            _log(f"RUNNING: {agent_id} (attempt {attempt + 1}/{MAX_RETRIES + 1}, slot {slot})")

            start = time.time()
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

            try:
                result = subprocess.run(
                    ["openclaw", "agent", "--agent", agent_id,
                     "--message", message,
                     "--thinking", thinking,
                     "--timeout", str(timeout)],
                    capture_output=True, text=True, timeout=timeout + 30, env=env,
                )
            except subprocess.TimeoutExpired:
                _log(f"TIMEOUT: {agent_id} after {timeout}s")
                return {"stdout": "", "stderr": "timeout", "returncode": -1, "duration": timeout}

            duration = int(time.time() - start)
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            combined = stdout + stderr

            if _is_rate_limited(combined):
                if attempt < MAX_RETRIES:
                    delay = _backoff_delay(attempt)
                    _log(f"RATE LIMITED: {agent_id} — backing off {delay:.1f}s (attempt {attempt + 1})")
                    # Release slot while backing off so others can use it
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    fd.close()
                    time.sleep(delay)
                    # Reacquire slot
                    while True:
                        fd, slot = _acquire_slot()
                        if fd is not None:
                            break
                        time.sleep(3)
                    continue
                else:
                    _log(f"GAVE UP: {agent_id} after {MAX_RETRIES + 1} attempts")
                    return {"stdout": "", "stderr": "rate limited", "returncode": -1, "duration": 0}

            _log(f"DONE: {agent_id} exit={result.returncode} duration={duration}s chars={len(stdout)}")
            return {
                "stdout": stdout,
                "stderr": stderr,
                "returncode": result.returncode,
                "duration": duration,
            }

    finally:
        # Release slot
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
        except (AgentError, Exception) as e:
            pass

    return {"stdout": "", "stderr": "unexpected", "returncode": -1, "duration": 0}


# Async version for email gateway
async def async_queue_agent_call(agent_id, message, timeout=300, thinking="low"):
    """Async version — runs the sync version in an executor."""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: queue_agent_call(agent_id, message, timeout, thinking)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent Queue v2 — concurrent with rate limit protection")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--thinking", default="low")
    args = parser.parse_args()

    result = queue_agent_call(args.agent, args.message, args.timeout, args.thinking)
    if result["stdout"]:
        print(result["stdout"])
    sys.exit(result["returncode"] if result["returncode"] >= 0 else 1)
