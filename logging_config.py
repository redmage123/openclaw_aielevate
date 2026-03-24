#!/usr/bin/env python3
"""AI Elevate Structured Logging — JSON logging with alert integration.

What: Provides a unified logging configuration that outputs structured JSON
      log lines. Integrates with the auditor, watchdog, and ops dashboard
      for real-time alert visibility.

Why:  The codebase uses inconsistent logging (some files use print(), some
      use logging.info(), some have no logging). Structured JSON logs enable:
      - Searchability (grep by service, severity, agent_id)
      - Dashboard integration (ops_dashboard reads log files)
      - Alert triggering (critical logs → ops notification)
      - Performance tracking (timing fields in every log)

How:  Configures Python's logging module with a JSON formatter that outputs
      one JSON object per line. Each log entry includes:
      - timestamp, level, service, message
      - Extra fields from LoggerAdapter (agent_id, customer_email, etc.)
      - Exception info if present

Usage:
    from logging_config import get_logger

    log = get_logger("build-workflow")
    log.info("Sprint plan created", extra={"project": "carehaven", "step": 4})
    log.error("Deploy failed", extra={"port": 4106}, exc_info=True)

    # With context adapter (carries fields across multiple log calls)
    log = get_logger("email-gateway", customer="sarah@example.com", agent="gigforge-sales")
    log.info("Email received")  # automatically includes customer + agent
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects.

    What: Each log line is a valid JSON object with structured fields,
          making logs machine-parseable while remaining human-readable.

    Why:  Plain text logs are hard to search and aggregate. JSON logs
          integrate with dashboards, alerting systems, and log aggregators.

    How:  Overrides logging.Formatter.format() to serialize the LogRecord
          as a JSON dict. Extra fields from logger.info(..., extra={})
          are merged into the output.
    """

    SKIP_FIELDS = {
        "args", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelno", "lineno", "module", "msecs", "pathname", "process",
        "processName", "relativeCreated", "stack_info", "thread", "threadName",
        "msg", "name", "levelname", "message",
    }

    def format(self, record: logging.LogRecord) -> str:
        """Format a LogRecord as a JSON line.

        Args:
            record: The log record to format.

        Returns:
            Single-line JSON string.
        """
        output = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "service": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields (from logger.info(..., extra={...}))
        for key, value in record.__dict__.items():
            if key not in self.SKIP_FIELDS and not key.startswith("_"):
                try:
                    json.dumps(value)  # Verify serializable
                    output[key] = value
                except (TypeError, ValueError):
                    output[key] = str(value)

        # Add exception info if present
        if record.exc_info and record.exc_info[1]:
            output["exception"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }

        return json.dumps(output, default=str, ensure_ascii=False)


class AlertHandler(logging.Handler):
    """Sends critical/error logs to ops notification system.

    What: A logging handler that triggers ops alerts for high-severity events.

    Why:  Critical errors (circuit breaker open, gateway down, build failures)
          need immediate visibility — not just in log files.

    How:  On every ERROR or CRITICAL log, calls ops_notify() which dispatches
          to the ops dashboard alerts panel and optionally to email/Telegram.
    """

    def __init__(self, min_level: int = logging.ERROR):
        """Initialize with minimum alert level.

        Args:
            min_level: Minimum logging level to trigger alerts (default: ERROR)
        """
        super().__init__(min_level)
        self.setLevel(min_level)

    def emit(self, record: logging.LogRecord) -> None:
        """Send alert for high-severity log events.

        Args:
            record: The log record that triggered the alert.
        """
        try:
            from ops_notify import ops_notify
            severity = "escalation" if record.levelno >= logging.CRITICAL else "blocker"
            message = f"[{record.name}] {record.getMessage()}"
            ops_notify(
                severity,
                message[:300],
                agent=record.name,
                customer_email=getattr(record, "customer_email", ""),
            )
        except Exception:
            pass  # Never let alert failures crash the app


class ContextAdapter(logging.LoggerAdapter):
    """Logger adapter that carries context fields across log calls.

    What: Wraps a logger with persistent extra fields (agent_id, customer_email,
          project, org) that are included in every log call automatically.

    Why:  Avoids repeating extra={...} on every log call within a function
          that handles a specific customer/agent/project.

    Usage:
        log = ContextAdapter(logger, {"agent": "gigforge-sales", "org": "gigforge"})
        log.info("Processing email")  # includes agent + org automatically
        log.info("Reply sent", extra={"chars": 500})  # adds chars too
    """

    def process(self, msg, kwargs):
        """Merge adapter context with per-call extras.

        Args:
            msg: Log message
            kwargs: Keyword args including 'extra'

        Returns:
            Tuple of (message, updated kwargs)
        """
        extra = {**self.extra, **kwargs.get("extra", {})}
        kwargs["extra"] = extra
        return msg, kwargs


def get_logger(
    name: str,
    level: str = "INFO",
    enable_alerts: bool = True,
    **context,
) -> logging.Logger:
    """Get a configured structured logger.

    What: Returns a logger configured with JSON formatting, stdout output,
          and optional alert handler for critical errors.

    Why:  Single function to get a properly configured logger. No more
          inconsistent logging.basicConfig() calls scattered across files.

    Args:
        name: Logger name (usually the service name, e.g., 'build-workflow')
        level: Logging level ('DEBUG', 'INFO', 'WARNING', 'ERROR')
        enable_alerts: Whether to attach the AlertHandler
        **context: Persistent context fields (agent_id, org, customer_email)

    Returns:
        Configured logger (or ContextAdapter if context is provided)
    """
    logger = logging.getLogger(name)

    # Only configure if not already configured (avoid duplicate handlers)
    if not logger.handlers:
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))

        # JSON stdout handler
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(JsonFormatter())
        logger.addHandler(stdout_handler)

        # Alert handler for critical errors
        if enable_alerts:
            logger.addHandler(AlertHandler(logging.ERROR))

        # Prevent propagation to root logger (avoids duplicate output)
        logger.propagate = False

    if context:
        return ContextAdapter(logger, context)

    return logger
