"""Read-only SQLite backend: validation, time limit, row limit and an authorizer allowlist."""

import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from cinedata_agent.db.base import QueryResult
from cinedata_agent.db.errors import (
    DatabaseNotFoundError,
    QueryExecutionError,
    QueryTimeoutError,
)
from cinedata_agent.db.schema import SQLITE_ALLOWED_SCHEMAS, SQLITE_ALLOWED_TABLES
from cinedata_agent.db.validator import (
    LITERAL_TABLE_FUNCTIONS,
    defined_cte_names,
    validate_select,
)

# How many SQLite VM instructions run between two deadline checks.
PROGRESS_HANDLER_STEPS = 1_000

# Largest string/blob a query may build (zeroblob, printf...): caps memory use per value.
MAX_VALUE_BYTES = 10_000_000

# Second line of defense after the validator: anything else (writes, ATTACH, PRAGMA...) is denied.
ALLOWED_ACTIONS = frozenset(
    {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
)

Authorizer = Callable[[int, str | None, str | None, str | None, str | None], int]


class SQLiteDatabase:
    """Runs validated SELECT statements on a SQLite file opened with mode=ro."""

    dialect: str = "sqlite"

    def __init__(
        self,
        path: Path | str,
        timeout_seconds: float,
        max_rows: int,
        allowed_tables: frozenset[str] = SQLITE_ALLOWED_TABLES,
        allowed_schemas: frozenset[str] = SQLITE_ALLOWED_SCHEMAS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_rows <= 0:
            raise ValueError("max_rows must be positive")

        self.path = Path(path)
        if not self.path.is_file():
            raise DatabaseNotFoundError(
                f"Banco de dados não encontrado em '{self.path}'. Baixe o cinerocket.db e "
                "coloque-o em data/cinerocket.db (ou ajuste DB_PATH no .env)."
            )
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self.allowed_tables = allowed_tables
        self.allowed_schemas = allowed_schemas

    def run_query(self, sql: str) -> QueryResult:
        """Validate and run one SELECT, returning at most max_rows rows."""
        normalized = validate_select(
            sql,
            dialect=self.dialect,
            allowed_tables=self.allowed_tables,
            allowed_schemas=self.allowed_schemas,
        )
        authorizer = _make_authorizer(
            self.allowed_tables,
            self.allowed_schemas,
            defined_cte_names(normalized, dialect=self.dialect),
        )
        started = time.perf_counter()
        deadline = _Deadline(self.timeout_seconds)

        try:
            with closing(self._connect()) as connection:
                connection.set_authorizer(authorizer)
                connection.set_progress_handler(deadline, PROGRESS_HANDLER_STEPS)
                cursor = connection.execute(normalized)
                rows = cursor.fetchmany(self.max_rows + 1)
                columns = tuple(column[0] for column in cursor.description or ())
        except sqlite3.Error as error:
            if deadline.expired:
                raise QueryTimeoutError(
                    f"A consulta excedeu o tempo limite de {self.timeout_seconds:g} s. "
                    "Simplifique a SQL (menos junções, filtros mais seletivos) e tente de novo."
                ) from error
            raise QueryExecutionError(f"Erro do SQLite: {error}") from error

        return QueryResult(
            columns=columns,
            rows=tuple(tuple(row) for row in rows[: self.max_rows]),
            truncated=len(rows) > self.max_rows,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    def _connect(self) -> sqlite3.Connection:
        # as_uri() percent-encodes spaces and '#', which a raw f"file:{path}" would break on.
        connection = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            connection.execute("PRAGMA query_only = ON")
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
        except sqlite3.Error:
            connection.close()
            raise
        return connection


class _Deadline:
    """Progress handler that aborts the query once the time limit is over and remembers it."""

    def __init__(self, seconds: float) -> None:
        self._limit = time.monotonic() + seconds
        self.expired = False

    def __call__(self) -> int:
        if time.monotonic() > self._limit:
            self.expired = True
        return int(self.expired)


def _make_authorizer(
    tables: frozenset[str], schemas: frozenset[str], cte_names: frozenset[str]
) -> Authorizer:
    """Deny every action outside ALLOWED_ACTIONS and every read of a table off the allowlist.

    Column reads carry a schema ("main") and must hit an allowlisted table, so a gap in the
    validator's CTE handling cannot expose data. Row-count reads (COUNT(*)) carry no schema
    and may also target a CTE of this query; at worst they reveal a row count, never content.
    json_each/json_tree show up as reads; the validator only lets them run over literals.
    """
    readable = frozenset(name.lower() for name in tables) | LITERAL_TABLE_FUNCTIONS
    readable_schemas = frozenset(name.lower() for name in schemas)

    def authorize(
        action: int,
        arg1: str | None,
        arg2: str | None,
        db_name: str | None,
        trigger: str | None,
    ) -> int:
        if action not in ALLOWED_ACTIONS:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ:
            table = (arg1 or "").lower()
            if db_name is None:
                allowed = table in readable or table in cte_names
            else:
                allowed = db_name.lower() in readable_schemas and table in readable
            if not allowed:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return authorize
