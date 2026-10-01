"""Backend-agnostic query result and database interface."""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from cinedata_agent.db.errors import QueryExecutionError, QueryTimeoutError

__all__ = ["Database", "QueryExecutionError", "QueryResult", "QueryTimeoutError"]


@dataclass(frozen=True)
class QueryResult:
    """Rows returned by a read-only query, capped at the backend's row limit."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool
    elapsed_ms: float
    # The SQL that actually ran: macros expanded, validated and normalized.
    sql: str = ""


@runtime_checkable
class Database(Protocol):
    """A read-only backend. Implementations must validate SQL before executing it."""

    dialect: str

    def run_query(self, sql: str) -> QueryResult: ...
