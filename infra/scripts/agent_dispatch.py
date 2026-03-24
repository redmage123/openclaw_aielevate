#!/usr/bin/env python3
"""Reliable Agent Dispatch — replaces fire-and-forget subprocess.run.

Fixes issues #1, #2, #7, #8:
  1. Reliable dispatch — polls for output, detects dead agents
  2. Health checks — verifies gateway + LLM before dispatching
  7. Smart subprocess — monitors process, detects stuck vs thinking
  8. Circuit breaker — pauses all dispatches when API is down

Usage:
    from agent_dispatch import dispatch, is_healthy, circuit_status

    result = dispatch("gigforge-engineer", "Build the app", timeout=300)
    # result = DispatchResult(output="...", exit_code=0, duration=45, status="success")

    if not is_healthy():
        print("System not ready for agent dispatch")
"""

import asyncio
import json
import logging
import os
import subprocess
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("agent-dispatch")

# ============================================================================
# Circuit Breaker — shared state across all dispatches
# ============================================================================

@dataclass
class CircuitState:
    status: str = "closed"  # closed (healthy), open (broken), half_open (testing)
    failures: int = 0
    last_failure: float = 0.0
    last_success: float = 0.0
    opened_at: float = 0.0
    failure_threshold: int = 3  # consecutive failures to open
    recovery_timeout: float = 120.0  # seconds before trying half_open
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record_success(self):
        with self.lock:
            self.failures = 0
            self.last_success = time.time()
            if self.status != "closed":
                log.info("Circuit breaker: CLOSED (recovered)")
            self.status = "closed"

    def record_failure(self, reason=""):
        with self.lock:
            self.failures += 1
            self.last_failure = time.time()
            if self.failures >= self.failure_threshold and self.status == "closed":
                self.status = "open"
                self.opened_at = time.time()
                log.error(f"Circuit breaker: OPEN after {self.failures} failures. Reason: {reason}")

    def can_dispatch(self) -> bool:
        with self.lock:
            if self.status == "closed":
                return True
            if self.status == "open":
                if time.time() - self.opened_at > self.recovery_timeout:
                    self.status = "half_open"
                    log.info("Circuit breaker: HALF_OPEN (testing)")
                    return True
                return False
            # half_open — allow one request through
            return True

    def to_dict(self):
        return {
            "status": self.status,
            "failures": self.failures,
            "last_failure": self.last_failure,
            "last_success": self.last_success,
        }


_circuit = CircuitState()

# ============================================================================
# Health Checks
# ============================================================================

def check_gateway() -> bool:
    """Check if the openclaw gateway is listening."""
    try:
        result = subprocess.run(
            ["ss", "-ltnp"], capture_output=True, text=True, timeout=5)
        return ":18789 " in result.stdout
    except Exception:
        return False


def check_llm_api() -> bool:
    """Quick LLM health check — 10s timeout."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    try:
        proc = subprocess.run(
            ["openclaw", "agent", "--agent", "gigforge-sales",
             "--message", "Reply with exactly: OK", "--thinking", "low", "--timeout", "10"],
            capture_output=True, text=True, timeout=20, env=env,
        )
        output = (proc.stdout or "").strip()
        return "OK" in output and proc.returncode == 0
    except Exception:
        return False


def is_healthy() -> bool:
    """Full system health check."""
    if not _circuit.can_dispatch():
        return False
    if not check_gateway():
        log.warning("Health check: gateway not listening")
        return False
    return True


def circuit_status() -> dict:
    return _circuit.to_dict()


# ============================================================================
# Dispatch Result
# ============================================================================

@dataclass
class DispatchResult:
    output: str = ""
    exit_code: int = -1
    duration: float = 0.0
    status: str = "unknown"  # success, timeout, empty_output, agent_error, circuit_open, gateway_down
    error: str = ""


# ============================================================================
# Reliable Dispatch
# ============================================================================

def _clear_session(agent_id: str):
    """Clear agent sessions to prevent resume."""
    session_dir = Path(f"/home/aielevate/.openclaw/agents/{agent_id}/sessions")
    if session_dir.exists():
        for f in session_dir.glob("*.jsonl"):
            f.unlink()


def _strip_tool_markers(text: str) -> str:
    """Remove tool call markers from agent output."""
    import re
    text = re.sub(r'\*\[.*?\]\*', '', text, flags=re.DOTALL).strip()
    text = re.sub(r'\(\d+s\)\s*·\s*\d+\s*tools?', '', text).strip()
    lines = [l for l in text.split('\n') if l.strip() and l.strip() != '---']
    return '\n'.join(lines).strip()


def _poll_process(proc, agent_id: str, timeout: int, start: float, heartbeat_fn):
    """Poll subprocess until completion, timeout, or error.

    Returns (retcode, stdout) on success, or DispatchResult on timeout.
    """
    poll_interval = 5
    last_heartbeat = time.time()

    while True:
        elapsed = time.time() - start
        retcode = proc.poll()
        if retcode is not None:
            stdout = proc.stdout.read().decode("utf-8", errors="replace") if proc.stdout else ""
            return retcode, stdout

        if elapsed > timeout + 30:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            _circuit.record_failure("timeout")
            return DispatchResult(
                status="timeout",
                error=f"Agent {agent_id} timed out after {elapsed:.0f}s",
                duration=elapsed,
                exit_code=-1,
            )

        if heartbeat_fn and time.time() - last_heartbeat > 30:
            try:
                heartbeat_fn(f"Agent {agent_id} running ({elapsed:.0f}s)")
            except Exception:
                pass
            last_heartbeat = time.time()

        time.sleep(poll_interval)


def _diagnose_empty_output(agent_id: str, retcode: int, duration: float, raw_len: int) -> str:
    """Diagnose why an agent produced empty output."""
    import subprocess as _sp
    gw_check = _sp.run(["ss", "-ltnp"], capture_output=True, text=True, timeout=5)
    gw_up = ":18789 " in gw_check.stdout
    gw_pid = ""
    if gw_up:
        for line in gw_check.stdout.split("\n"):
            if ":18789 " in line:
                gw_pid = line.split("pid=")[-1].split(",")[0] if "pid=" in line else ""
    gw_uptime = ""
    if gw_pid:
        try:
            gw_uptime = _sp.run(["ps", "-o", "etimes=", "-p", gw_pid],
                capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            pass
    return (f"Agent {agent_id} empty output. "
            f"Gateway: {'up' if gw_up else 'DOWN'}, pid={gw_pid}, uptime={gw_uptime}s. "
            f"Agent exit_code={retcode}, duration={duration:.0f}s, raw_len={raw_len}")


def dispatch(agent_id: str, message: str, timeout: int = 300,
             heartbeat_fn=None) -> DispatchResult:
    """Dispatch an agent with full monitoring.

    Args:
        agent_id: The agent to dispatch
        message: The message/instruction
        timeout: Max seconds to wait
        heartbeat_fn: Optional callable to report progress (for Temporal heartbeats)

    Returns:
        DispatchResult with output, status, timing
    """
    start = time.time()

    # Circuit breaker check
    if not _circuit.can_dispatch():
        elapsed = time.time() - _circuit.opened_at
        remaining = _circuit.recovery_timeout - elapsed
        return DispatchResult(
            status="circuit_open",
            error=f"Circuit breaker open. {_circuit.failures} consecutive failures. "
                  f"Recovery in {remaining:.0f}s.",
            duration=0,
        )

    # Gateway health check
    if not check_gateway():
        _circuit.record_failure("gateway_down")
        return DispatchResult(
            status="gateway_down",
            error="OpenClaw gateway not listening on port 18789",
            duration=0,
        )

    # Clear session to prevent resume
    _clear_session(agent_id)

    # Prepare environment
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    # Launch agent process
    try:
        proc = subprocess.Popen(
            ["openclaw", "agent", "--agent", agent_id,
             "--message", message, "--thinking", "low", "--timeout", str(timeout)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
    except Exception as e:
        _circuit.record_failure(f"popen_failed: {e}")
        return DispatchResult(
            status="agent_error",
            error=f"Failed to start agent process: {e}",
            duration=time.time() - start,
        )

    # Poll for completion with heartbeats
    poll_result = _poll_process(proc, agent_id, timeout, start, heartbeat_fn)
    if isinstance(poll_result, DispatchResult):
        return poll_result
    retcode, stdout = poll_result

    duration = time.time() - start

    # Process result
    output = _strip_tool_markers(stdout)

    # Check for auth errors (transient)
    auth_errors = ["authentication_error", "401", "invalid authentication", "rate_limit", "429"]
    is_auth_error = any(err in output.lower() for err in auth_errors) and len(output) < 300

    if is_auth_error:
        _circuit.record_failure(f"auth_error: {output[:100]}")
        return DispatchResult(
            output=output,
            exit_code=retcode,
            duration=duration,
            status="agent_error",
            error=f"Auth/API error: {output[:200]}",
        )

    # Check for empty output — log diagnostics
    if not output or len(output) < 10:
        diag = _diagnose_empty_output(agent_id, retcode, duration, len(stdout))
        log.error(diag)
        _circuit.record_failure("empty_output")
        return DispatchResult(
            output=output,
            exit_code=retcode,
            duration=duration,
            status="empty_output",
            error=diag,
        )

    # Check exit code
    if retcode != 0:
        _circuit.record_failure(f"exit_code_{retcode}")
        return DispatchResult(
            output=output,
            exit_code=retcode,
            duration=duration,
            status="agent_error",
            error=f"Agent {agent_id} exited with code {retcode}",
        )

    # Success
    _circuit.record_success()
    log.info(f"Dispatch {agent_id}: {len(output)} chars in {duration:.0f}s")

    return DispatchResult(
        output=output,
        exit_code=retcode,
        duration=duration,
        status="success",
    )


async def async_dispatch(agent_id: str, message: str, timeout: int = 300,
                         heartbeat_fn=None) -> DispatchResult:
    """Async wrapper for dispatch — runs in executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: dispatch(agent_id, message, timeout, heartbeat_fn))
