"""Read-only SQLite backend: validation, time limit, row limit and an authorizer allowlist."""

import sqlite3
import time
from contextlib import closing
from pathlib import Path

from cinedata_agent.db.base import QueryResult
from cinedata_agent.db.errors import (
    DatabaseNotFoundError,
    QueryExecutionError,
    QueryTimeoutError,
)
from cinedata_agent.db.schema import SQLITE_ALLOWED_SCHEMAS, SQLITE_ALLOWED_TABLES
from cinedata_agent.db.validator import validate_select

# How many SQLite VM instructions run between two deadline checks.
PROGRESS_HANDLER_STEPS = 1_000

# Second line of defense after the validator: anything else (writes, ATTACH, PRAGMA...) is denied.
ALLOWED_ACTIONS = frozenset(
    {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
)


class SQLiteDatabase:
    """Runs validated SELECT statements on a SQLite file opened with mode=ro."""

    dialect: str = "sqlite"

    def __init__(self, path: Path | str, timeout_seconds: float, max_rows: int) -> None:
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

    def run_query(self, sql: str) -> QueryResult:
        """Validate and run one SELECT, returning at most max_rows rows."""
        normalized = validate_select(
            sql,
            dialect=self.dialect,
            allowed_tables=SQLITE_ALLOWED_TABLES,
            allowed_schemas=SQLITE_ALLOWED_SCHEMAS,
        )
        started = time.perf_counter()
        deadline = time.monotonic() + self.timeout_seconds

        try:
            with closing(self._connect()) as connection:
                connection.set_progress_handler(
                    lambda: time.monotonic() > deadline, PROGRESS_HANDLER_STEPS
                )
                cursor = connection.execute(normalized)
                rows = cursor.fetchmany(self.max_rows + 1)
                columns = tuple(column[0] for column in cursor.description or ())
        except sqlite3.Error as error:
            if time.monotonic() > deadline:
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
            connection.set_authorizer(_authorize)
        except sqlite3.Error:
            connection.close()
            raise
        return connection


def _authorize(
    action: int,
    arg1: str | None,
    arg2: str | None,
    db_name: str | None,
    trigger: str | None,
) -> int:
    return sqlite3.SQLITE_OK if action in ALLOWED_ACTIONS else sqlite3.SQLITE_DENY
