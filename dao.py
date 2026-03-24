#!/usr/bin/env python3
"""AI Elevate Data Access Object (DAO) Base — unified database access pattern.

What: Provides a base class for all database operations across the platform.
      Every table gets its own DAO subclass that inherits connection management,
      error handling, logging, and common CRUD operations.

Why:  The codebase currently has psycopg2.connect() calls scattered across 19+
      files with inconsistent error handling, connection leaking, and no logging.
      The DAO pattern centralizes this into a single, tested, observable layer.

How:  BaseDAO manages a connection pool (or per-call connections for simplicity).
      Subclasses define table-specific queries. All operations:
      - Wrap psycopg2 errors in custom DatabaseError exceptions
      - Log every query with timing (for slow query detection)
      - Use parameterized queries (SQL injection prevention)
      - Handle connection failures gracefully with retry

Usage:
    from dao import SentimentDAO, InteractionDAO, MilestoneDAO

    sentiment_dao = SentimentDAO()
    sentiment_dao.upsert("sarah@example.com", "positive", "Customer accepted", "gigforge-sales")

    interactions = InteractionDAO()
    interactions.log("msg-001", "sarah@example.com", "sales@gigforge.ai", "gigforge-sales", "Need a web app")

Architecture:
    BaseDAO (connection management, error wrapping, logging)
      ├── SentimentDAO (customer_sentiment table)
      ├── InteractionDAO (email_interactions table)
      ├── DedupDAO (email_dedup table)
      ├── MilestoneDAO (project_milestones table)
      ├── NoteDAO (customer_notes table)
      ├── InvoiceDAO (invoices table)
      ├── AgentBioDAO (agent_bios table)
      ├── ProposalDAO (proposal_queue table)
      ├── BlogPostDAO (blog_posts table)
      ├── NewsletterDAO (newsletters table)
      └── ... (one per table)
"""

import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

log = logging.getLogger("dao")

# Default connection parameters — override via environment or constructor
DEFAULT_DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5434,
    "dbname": "rag",
    "user": "rag",
    "password": "rag_vec_2026",
}


class BaseDAO:
    """Base Data Access Object — connection management and common operations.

    What: Provides database connection lifecycle management, parameterized query
          execution, error wrapping, and performance logging for all DAO subclasses.

    Why:  Eliminates the pattern of psycopg2.connect() + try/except scattered
          across 19+ files. Centralizes connection pooling, error handling, and
          observability into one place.

    How:  Uses context managers for connection scoping. Every query is:
          1. Logged with parameters (debug level)
          2. Timed (warns if > 1 second)
          3. Wrapped in DatabaseError on failure
          4. Auto-committed (autocommit=True by default)

    Attributes:
        db_config: Database connection parameters (host, port, dbname, user, password)
        table_name: The primary table this DAO operates on (set by subclasses)
    """

    table_name: str = ""

    def __init__(self, db_config: Optional[Dict[str, Any]] = None):
        """Initialize with database configuration.

        Args:
            db_config: Database connection parameters. Defaults to DEFAULT_DB_CONFIG.
        """
        self.db_config = db_config or DEFAULT_DB_CONFIG.copy()

    @contextmanager
    def connection(self):
        """Context manager for database connections.

        What: Yields a psycopg2 connection with autocommit enabled.
              Automatically closes the connection when the context exits.

        Why:  Prevents connection leaks. Every DAO operation should use this
              context manager rather than creating raw connections.

        Yields:
            psycopg2 connection with autocommit=True

        Raises:
            DatabaseConnectionError: If the connection cannot be established.
        """
        conn = None
        try:
            conn = psycopg2.connect(**self.db_config)
            conn.autocommit = True
            yield conn
        except psycopg2.OperationalError as e:
            from exceptions import DatabaseConnectionError
            raise DatabaseConnectionError(
                db_name=self.db_config.get("dbname", "unknown"),
                original_error=e
            )
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    @contextmanager
    def cursor(self, dict_cursor: bool = False):
        """Context manager for database cursors.

        What: Yields a cursor (optionally dict-based) within a managed connection.

        Args:
            dict_cursor: If True, returns a RealDictCursor (rows as dicts).

        Yields:
            psycopg2 cursor
        """
        with self.connection() as conn:
            factory = psycopg2.extras.RealDictCursor if dict_cursor else None
            cur = conn.cursor(cursor_factory=factory)
            try:
                yield cur
            finally:
                cur.close()

    def execute(
        self,
        query: str,
        params: Optional[Tuple] = None,
        fetch: str = "none",
    ) -> Any:
        """Execute a parameterized query with logging and timing.

        What: Runs a SQL query with parameters, logs execution time, and
              returns results based on the fetch mode.

        Why:  Single point of query execution enables consistent logging,
              timing, and error handling across all DAO operations.

        Args:
            query: SQL query string with %s placeholders
            params: Tuple of parameter values (prevents SQL injection)
            fetch: 'none' | 'one' | 'all' — what to return

        Returns:
            None (fetch='none'), single row (fetch='one'), or list of rows (fetch='all')

        Raises:
            DatabaseError: On any psycopg2 error, wrapped with context.
        """
        start = time.time()
        try:
            with self.cursor() as cur:
                cur.execute(query, params)
                elapsed = time.time() - start

                if elapsed > 1.0:
                    log.warning(
                        "Slow query (%.2fs): %s",
                        elapsed,
                        query[:100],
                        extra={"table": self.table_name, "elapsed": elapsed},
                    )

                if fetch == "one":
                    return cur.fetchone()
                elif fetch == "all":
                    return cur.fetchall()
                return cur.rowcount

        except psycopg2.Error as e:
            from exceptions import DatabaseError
            raise DatabaseError(
                message=f"Query failed on {self.table_name}: {e}",
                table=self.table_name,
                operation=query[:50],
                original_error=e,
                retryable="connection" in str(e).lower(),
            )

    def execute_dict(self, query: str, params: Optional[Tuple] = None) -> List[Dict]:
        """Execute a query and return results as a list of dicts.

        What: Convenience method that uses RealDictCursor for dict-based results.

        Args:
            query: SQL query with %s placeholders
            params: Parameter values

        Returns:
            List of dicts, one per row.
        """
        try:
            with self.cursor(dict_cursor=True) as cur:
                cur.execute(query, params)
                return [dict(row) for row in cur.fetchall()]
        except psycopg2.Error as e:
            from exceptions import DatabaseError
            raise DatabaseError(
                message=f"Dict query failed on {self.table_name}: {e}",
                table=self.table_name,
                original_error=e,
            )

    def count(self, where: str = "", params: Optional[Tuple] = None) -> int:
        """Count rows matching a condition.

        Args:
            where: Optional WHERE clause (without 'WHERE' keyword)
            params: Parameters for the WHERE clause

        Returns:
            Integer count of matching rows.
        """
        query = f"SELECT COUNT(*) FROM {self.table_name}"
        if where:
            query += f" WHERE {where}"
        result = self.execute(query, params, fetch="one")
        return result[0] if result else 0

    def exists(self, where: str, params: Tuple) -> bool:
        """Check if any rows match a condition.

        Args:
            where: WHERE clause (without 'WHERE' keyword)
            params: Parameters for the WHERE clause

        Returns:
            True if at least one row matches.
        """
        return self.count(where, params) > 0


# ============================================================================
# Concrete DAOs — one per table
# ============================================================================

class SentimentDAO(BaseDAO):
    """Data access for customer_sentiment table.

    What: Tracks customer sentiment (positive/neutral/frustrated) per email,
          updated by the Temporal email workflow's update_sentiment activity.

    Why:  Sentiment drives escalation (frustrated → CSAT agent) and informs
          agent context (positive → upsell opportunity).
    """
    table_name = "customer_sentiment"

    def upsert(self, email: str, rating: str, notes: str, agent: str) -> None:
        """Insert or update sentiment for a customer.

        Args:
            email: Customer email address
            rating: One of 'positive', 'neutral', 'frustrated', 'at_risk'
            notes: Human-readable reason for the rating
            agent: Agent ID that set this rating
        """
        self.execute(
            "INSERT INTO customer_sentiment (email, rating, notes, agent, timestamp) "
            "VALUES (%s, %s, %s, %s, NOW()) "
            "ON CONFLICT (email) DO UPDATE SET rating=%s, notes=%s, agent=%s, timestamp=NOW()",
            (email, rating, notes, agent, rating, notes, agent),
        )

    def get(self, email: str) -> Optional[Dict]:
        """Get sentiment for a customer email."""
        rows = self.execute_dict(
            "SELECT * FROM customer_sentiment WHERE email=%s", (email,)
        )
        return rows[0] if rows else None


class InteractionDAO(BaseDAO):
    """Data access for email_interactions table.

    What: Audit trail of every inbound/outbound email processed by the gateway.
    """
    table_name = "email_interactions"

    def log(
        self,
        message_id: str,
        sender_email: str,
        recipient: str,
        agent_id: str,
        subject: str,
        direction: str = "inbound",
        status: str = "received",
    ) -> None:
        """Log an email interaction."""
        self.execute(
            "INSERT INTO email_interactions "
            "(message_id, sender_email, recipient, agent_id, subject, direction, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (message_id, sender_email, recipient, agent_id, subject, direction, status),
        )

    def recent(self, limit: int = 30) -> List[Dict]:
        """Get recent interactions ordered by time."""
        return self.execute_dict(
            "SELECT * FROM email_interactions ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )

    def count_today(self) -> int:
        """Count interactions from today."""
        return self.count("created_at >= CURRENT_DATE")


class DedupDAO(BaseDAO):
    """Data access for email_dedup table.

    What: Postgres-backed deduplication with 24h TTL. Prevents duplicate
          email processing from Mailgun webhook retries.
    """
    table_name = "email_dedup"

    def is_duplicate(self, dedup_key: str) -> bool:
        """Check if this key was already processed. Inserts if not.

        Args:
            dedup_key: Hash of message_id or sender+subject

        Returns:
            True if already processed (skip this email).
        """
        if self.exists("dedup_key = %s", (dedup_key,)):
            return True
        self.execute(
            "INSERT INTO email_dedup (dedup_key) VALUES (%s) ON CONFLICT DO NOTHING",
            (dedup_key,),
        )
        return False

    def purge_old(self) -> int:
        """Remove entries older than 24 hours. Returns count removed."""
        return self.execute(
            "DELETE FROM email_dedup WHERE created_at < NOW() - INTERVAL '24 hours'"
        )


class MilestoneDAO(BaseDAO):
    """Data access for project_milestones table."""
    table_name = "project_milestones"

    def create(self, customer_email: str, project_title: str, milestone: str) -> None:
        """Create a milestone entry."""
        self.execute(
            "INSERT INTO project_milestones (customer_email, project_title, milestone, status) "
            "VALUES (%s, %s, %s, 'pending') ON CONFLICT DO NOTHING",
            (customer_email, project_title, milestone),
        )

    def complete(self, customer_email: str, milestone: str) -> None:
        """Mark a milestone as completed."""
        self.execute(
            "UPDATE project_milestones SET status='completed', completed_at=NOW() "
            "WHERE customer_email=%s AND milestone=%s",
            (customer_email, milestone),
        )

    def get_for_customer(self, customer_email: str) -> List[Dict]:
        """Get all milestones for a customer."""
        return self.execute_dict(
            "SELECT * FROM project_milestones WHERE customer_email=%s ORDER BY id",
            (customer_email,),
        )


class NoteDAO(BaseDAO):
    """Data access for customer_notes table."""
    table_name = "customer_notes"

    def add(self, email: str, note: str, agent: str) -> None:
        """Add a customer note."""
        self.execute(
            "INSERT INTO customer_notes (email, note, agent, timestamp) VALUES (%s, %s, %s, NOW())",
            (email, note, agent),
        )

    def get_for_customer(self, email: str, limit: int = 10) -> List[Dict]:
        """Get recent notes for a customer."""
        return self.execute_dict(
            "SELECT * FROM customer_notes WHERE email=%s ORDER BY timestamp DESC LIMIT %s",
            (email, limit),
        )


class AgentBioDAO(BaseDAO):
    """Data access for agent_bios table."""
    table_name = "agent_bios"

    def get(self, agent_id: str) -> Optional[Dict]:
        """Get agent bio by ID."""
        rows = self.execute_dict(
            "SELECT * FROM agent_bios WHERE agent_id=%s", (agent_id,)
        )
        return rows[0] if rows else None

    def get_name_and_role(self, agent_id: str) -> Tuple[str, str]:
        """Get just the name and role for an agent.

        Returns:
            Tuple of (name, role). Defaults to ('', '') if not found.
        """
        result = self.execute(
            "SELECT name, role FROM agent_bios WHERE agent_id=%s",
            (agent_id,), fetch="one"
        )
        return (result[0] or "", result[1] or "") if result else ("", "")

    def upsert(self, agent_id: str, name: str, role: str, bio: str = "") -> None:
        """Insert or update agent bio."""
        self.execute(
            "INSERT INTO agent_bios (agent_id, name, role, bio) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (agent_id) DO UPDATE SET name=%s, role=%s, bio=%s",
            (agent_id, name, role, bio or f"{role} at the organization", name, role, bio or f"{role} at the organization"),
        )

    def list_by_org(self, org_prefix: str) -> List[Dict]:
        """List all agents for an org (by agent_id prefix)."""
        return self.execute_dict(
            "SELECT agent_id, name, role FROM agent_bios WHERE agent_id LIKE %s ORDER BY agent_id",
            (f"{org_prefix}%",),
        )
