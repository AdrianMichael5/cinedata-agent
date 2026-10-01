import dataclasses
import hashlib
import sqlite3
import time
from pathlib import Path

import pytest

from cinedata_agent.db import sqlite as sqlite_module
from cinedata_agent.db.base import (
    Database,
    QueryExecutionError,
    QueryResult,
    QueryTimeoutError,
)
from cinedata_agent.db.errors import DatabaseError, DatabaseNotFoundError, UnsafeQueryError
from cinedata_agent.db.sqlite import SQLiteDatabase

INFINITE_COUNT = (
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
)
INFINITE_ROWS = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT x FROM c"


def make_db(path: Path, timeout_seconds: float = 5, max_rows: int = 100) -> SQLiteDatabase:
    return SQLiteDatabase(path, timeout_seconds=timeout_seconds, max_rows=max_rows)


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count_movies(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM dim_movies").fetchone()[0]
    connection.close()
    return count


class TestValidQuery:
    def test_returns_columns_rows_and_timing(self, sample_db):
        result = make_db(sample_db).run_query(
            "SELECT id, titulo FROM dim_movies ORDER BY id LIMIT 2"
        )

        assert result.columns == ("id", "titulo")
        assert result.rows == ((1, "Avatar"), (2, "Barbie"))
        assert result.truncated is False
        assert result.elapsed_ms >= 0

    def test_accepts_trailing_semicolon_and_joins(self, sample_db):
        sql = """
            SELECT g.nome, COUNT(*) AS filmes
            FROM bridge_movie_genre mg JOIN dim_genres g ON g.id = mg.genre_id
            GROUP BY g.nome ORDER BY g.nome;
        """

        result = make_db(sample_db).run_query(sql)

        assert result.rows == (("Animation", 1), ("Drama", 2))

    def test_result_is_immutable(self, sample_db):
        result = make_db(sample_db).run_query("SELECT 1 AS a")

        with pytest.raises(dataclasses.FrozenInstanceError):
            result.truncated = True  # type: ignore[misc]

    def test_satisfies_database_protocol(self, sample_db):
        database = make_db(sample_db)

        assert isinstance(database, Database)
        assert database.dialect == "sqlite"


class TestTruncation:
    def test_marks_truncated_when_more_rows_than_limit(self, sample_db):
        result = make_db(sample_db, max_rows=2).run_query("SELECT id FROM dim_movies ORDER BY id")

        assert result.rows == ((1,), (2,))
        assert result.truncated is True

    def test_not_truncated_when_rows_equal_limit(self, sample_db):
        result = make_db(sample_db, max_rows=5).run_query("SELECT id FROM dim_movies")

        assert len(result.rows) == 5
        assert result.truncated is False

    def test_does_not_load_every_row(self, sample_db):
        # An endless row source only works if rows are fetched lazily (fetchmany).
        result = make_db(sample_db, timeout_seconds=2, max_rows=3).run_query(INFINITE_ROWS)

        assert result.rows == ((1,), (2,), (3,))
        assert result.truncated is True


class TestBlockedQueries:
    def test_unsafe_sql_never_reaches_the_database(self, sample_db, monkeypatch):
        database = make_db(sample_db)

        def fail_connect(*args, **kwargs):
            raise AssertionError("sqlite3.connect must not be called for blocked SQL")

        monkeypatch.setattr(sqlite_module.sqlite3, "connect", fail_connect)

        with pytest.raises(UnsafeQueryError):
            database.run_query("DROP TABLE dim_movies")

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT name FROM sqlite_master",
            "SELECT * FROM filmes",
            "SELECT * FROM pragma_table_info('dim_movies')",
        ],
    )
    def test_uses_the_gold_table_allowlist(self, sample_db, sql):
        with pytest.raises(UnsafeQueryError):
            make_db(sample_db).run_query(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM dim_movies",
            "INSERT INTO dim_movies VALUES (99, 'X', 2000)",
            "UPDATE dim_movies SET titulo = 'X'",
            "DROP TABLE dim_movies",
            "CREATE TABLE hack (a INTEGER)",
            "ATTACH DATABASE 'other.db' AS other",
            "PRAGMA query_only = OFF",
        ],
    )
    def test_writes_fail_even_when_validator_is_bypassed(self, sample_db, monkeypatch, sql):
        monkeypatch.setattr(sqlite_module, "validate_select", lambda query, **kwargs: query)
        digest_before = file_digest(sample_db)

        with pytest.raises(QueryExecutionError):
            make_db(sample_db).run_query(sql)

        assert file_digest(sample_db) == digest_before
        assert count_movies(sample_db) == 5


class TestExecutionErrors:
    def test_unknown_column_keeps_sqlite_message(self, sample_db):
        with pytest.raises(QueryExecutionError, match="no such column: nao_existe"):
            make_db(sample_db).run_query("SELECT nao_existe FROM dim_movies")

    def test_allowed_table_missing_from_file_keeps_sqlite_message(self, sample_db):
        # dim_people is on the allowlist but the small test database does not have it.
        with pytest.raises(QueryExecutionError, match="no such table: dim_people"):
            make_db(sample_db).run_query("SELECT * FROM dim_people")

    def test_errors_share_a_base_class(self):
        for error in (QueryExecutionError, QueryTimeoutError, DatabaseNotFoundError):
            assert issubclass(error, DatabaseError)

    def test_file_that_is_not_sqlite(self, tmp_path):
        fake = tmp_path / "fake.db"
        fake.write_text("not a sqlite database " * 100, encoding="utf-8")

        with pytest.raises(QueryExecutionError, match="not a database"):
            make_db(fake).run_query("SELECT * FROM dim_movies")

    def test_connection_is_closed_when_setup_fails(self, sample_db, monkeypatch):
        class BrokenConnection:
            closed = False

            def execute(self, sql):
                raise sqlite3.OperationalError("setup failed")

            def close(self):
                BrokenConnection.closed = True

        monkeypatch.setattr(sqlite_module.sqlite3, "connect", lambda *a, **k: BrokenConnection())

        with pytest.raises(QueryExecutionError, match="setup failed"):
            make_db(sample_db).run_query("SELECT 1")

        assert BrokenConnection.closed is True


class TestTimeout:
    def test_slow_query_raises_timeout(self, sample_db):
        database = make_db(sample_db, timeout_seconds=0.2)
        started = time.monotonic()

        with pytest.raises(QueryTimeoutError, match="tempo limite"):
            database.run_query(INFINITE_COUNT)

        assert time.monotonic() - started < 2

    def test_database_still_usable_after_timeout(self, sample_db):
        database = make_db(sample_db, timeout_seconds=0.2)
        with pytest.raises(QueryTimeoutError):
            database.run_query(INFINITE_COUNT)

        assert database.run_query("SELECT COUNT(*) FROM dim_movies").rows == ((5,),)


class TestConstruction:
    def test_missing_file_explains_where_to_put_the_database(self, tmp_path):
        missing = tmp_path / "data" / "cinerocket.db"

        with pytest.raises(DatabaseNotFoundError) as error:
            make_db(missing)

        message = str(error.value)
        assert "cinerocket.db" in message
        assert "data/" in message
        assert str(missing) in message

    def test_directory_is_not_a_database_file(self, tmp_path):
        with pytest.raises(DatabaseNotFoundError):
            make_db(tmp_path)

    @pytest.mark.parametrize(
        ("timeout_seconds", "max_rows"),
        [(0, 10), (-1, 10), (1, 0), (1, -5)],
    )
    def test_rejects_non_positive_limits(self, sample_db, timeout_seconds, max_rows):
        with pytest.raises(ValueError):
            make_db(sample_db, timeout_seconds=timeout_seconds, max_rows=max_rows)

    def test_query_result_fields(self):
        result = QueryResult(columns=("a",), rows=((1,),), truncated=False, elapsed_ms=1.5)

        assert (result.columns, result.rows, result.truncated, result.elapsed_ms) == (
            ("a",),
            ((1,),),
            False,
            1.5,
        )
