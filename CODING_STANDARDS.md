# AI Elevate Coding Standards — MANDATORY

Every Python file must follow these rules. No exceptions.

## Exceptions
- Never bare try/except Exception — use custom types from exceptions.py
- Import: from exceptions import AgentTimeoutError, DatabaseError, etc.

## Database
- Never inline psycopg2.connect() — use DAOs from dao.py
- Import: from dao import SentimentDAO, InteractionDAO, etc.

## Logging
- Never print() — use structured logging from logging_config.py
- Import: from logging_config import get_logger

## Docstrings
- Every class and public method: WHAT it does, WHY it exists, HOW it works
- Include Args, Returns, Raises sections

## SOLID
- Single Responsibility per file/class
- Dependency injection (pass DAOs as parameters)
- Depend on abstractions (BaseDAO), not concrete implementations

## Security
- Never hardcode credentials
- Always parameterized SQL queries
- Never log passwords, API keys, or PHI

## Foundation files
- /home/aielevate/exceptions.py — custom exception hierarchy
- /home/aielevate/dao.py — database access objects
- /home/aielevate/logging_config.py — structured JSON logging
