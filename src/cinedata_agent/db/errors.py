"""Database-layer exceptions. Messages are in Portuguese because they are sent back to the LLM."""


class DatabaseError(Exception):
    """Base class for all database-layer errors."""


class UnsafeQueryError(DatabaseError, ValueError):
    """Raised when SQL is not a single read-only query or cannot be parsed."""


class QueryTimeoutError(DatabaseError):
    """Raised when a query runs longer than the configured time limit."""


class QueryExecutionError(DatabaseError):
    """Raised when the database rejects a query; carries the original database message."""


class DatabaseNotFoundError(DatabaseError, FileNotFoundError):
    """Raised when the database file does not exist."""
