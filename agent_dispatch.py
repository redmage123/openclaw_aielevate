#!/usr/bin/env python3
"""AI Elevate Agent Dispatch — reliable agent execution with circuit breaker.

What: Provides the primary interface for dispatching OpenClaw agents. Every
      workflow, email handler, and build step calls dispatch() to run an agent.
      This module replaces the previous fire-and-forget subprocess.run() pattern.

Why:  Direct subprocess.run() calls were unreliable — agents produced 0-byte
      output, timed out silently, and had no visibility into why they failed.
      This module adds process polling, health checks, circuit breaking, and
      structured error reporting so failures are visible and retryable.

How:  dispatch() follows this sequence:
      1. Circuit breaker check (fail-fast if system is degraded)
      2. Gateway health check (verify OpenClaw is listening)
      3. Clear agent sessions (prevent resume)
      4. Launch subprocess with Popen (not run — allows polling)
      5. Poll every 5s for completion (detect stuck vs. thinking)
      6. Classify result (success / timeout / auth error / empty output)
      7. Update circuit breaker state
      8. Return structured DispatchResult

Usage:
    from agent_dispatch import dispatch, async_dispatch, is_healthy, circuit_status

    # Synchronous
    result = dispatch("gigforge-engineer", "Build the app", timeout=300)
    if result.status == "success":
        print(result.output)

    # Async (for Temporal activities)
    result = await async_dispatch("gigforge-sales", message, timeout=120)

    # Health check
    if not is_healthy():
        raise GatewayDownError()

CLI:
    python3 agent_dispatch.py --agent gigforge-sales --message "Say hello"
    python3 agent_dispatch.py --health
    python3 agent_dispatch.py --circuit-status
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple, Union

sys.path.insert(0, str(Path(__file__).parent))

from exceptions import (
    AgentTimeoutError,
    AgentAuthError,
    AgentEmptyOutputError,
    CircuitBreakerOpenError,
    GatewayDownError,
)
from logging_config import get_logger

log = get_logger("agent-dispatch")

# ============================================================================
# Constants
# ============================================================================

GATEWAY_PORT = 18789
AGENTS_BASE_DIR = Path("/home/aielevate/.openclaw/agents")
POLL_INTERVAL_SECONDS = 5
HEARTBEAT_INTERVAL_SECONDS = 30

AUTH_ERROR_KEYWORDS = frozenset([
    "authentication_error", "401", "invalid authentication",
    "rate_limit", "429", "overloaded",
])


# ============================================================================
# Circuit Breaker
# ============================================================================

@dataclass
class CircuitBreaker:
    """Thread-safe circuit breaker for agent dispatch.

    What: Tracks consecutive failures and blocks all dispatches when the
          failure threshold is reached. Automatically recovers after a
          cooldown period.

    Why:  Without a circuit breaker, a down LLM API causes every workflow
          to wait the full timeout (5+ minutes) before failing. The circuit
          breaker detects the pattern after 3 failures and fails-fast for
          all subsequent calls until the system recovers.

    How:  Three states:
          - CLOSED: healthy, all dispatches allowed
          - OPEN: broken, all dispatches blocked (fail-fast)
          - HALF_OPEN: testing, one dispatch allowed to probe recovery

          State transitions:
          - CLOSED → OPEN: after failure_threshold consecutive failures
          - OPEN → HALF_OPEN: after recovery_timeout seconds
          - HALF_OPEN → CLOSED: on success
          - HALF_OPEN → OPEN: on failure

    Attributes:
        status: Current state ('closed', 'open', 'half_open')
        failures: Count of consecutive failures
        failure_threshold: Failures needed to open the circuit
        recovery_timeout: Seconds before testing recovery
    """

    status: str = "closed"
    failures: int = 0
    last_failure: float = 0.0
    last_success: float = 0.0
    opened_at: float = 0.0
    failure_threshold: int = 3
    recovery_timeout: float = 120.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record_success(self) -> None:
        """Record a successful dispatch — resets failure count and closes circuit.

        What: Marks the system as healthy after a successful agent call.
        Why:  Resets the failure counter so the circuit stays closed.
        """
        with self._lock:
            self.failures = 0
            self.last_success = time.time()
            if self.status != "closed":
                log.info("Circuit breaker recovered", extra={
                    "previous_status": self.status,
                    "failures_before_recovery": self.failures,
                })
            self.status = "closed"

    def record_failure(self, reason: str = "") -> None:
        """Record a failed dispatch — increments failure count, may open circuit.

        What: Tracks a failure. Opens the circuit if threshold is reached.
        Why:  Cascading failures waste resources. Opening the circuit protects
              the system by failing fast.

        Args:
            reason: Human-readable reason for the failure (for logging)
        """
        with self._lock:
            self.failures += 1
            self.last_failure = time.time()
            if self.failures >= self.failure_threshold and self.status == "closed":
                self.status = "open"
                self.opened_at = time.time()
                log.critical(
                    "Circuit breaker OPENED",
                    extra={
                        "failures": self.failures,
                        "reason": reason,
                        "recovery_in_seconds": self.recovery_timeout,
                    },
                )

    def can_dispatch(self) -> bool:
        """Check if a dispatch is allowed in the current state.

        What: Returns True if the circuit allows a dispatch attempt.
        Why:  Called before every dispatch to fail-fast when the system is down.

        Returns:
            True if dispatch is allowed, False if circuit is open.
        """
        with self._lock:
            if self.status == "closed":
                return True
            if self.status == "open":
                if time.time() - self.opened_at > self.recovery_timeout:
                    self.status = "half_open"
                    log.info("Circuit breaker HALF_OPEN — testing recovery")
                    return True
                return False
            return True  # half_open — allow one probe request

    def to_dict(self) -> Dict[str, Any]:
        """Serialize circuit breaker state for dashboard/API consumption.

        Returns:
            Dict with status, failure count, and timestamps.
        """
        return {
            "status": self.status,
            "failures": self.failures,
            "last_failure": self.last_failure,
            "last_success": self.last_success,
        }


# Module-level singleton
_circuit = CircuitBreaker()


# ============================================================================
# Health Checks
# ============================================================================

def check_gateway() -> bool:
    """Verify the OpenClaw gateway is listening on its expected port.

    What: Checks if port 18789 has an active listener using ss -ltnp.

    Why:  If the gateway is down, all agent calls will fail. Detecting this
          early prevents wasting time on doomed subprocess launches.

    How:  Runs 'ss -ltnp' and checks if ':18789' appears in the output.
          Uses subprocess with a 5-second timeout to avoid hanging.

    Returns:
        True if the gateway is listening, False otherwise.
    """
    try:
        result = subprocess.run(
            ["ss", "-ltnp"],
            capture_output=True, text=True, timeout=5,
        )
        return f":{GATEWAY_PORT} " in result.stdout
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("Gateway check failed", extra={"error": str(e)})
        return False


def is_healthy() -> bool:
    """Full system health check — circuit breaker + gateway.

    What: Returns True only if both the circuit breaker allows dispatches
          AND the gateway is listening.

    Why:  Used by callers to decide whether to attempt a dispatch at all.
          Avoids the overhead of starting a subprocess when the system is down.

    Returns:
        True if the system is ready for agent dispatches.
    """
    if not _circuit.can_dispatch():
        return False
    if not check_gateway():
        log.warning("Health check failed: gateway not listening")
        return False
    return True


def circuit_status() -> Dict[str, Any]:
    """Get the current circuit breaker state.

    What: Returns a serialized dict of the circuit breaker state.
    Why:  Used by the ops dashboard /api/health endpoint.

    Returns:
        Dict with status, failures, and timestamps.
    """
    return _circuit.to_dict()


# ============================================================================
# Dispatch Result
# ============================================================================

@dataclass
class DispatchResult:
    """Structured result from an agent dispatch.

    What: Encapsulates the outcome of a dispatch() call — output text,
          exit code, timing, status classification, and error details.

    Why:  A plain string return doesn't carry enough context. Callers need
          to know whether the result is trustworthy (success), should be
          retried (timeout/auth), or is terminal (empty output).

    Attributes:
        output: The agent's text output (tool markers stripped)
        exit_code: Process exit code (0 = success, -1 = timeout/error)
        duration: Wall-clock seconds from launch to completion
        status: Classification — 'success', 'timeout', 'empty_output',
                'agent_error', 'circuit_open', 'gateway_down'
        error: Human-readable error description (empty on success)
    """

    output: str = ""
    exit_code: int = -1
    duration: float = 0.0
    status: str = "unknown"
    error: str = ""

    @property
    def is_success(self) -> bool:
        """Whether the dispatch succeeded."""
        return self.status == "success"

    @property
    def is_retryable(self) -> bool:
        """Whether the caller should retry this dispatch."""
        return self.status in ("timeout", "agent_error", "circuit_open", "gateway_down")


# ============================================================================
# Internal Helpers
# ============================================================================

def _clear_session(agent_id: str) -> None:
    """Clear an agent's session files to prevent session resume.

    What: Deletes all .jsonl files in the agent's sessions directory.

    Why:  OpenClaw agents resume previous sessions if session files exist.
          For workflow dispatches, we want a fresh session every time to
          avoid the agent continuing a previous conversation.

    Args:
        agent_id: The agent whose sessions to clear.
    """
    session_dir = AGENTS_BASE_DIR / agent_id / "sessions"
    if session_dir.exists():
        for f in session_dir.glob("*.jsonl"):
            f.unlink()


def _strip_tool_markers(text: str) -> str:
    """Remove OpenClaw tool call markers from agent output.

    What: Strips *[Tool: args]* markers and timing annotations
          that appear in raw agent stdout.

    Why:  Tool markers are internal metadata — callers want the agent's
          actual response text, not the debugging artifacts.

    Args:
        text: Raw agent stdout

    Returns:
        Cleaned text with only the agent's response content.
    """
    text = re.sub(r'\*\[.*?\]\*', '', text, flags=re.DOTALL).strip()
    text = re.sub(r'\(\d+s\)\s*·\s*\d+\s*tools?', '', text).strip()
    lines = [line for line in text.split('\n') if line.strip() and line.strip() != '---']
    return '\n'.join(lines).strip()


def _detect_auth_error(output: str) -> bool:
    """Check if agent output indicates an LLM API authentication failure.

    What: Scans the output for known auth error patterns.

    Why:  Auth errors are transient (key rotation, rate limits) and should
          trigger the circuit breaker. We only flag short outputs (<300 chars)
          to avoid false positives on long responses that mention "401" in context.

    Args:
        output: The agent's text output.

    Returns:
        True if the output looks like an auth/API error.
    """
    if len(output) >= 300:
        return False
    output_lower = output.lower()
    return any(keyword in output_lower for keyword in AUTH_ERROR_KEYWORDS)


def _diagnose_empty_output(agent_id: str, retcode: int, duration: float, raw_len: int) -> str:
    """Generate diagnostic info when an agent produces no output.

    What: Collects gateway status, PID, and uptime to help debug why
          the agent returned nothing.

    Why:  Empty output is the most common failure mode. Without diagnostics,
          we'd have no idea whether the gateway was down, the agent crashed,
          or the LLM just returned nothing.

    Args:
        agent_id: The agent that produced empty output
        retcode: Process exit code
        duration: How long the dispatch took
        raw_len: Length of raw stdout before stripping

    Returns:
        Diagnostic string for logging and error reporting.
    """
    gateway_up = check_gateway()
    gateway_pid = ""
    gateway_uptime = ""

    if gateway_up:
        try:
            ss_result = subprocess.run(
                ["ss", "-ltnp"], capture_output=True, text=True, timeout=5,
            )
            for line in ss_result.stdout.split("\n"):
                if f":{GATEWAY_PORT} " in line and "pid=" in line:
                    gateway_pid = line.split("pid=")[-1].split(",")[0]
                    break
            if gateway_pid:
                ps_result = subprocess.run(
                    ["ps", "-o", "etimes=", "-p", gateway_pid],
                    capture_output=True, text=True, timeout=5,
                )
                gateway_uptime = ps_result.stdout.strip()
        except (subprocess.TimeoutExpired, OSError):
            pass

    return (
        f"Agent {agent_id} empty output. "
        f"Gateway: {'up' if gateway_up else 'DOWN'}, "
        f"pid={gateway_pid}, uptime={gateway_uptime}s. "
        f"exit_code={retcode}, duration={duration:.0f}s, raw_len={raw_len}"
    )


# ============================================================================
# Public API
# ============================================================================

def _check_preconditions(agent_id: str) -> Optional["DispatchResult"]:
    """Check circuit breaker and gateway health. Returns a failure DispatchResult or None."""
    if not _circuit.can_dispatch():
        elapsed_since_open = time.time() - _circuit.opened_at
        remaining = _circuit.recovery_timeout - elapsed_since_open
        log.warning(
            "Dispatch blocked by circuit breaker",
            extra={"agent_id": agent_id, "failures": _circuit.failures},
        )
        return DispatchResult(
            status="circuit_open",
            error=(
                f"Circuit breaker open ({_circuit.failures} failures). "
                f"Recovery in {remaining:.0f}s."
            ),
            duration=0,
        )

    if not check_gateway():
        _circuit.record_failure("gateway_down")
        log.error("Gateway down — dispatch blocked", extra={"agent_id": agent_id})
        return DispatchResult(
            status="gateway_down",
            error=f"OpenClaw gateway not listening on port {GATEWAY_PORT}",
            duration=0,
        )
    return None


def _launch_subprocess(agent_id: str, message: str, timeout: int, start: float) -> "tuple[subprocess.Popen, None] | tuple[None, DispatchResult]":
    """Launch the agent subprocess. Returns (proc, None) on success or (None, DispatchResult) on failure."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    try:
        proc = subprocess.Popen(
            ["openclaw", "agent", "--agent", agent_id, "--message", message,
             "--thinking", "low", "--timeout", str(timeout)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        return proc, None
    except OSError as e:
        _circuit.record_failure(f"popen_failed: {e}")
        log.error("Failed to start agent process", extra={"agent_id": agent_id, "error": str(e)})
        return None, DispatchResult(
            status="agent_error",
            error=f"Failed to start agent process: {e}",
            duration=time.time() - start,
        )


def _poll_process(
    proc: "subprocess.Popen",
    agent_id: str,
    timeout: int,
    heartbeat_fn: Optional[Callable[[str], None]],
    start: float,
) -> "tuple[str, int] | DispatchResult":
    """Poll subprocess until done or timeout. Returns (stdout, retcode) or a timeout DispatchResult."""
    last_heartbeat = time.time()
    while True:
        elapsed = time.time() - start
        retcode = proc.poll()

        if retcode is not None:
            stdout = proc.stdout.read().decode("utf-8", errors="replace") if proc.stdout else ""
            return stdout, retcode

        if elapsed > timeout + 30:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            _circuit.record_failure("timeout")
            log.warning("Agent timed out", extra={"agent_id": agent_id, "timeout": timeout, "elapsed": elapsed})
            return DispatchResult(status="timeout", error=f"Agent {agent_id} timed out after {elapsed:.0f}s",
                                  duration=elapsed, exit_code=-1)

        if heartbeat_fn and time.time() - last_heartbeat > HEARTBEAT_INTERVAL_SECONDS:
            try:
                heartbeat_fn(f"Agent {agent_id} running ({elapsed:.0f}s)")
            except Exception:
                pass
            last_heartbeat = time.time()

        time.sleep(POLL_INTERVAL_SECONDS)


def _classify_output(output: str, stdout: str, retcode: int, agent_id: str, duration: float) -> "DispatchResult":
    """Classify agent output and return the appropriate DispatchResult."""
    if _detect_auth_error(output):
        _circuit.record_failure(f"auth_error: {output[:100]}")
        log.warning("Agent auth error", extra={"agent_id": agent_id, "output_preview": output[:200]})
        return DispatchResult(output=output, exit_code=retcode, duration=duration,
                              status="agent_error", error=f"Auth/API error: {output[:200]}")

    if not output or len(output) < 10:
        diag = _diagnose_empty_output(agent_id, retcode, duration, len(stdout))
        log.error(diag, extra={"agent_id": agent_id})
        _circuit.record_failure("empty_output")
        return DispatchResult(output=output, exit_code=retcode, duration=duration,
                              status="empty_output", error=diag)

    if retcode != 0:
        _circuit.record_failure(f"exit_code_{retcode}")
        log.warning("Agent non-zero exit", extra={"agent_id": agent_id, "exit_code": retcode})
        return DispatchResult(output=output, exit_code=retcode, duration=duration,
                              status="agent_error", error=f"Agent {agent_id} exited with code {retcode}")

    _circuit.record_success()
    log.info("Agent dispatch succeeded", extra={
        "agent_id": agent_id, "output_chars": len(output), "duration_seconds": round(duration, 1),
    })
    return DispatchResult(output=output, exit_code=retcode, duration=duration, status="success")


def dispatch(
    agent_id: str,
    message: str,
    timeout: int = 300,
    heartbeat_fn: Optional[Callable[[str], None]] = None,
) -> DispatchResult:
    """Dispatch an OpenClaw agent with full monitoring and circuit breaking.

    This is the ONLY way agents should be dispatched — direct subprocess.run() calls
    bypass health checks, circuit breaking, output validation, and diagnostics.

    Args:
        agent_id: The agent to dispatch (e.g., 'gigforge-engineer')
        message: The instruction/prompt to send to the agent
        timeout: Maximum seconds to wait for the agent to respond
        heartbeat_fn: Optional callback for Temporal activity keep-alive

    Returns:
        DispatchResult with output, status, timing, and error details.
        All errors are captured — callers should check result.status or result.is_success.
    """
    start = time.time()

    early = _check_preconditions(agent_id)
    if early:
        return early

    _clear_session(agent_id)

    proc, launch_err = _launch_subprocess(agent_id, message, timeout, start)
    if launch_err:
        return launch_err

    poll_result = _poll_process(proc, agent_id, timeout, heartbeat_fn, start)
    if isinstance(poll_result, DispatchResult):
        return poll_result

    stdout, retcode = poll_result
    duration = time.time() - start
    output = _strip_tool_markers(stdout)
    return _classify_output(output, stdout, retcode, agent_id, duration)


async def async_dispatch(
    agent_id: str,
    message: str,
    timeout: int = 300,
    heartbeat_fn: Optional[Callable[[str], None]] = None,
) -> DispatchResult:
    """Async wrapper for dispatch — runs in a thread pool executor.

    What: Allows dispatch() to be called from async code (Temporal activities,
          FastAPI endpoints) without blocking the event loop.

    Why:  dispatch() is synchronous (subprocess + polling). Temporal activities
          run in an async context. This wrapper bridges the gap.

    Args:
        Same as dispatch().

    Returns:
        Same as dispatch().
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: dispatch(agent_id, message, timeout, heartbeat_fn)
    )


# ============================================================================
# CLI Interface
# ============================================================================

def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns:
        Configured ArgumentParser with all dispatch options.
    """
    parser = argparse.ArgumentParser(
        description="Agent Dispatch — reliable agent execution with circuit breaker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --agent gigforge-sales --message "Say hello"
  %(prog)s --agent gigforge-engineer --message "Build the app" --timeout 600
  %(prog)s --health
  %(prog)s --circuit-status

Status codes:
  success       Agent responded normally
  timeout       Agent exceeded timeout limit
  empty_output  Agent produced no meaningful output
  agent_error   Agent crashed or returned auth error
  circuit_open  Circuit breaker is blocking dispatches
  gateway_down  OpenClaw gateway is not running
        """,
    )
    parser.add_argument("--agent", help="Agent ID to dispatch (e.g., gigforge-sales)")
    parser.add_argument("--message", help="Message/instruction to send to the agent")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds (default: 300)")
    parser.add_argument("--health", action="store_true", help="Check system health and exit")
    parser.add_argument("--circuit-status", action="store_true", help="Show circuit breaker state")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    return parser


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()

    if args.health:
        healthy = is_healthy()
        gateway = check_gateway()
        circuit = circuit_status()
        if args.json:
            print(json.dumps({"healthy": healthy, "gateway": gateway, "circuit": circuit}))
        else:
            print(f"Healthy: {healthy}")
            print(f"Gateway: {'up' if gateway else 'DOWN'}")
            print(f"Circuit: {circuit['status']} (failures: {circuit['failures']})")
        sys.exit(0 if healthy else 1)

    if args.circuit_status:
        status = circuit_status()
        if args.json:
            print(json.dumps(status))
        else:
            for key, value in status.items():
                print(f"  {key}: {value}")
        sys.exit(0)

    if not args.agent or not args.message:
        parser.print_help()
        sys.exit(1)

    result = dispatch(args.agent, args.message, args.timeout)
    if args.json:
        print(json.dumps({
            "status": result.status,
            "output": result.output[:1000],
            "exit_code": result.exit_code,
            "duration": round(result.duration, 1),
            "error": result.error,
        }))
    else:
        if result.is_success:
            print(result.output)
        else:
            print(f"ERROR [{result.status}]: {result.error}", file=sys.stderr)
    sys.exit(0 if result.is_success else 1)
