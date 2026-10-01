import dataclasses
import hashlib
import logging
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
            SELECT g.nome_genero, COUNT(*) AS filmes
            FROM bridge_movie_genre mg JOIN dim_genres g ON g.id = mg.genre_id
            GROUP BY g.nome_genero ORDER BY g.nome_genero;
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

        assert time.monotonic() - started < 10  # generous: slow CI must not flake

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


@pytest.fixture
def db_with_secret(sample_db: Path) -> Path:
    """sample_db plus a table that is not on the allowlist."""
    with sqlite3.connect(sample_db) as connection:
        connection.execute("CREATE TABLE secret (x TEXT)")
        connection.execute("INSERT INTO secret VALUES ('S3CRET')")
    connection.close()
    return sample_db


def bypass_validator(monkeypatch) -> None:
    monkeypatch.setattr(sqlite_module, "validate_select", lambda query, **kwargs: query)


class TestTableLevelAuthorizer:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM secret",
            "SELECT COUNT(*) FROM secret",
            "SELECT name, sql FROM sqlite_master",
            "SELECT * FROM secret WHERE 1 IN (WITH secret AS (SELECT 1) SELECT 1)",
        ],
    )
    def test_reads_outside_allowlist_are_denied_by_sqlite(self, db_with_secret, monkeypatch, sql):
        bypass_validator(monkeypatch)

        with pytest.raises(QueryExecutionError, match="not authorized|prohibited"):
            make_db(db_with_secret).run_query(sql)

    def test_cte_scope_trick_is_blocked_end_to_end(self, db_with_secret):
        with pytest.raises(UnsafeQueryError):
            make_db(db_with_secret).run_query(
                "SELECT * FROM secret WHERE 1 IN (WITH secret AS (SELECT 1) SELECT 1)"
            )

    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            ("SELECT COUNT(*) FROM dim_movies", ((5,),)),
            ("WITH x AS (SELECT id FROM dim_movies WHERE id < 3) SELECT COUNT(*) FROM x", ((2,),)),
            (
                "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 3) "
                "SELECT COUNT(*) FROM r",
                ((3,),),
            ),
            (
                "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 4) "
                "SELECT COUNT(*) FROM r CROSS JOIN dim_genres",
                ((8,),),
            ),
            ('SELECT COUNT(*) FROM json_each(\'["a", "b"]\')', ((2,),)),
            (
                "SELECT COUNT(*) FROM dim_movies WHERE titulo NOT IN (SELECT nome_genero "
                "FROM dim_genres UNION ALL SELECT value FROM json_each('[\"Coco\"]'))",
                ((4,),),
            ),
            (
                "SELECT MAX(r) FROM (SELECT RANK() OVER (ORDER BY ano) AS r FROM dim_movies)",
                ((5,),),
            ),
        ],
        ids=[
            "count",
            "cte",
            "recursive-cte",
            "recursive-cte-join",
            "json-each",
            "invalid-names-pattern",
            "window",
        ],
    )
    def test_legitimate_reads_still_work(self, sample_db, sql, expected):
        assert make_db(sample_db).run_query(sql).rows == expected


class TestTimeoutDetection:
    def test_error_after_the_deadline_is_not_reported_as_timeout(self, sample_db, monkeypatch):
        # The clock jumps past the deadline, but the query fails at prepare time:
        # the progress handler never aborted it, so this is an execution error.
        ticks = iter([0.0])
        monkeypatch.setattr(sqlite_module.time, "monotonic", lambda: next(ticks, 1_000_000.0))

        with pytest.raises(QueryExecutionError, match="no such column"):
            make_db(sample_db, timeout_seconds=1).run_query("SELECT nao_existe FROM dim_movies")


class TestValueSizeLimit:
    def test_huge_values_are_rejected(self, sample_db):
        with pytest.raises(QueryExecutionError, match="too big"):
            make_db(sample_db).run_query("SELECT length(zeroblob(50000000))")

    def test_normal_values_fit(self, sample_db):
        assert make_db(sample_db).run_query("SELECT length(zeroblob(1000))").rows == ((1000,),)


class TestMacrosAndExecutedSql:
    MACRO_SQL = (
        "SELECT titulo FROM dim_movies WHERE titulo NOT IN {{NOMES_INVALIDOS}} ORDER BY id LIMIT 2"
    )

    def test_expands_macro_before_validating_and_running(self, sample_db):
        result = make_db(sample_db).run_query(self.MACRO_SQL)

        assert result.rows == (("Avatar",), ("Barbie",))

    def test_result_keeps_the_expanded_sql(self, sample_db):
        result = make_db(sample_db).run_query(self.MACRO_SQL)

        assert "{{" not in result.sql
        assert "json_each" in result.sql.lower()  # the validator upper-cases functions
        assert "dim_movies" in result.sql

    def test_log_records_the_expanded_sql(self, sample_db, caplog):
        caplog.set_level(logging.INFO, logger="cinedata_agent.db.sqlite")

        make_db(sample_db).run_query(self.MACRO_SQL)

        assert "json_each" in caplog.text.lower()
        assert "{{NOMES_INVALIDOS}}" not in caplog.text

    def test_unknown_macro_never_reaches_the_database(self, sample_db, monkeypatch):
        database = make_db(sample_db)

        def fail_connect(*args, **kwargs):
            raise AssertionError("sqlite3.connect must not be called")

        monkeypatch.setattr(sqlite_module.sqlite3, "connect", fail_connect)

        with pytest.raises(UnsafeQueryError, match="NOMES_INVALIDOS"):
            database.run_query("SELECT * FROM dim_people WHERE nome_pessoa NOT IN {{X}}")
