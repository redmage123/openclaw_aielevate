#!/usr/bin/env python3
"""AI Elevate Exception Hierarchy — custom exceptions for all services.

What: Provides a structured exception hierarchy that replaces bare Exception
      catches throughout the codebase. Every exception carries context about
      what went wrong, which service raised it, and what the caller should do.

Why:  Bare exceptions (try/except Exception) swallow context and make debugging
      impossible. Custom exceptions enable:
      - Granular error handling (catch AgentTimeoutError vs AgentAuthError)
      - Structured logging (each exception knows its severity and category)
      - Auditor/watchdog integration (exceptions can trigger alerts)
      - Retryable vs non-retryable classification for Temporal workflows

How:  All exceptions inherit from AiElevateError which provides:
      - service_name: which service raised the error
      - severity: 'critical', 'error', 'warning'
      - retryable: whether Temporal should retry
      - context: dict of additional debugging info
      - to_log_dict(): structured dict for JSON logging
      - to_alert(): formatted string for ops notifications

Usage:
    from exceptions import AgentDispatchError, DatabaseError, EmailDeliveryError

    try:
        result = dispatch("gigforge-sales", message)
    except AgentDispatchError as e:
        logger.error(e.to_log_dict())
        if e.retryable:
            raise  # Let Temporal retry
        ops_alert(e.to_alert())
"""

import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class AiElevateError(Exception):
    """Base exception for all AI Elevate services.

    What: Root of the exception hierarchy. All custom exceptions inherit from this.

    Why:  Enables catching all AI Elevate errors with a single except clause
          while still allowing granular handling of specific error types.

    How:  Stores structured context (service, severity, retryable flag, extra data)
          alongside the human-readable message. Provides serialization methods
          for logging and alerting systems.

    Attributes:
        service_name: The service that raised the error (e.g., 'build-workflow')
        severity: One of 'critical', 'error', 'warning'
        retryable: Whether the operation should be retried
        context: Additional key-value pairs for debugging
        timestamp: When the error occurred (UTC)
    """

    def __init__(
        self,
        message: str,
        service_name: str = "unknown",
        severity: str = "error",
        retryable: bool = False,
        context: Optional[Dict[str, Any]] = None,
        original_error: Optional[Exception] = None,
    ):
        """Initialize with structured error context.

        Args:
            message: Human-readable error description
            service_name: Which service raised this (e.g., 'email-gateway')
            severity: 'critical' | 'error' | 'warning'
            retryable: Should Temporal/caller retry this operation?
            context: Additional debugging info as key-value pairs
            original_error: The underlying exception that caused this, if any
        """
        super().__init__(message)
        self.service_name = service_name
        self.severity = severity
        self.retryable = retryable
        self.context = context or {}
        self.original_error = original_error
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_log_dict(self) -> Dict[str, Any]:
        """Serialize to a structured dict for JSON logging.

        What: Converts the exception to a flat dictionary suitable for
              structured logging (JSON log lines, ELK, Datadog, etc.).

        Returns:
            Dict with all error context, stack trace, and metadata.
        """
        result = {
            "error_type": type(self).__name__,
            "message": str(self),
            "service": self.service_name,
            "severity": self.severity,
            "retryable": self.retryable,
            "timestamp": self.timestamp,
            **self.context,
        }
        if self.original_error:
            result["original_error"] = str(self.original_error)
            result["original_type"] = type(self.original_error).__name__
            result["traceback"] = traceback.format_exception(
                type(self.original_error), self.original_error,
                self.original_error.__traceback__
            )
        return result

    def to_alert(self) -> str:
        """Format as an ops alert string.

        What: Creates a concise, actionable alert message for ops notifications
              (ntfy, email, Telegram, dashboard alerts panel).

        Returns:
            Formatted alert string with severity emoji, service, and message.
        """
        emoji = {"critical": "🔴", "error": "🟠", "warning": "🟡"}.get(self.severity, "⚪")
        return f"{emoji} [{self.severity.upper()}] {self.service_name}: {self}"


# ============================================================================
# Agent Errors
# ============================================================================

class AgentError(AiElevateError):
    """Base exception for agent dispatch and execution errors.

    What: Parent class for all errors related to dispatching or running
          OpenClaw agents (LLM calls, subprocess management, output parsing).

    Why:  Agent errors are the most common failure mode. Separating them
          allows the circuit breaker and retry logic to handle them specially.
    """
    def __init__(self, message: str, agent_id: str = "", **kwargs):
        kwargs.setdefault("service_name", "agent-dispatch")
        super().__init__(message, **kwargs)
        self.agent_id = agent_id
        self.context["agent_id"] = agent_id


class AgentTimeoutError(AgentError):
    """Agent did not respond within the configured timeout.

    What: The openclaw agent subprocess exceeded its timeout limit.

    Why:  Timeouts are usually transient (LLM API slow, high load) and
          should be retried with backoff.

    How:  Raised by agent_dispatch.dispatch() when subprocess.Popen
          exceeds the poll timeout. Always retryable.
    """
    def __init__(self, agent_id: str, timeout: int, **kwargs):
        super().__init__(
            f"Agent {agent_id} timed out after {timeout}s",
            agent_id=agent_id, retryable=True, **kwargs
        )
        self.context["timeout_seconds"] = timeout


class AgentAuthError(AgentError):
    """LLM API authentication failed (401, invalid key, rate limit).

    What: The underlying LLM API rejected the request due to auth issues.

    Why:  Auth errors are transient (key rotation, rate limits) and should
          be retried after a cooldown. The circuit breaker tracks these.

    How:  Detected by agent_dispatch.dispatch() when the agent output
          contains 'authentication_error', '401', or 'rate_limit'.
    """
    def __init__(self, agent_id: str, detail: str = "", **kwargs):
        super().__init__(
            f"Agent {agent_id} auth error: {detail[:200]}",
            agent_id=agent_id, retryable=True, severity="warning", **kwargs
        )


class AgentEmptyOutputError(AgentError):
    """Agent produced no meaningful output.

    What: The agent subprocess completed but stdout was empty or < 10 chars.

    Why:  Empty output usually means the agent session resumed incorrectly
          or the gateway is in a degraded state. May be retryable.

    How:  Detected by agent_dispatch.dispatch() after output stripping.
    """
    def __init__(self, agent_id: str, output_length: int = 0, **kwargs):
        super().__init__(
            f"Agent {agent_id} produced empty output ({output_length} chars)",
            agent_id=agent_id, retryable=True, **kwargs
        )
        self.context["output_length"] = output_length


class CircuitBreakerOpenError(AgentError):
    """Circuit breaker is open — all agent dispatches are blocked.

    What: Too many consecutive failures triggered the circuit breaker.
          No agent calls will be attempted until the recovery timeout.

    Why:  Prevents cascading failures when the LLM API or gateway is down.
          Fail-fast is better than waiting 5 minutes per call.

    How:  Raised by agent_dispatch.dispatch() before even starting the
          subprocess. Not retryable until the circuit half-opens.
    """
    def __init__(self, failures: int, recovery_in: float, **kwargs):
        super().__init__(
            f"Circuit breaker open ({failures} failures, recovery in {recovery_in:.0f}s)",
            retryable=False, severity="critical", **kwargs
        )
        self.context["consecutive_failures"] = failures
        self.context["recovery_seconds"] = recovery_in


class GatewayDownError(AgentError):
    """OpenClaw gateway is not listening on its expected port.

    What: The health check for the OpenClaw gateway (port 18789) failed.

    Why:  If the gateway is down, all agent calls will fail. This error
          triggers the gateway watchdog to attempt a restart.

    How:  Detected by agent_dispatch.check_gateway() via ss -ltnp.
    """
    def __init__(self, **kwargs):
        super().__init__(
            "OpenClaw gateway not listening on port 18789",
            retryable=False, severity="critical", **kwargs
        )


# ============================================================================
# Database Errors
# ============================================================================

class DatabaseError(AiElevateError):
    """Base exception for all database operations.

    What: Parent class for PostgreSQL, SQLite, and knowledge graph errors.

    Why:  Database errors need different handling than agent errors —
          connection failures are retryable, constraint violations are not.
    """
    def __init__(self, message: str, table: str = "", operation: str = "", **kwargs):
        kwargs.setdefault("service_name", "database")
        super().__init__(message, **kwargs)
        self.context["table"] = table
        self.context["operation"] = operation


class DatabaseConnectionError(DatabaseError):
    """Cannot connect to the database.

    What: psycopg2.connect() or equivalent failed.
    Why:  Usually transient (DB restarting, connection pool exhausted). Retry.
    """
    def __init__(self, db_name: str = "rag", **kwargs):
        super().__init__(
            f"Cannot connect to database '{db_name}'",
            retryable=True, severity="critical", **kwargs
        )


class RecordNotFoundError(DatabaseError):
    """Expected record not found in the database.

    What: A query that should return a result returned nothing.
    Why:  Not retryable — the data doesn't exist. Caller should handle gracefully.
    """
    def __init__(self, table: str, key: str, **kwargs):
        super().__init__(
            f"Record not found: {table}/{key}",
            table=table, retryable=False, severity="warning", **kwargs
        )
        self.context["key"] = key


# ============================================================================
# Email Errors
# ============================================================================

class EmailError(AiElevateError):
    """Base exception for email sending and processing errors."""
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault("service_name", "email")
        super().__init__(message, **kwargs)


class EmailDeliveryError(EmailError):
    """Email could not be delivered via Mailgun.

    What: The Mailgun API returned a non-200 status or the request timed out.
    Why:  Usually transient. Retry with backoff.
    """
    def __init__(self, recipient: str, status_code: int = 0, **kwargs):
        super().__init__(
            f"Email delivery failed to {recipient} (status {status_code})",
            retryable=True, **kwargs
        )
        self.context["recipient"] = recipient
        self.context["status_code"] = status_code


class EmailDuplicateError(EmailError):
    """Email was already processed (dedup match).

    What: The message ID or sender+subject hash already exists in email_dedup.
    Why:  Not an error — expected behavior for Mailgun webhook retries. Not retryable.
    """
    def __init__(self, message_id: str, **kwargs):
        super().__init__(
            f"Duplicate email: {message_id}",
            retryable=False, severity="warning", **kwargs
        )


# ============================================================================
# Workflow Errors
# ============================================================================

class WorkflowError(AiElevateError):
    """Base exception for Temporal workflow execution errors."""
    def __init__(self, message: str, workflow_id: str = "", **kwargs):
        kwargs.setdefault("service_name", "workflow")
        super().__init__(message, **kwargs)
        self.context["workflow_id"] = workflow_id


class WorkflowStepFailedError(WorkflowError):
    """A specific step/activity in a workflow failed.

    What: An activity within a Temporal workflow raised an error.
    Why:  The workflow may continue if the step is non-critical, or fail
          if it's a required step. Retryability depends on the underlying error.
    """
    def __init__(self, workflow_id: str, step_name: str, reason: str = "", **kwargs):
        super().__init__(
            f"Workflow {workflow_id} step '{step_name}' failed: {reason}",
            workflow_id=workflow_id, **kwargs
        )
        self.context["step_name"] = step_name


class BuildVerificationError(WorkflowError):
    """Build output verification failed (missing files, wrong structure).

    What: The engineer agent completed but the expected artifacts are missing.
    Why:  Non-retryable — the agent needs different instructions, not a retry.
    """
    def __init__(self, project_slug: str, missing: list = None, **kwargs):
        super().__init__(
            f"Build verification failed for {project_slug}: missing {missing}",
            retryable=False, **kwargs
        )
        self.context["project_slug"] = project_slug
        self.context["missing_files"] = missing or []


class AuthVerificationError(WorkflowError):
    """Preview auth credentials could not be verified.

    What: The verify_auth_credentials activity could not find or validate
          login credentials for the deployed preview.
    Why:  Blocks preview email — customer can't test without credentials.
    """
    def __init__(self, project_slug: str, **kwargs):
        super().__init__(
            f"Auth credentials not verified for {project_slug} — preview email blocked",
            retryable=False, severity="warning", **kwargs
        )
        self.context["project_slug"] = project_slug


# ============================================================================
# Integration Errors
# ============================================================================

class KnowledgeGraphError(AiElevateError):
    """Knowledge graph read/write error."""
    def __init__(self, message: str, org: str = "", **kwargs):
        kwargs.setdefault("service_name", "knowledge-graph")
        super().__init__(message, retryable=False, severity="warning", **kwargs)
        self.context["org"] = org


class PlaneError(AiElevateError):
    """Plane project management API error."""
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault("service_name", "plane")
        super().__init__(message, retryable=True, severity="warning", **kwargs)


class StripeError(AiElevateError):
    """Stripe payment processing error."""
    def __init__(self, message: str, **kwargs):
        kwargs.setdefault("service_name", "stripe")
        super().__init__(message, retryable=False, severity="error", **kwargs)
