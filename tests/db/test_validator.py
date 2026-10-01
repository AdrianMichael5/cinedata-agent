import json
from pathlib import Path

import pytest

from cinedata_agent.db.errors import UnsafeQueryError
from cinedata_agent.db.schema import SQLITE_ALLOWED_SCHEMAS, SQLITE_ALLOWED_TABLES
from cinedata_agent.db.validator import validate_select

GABARITO_PATH = Path(__file__).resolve().parents[2] / "eval" / "gabarito.json"


def _gabarito_queries() -> list[pytest.param]:
    if not GABARITO_PATH.exists():
        return []
    params = []
    for question in json.loads(GABARITO_PATH.read_text(encoding="utf-8")):
        queries = [question["principal"], *question["variantes"]]
        for index, query in enumerate(queries):
            params.append(pytest.param(query["sql"], id=f"{question['id']}-{index}"))
    return params


ACCEPTED = [
    pytest.param("SELECT titulo FROM dim_movies", id="simple"),
    pytest.param("SELECT 1;", id="trailing-semicolon"),
    pytest.param("WITH t AS (SELECT 1 AS a) SELECT a FROM t", id="cte"),
    pytest.param("SELECT 1 UNION SELECT 2", id="union"),
    pytest.param("SELECT 1 UNION ALL SELECT 2", id="union-all"),
    pytest.param("SELECT 1 INTERSECT SELECT 1", id="intersect"),
    pytest.param("SELECT 1 EXCEPT SELECT 2", id="except"),
    pytest.param(
        "WITH a AS (SELECT 1 AS x) SELECT x FROM a UNION SELECT 2",
        id="cte-with-union",
    ),
    pytest.param(
        "SELECT * FROM dim_movies WHERE sk_movie_id IN (SELECT sk_movie_id FROM dim_reviews)",
        id="subquery",
    ),
    pytest.param("SELECT * FROM (SELECT 1 AS a) AS t", id="derived-table"),
    pytest.param("SELECT 'a; DELETE FROM x' AS s", id="literal-with-semicolon-and-delete"),
    pytest.param("SELECT 1 -- fim", id="trailing-line-comment"),
    pytest.param("SELECT 1; -- comentário depois", id="comment-after-semicolon"),
    pytest.param("SELECT 1;;", id="double-semicolon"),
    pytest.param("  \n SELECT 1 \n ", id="surrounding-whitespace"),
]

FORBIDDEN_STATEMENTS = [
    pytest.param("INSERT INTO x VALUES (1)", id="insert"),
    pytest.param("INSERT OR REPLACE INTO x VALUES (1)", id="insert-or-replace"),
    pytest.param("REPLACE INTO x VALUES (1)", id="replace"),
    pytest.param("UPDATE x SET a = 1", id="update"),
    pytest.param("DELETE FROM x", id="delete"),
    pytest.param("DROP TABLE x", id="drop"),
    pytest.param("CREATE TABLE x (a INT)", id="create-table"),
    pytest.param("CREATE VIEW v AS SELECT 1", id="create-view"),
    pytest.param("ALTER TABLE x ADD COLUMN b INT", id="alter"),
    pytest.param("ATTACH DATABASE 'other.db' AS other", id="attach"),
    pytest.param("DETACH DATABASE other", id="detach"),
    pytest.param("PRAGMA table_info(x)", id="pragma"),
    pytest.param("PRAGMA writable_schema = 1", id="pragma-assign"),
    pytest.param("VACUUM", id="vacuum"),
    pytest.param("REINDEX x", id="reindex"),
    pytest.param("ANALYZE", id="analyze"),
    pytest.param("ANALYZE x", id="analyze-table"),
    pytest.param("BEGIN", id="begin"),
    pytest.param("BEGIN TRANSACTION", id="begin-transaction"),
    pytest.param("COMMIT", id="commit"),
    pytest.param("ROLLBACK", id="rollback"),
    pytest.param("SAVEPOINT s", id="savepoint"),
    pytest.param("RELEASE s", id="release"),
    pytest.param("EXPLAIN SELECT 1", id="explain-command"),
]

MULTIPLE_STATEMENTS = [
    pytest.param("SELECT 1; SELECT 2", id="two-selects"),
    pytest.param("SELECT 1; DROP TABLE x", id="select-then-drop"),
    pytest.param("SELECT 1;\nDELETE FROM x;", id="select-then-delete"),
]

HIDDEN_WRITES = [
    pytest.param("WITH c AS (SELECT 1) DELETE FROM x", id="with-delete"),
    pytest.param(
        "WITH d AS (DELETE FROM x RETURNING *) SELECT * FROM d",
        id="delete-inside-cte",
    ),
]

EMPTY = [
    pytest.param("", id="empty"),
    pytest.param("   \n\t", id="whitespace"),
    pytest.param(";", id="only-semicolon"),
    pytest.param("-- só comentário", id="only-line-comment"),
    pytest.param("/* só comentário */", id="only-block-comment"),
]

NOT_SQL = [
    pytest.param("olá, quais são os filmes mais populares?", id="natural-language"),
    pytest.param("SELECT FROM WHERE", id="broken-select"),
    pytest.param("SELECT 'sem fechar", id="unterminated-string"),
]

FORBIDDEN_FUNCTIONS = [
    pytest.param("SELECT load_extension('x')", id="load-extension"),
    pytest.param("SELECT LOAD_EXTENSION('x')", id="load-extension-upper"),
    pytest.param(
        "SELECT * FROM t WHERE a = load_extension('x', 'init')", id="load-extension-nested"
    ),
    pytest.param("SELECT readfile('/etc/passwd')", id="readfile"),
    pytest.param("SELECT writefile('x', 'y')", id="writefile"),
]


class TestAccepted:
    @pytest.mark.parametrize("sql", ACCEPTED)
    def test_accepts_read_only_queries(self, sql):
        normalized = validate_select(sql)

        assert normalized
        assert not normalized.rstrip().endswith(";")

    @pytest.mark.parametrize("sql", ACCEPTED)
    def test_normalized_sql_is_still_valid(self, sql):
        normalized = validate_select(sql)

        assert validate_select(normalized) == normalized

    def test_removes_trailing_semicolon(self):
        assert validate_select("SELECT 1;") == "SELECT 1"

    def test_keeps_string_literal_intact(self):
        assert "'a; DELETE FROM x'" in validate_select("SELECT 'a; DELETE FROM x' AS s")

    def test_drops_comments_so_nothing_can_escape_them(self):
        normalized = validate_select("SELECT 1 -- x */ ; DROP TABLE t")

        assert normalized == "SELECT 1"

    def test_accepts_other_dialect(self):
        assert validate_select("SELECT 1", dialect="databricks") == "SELECT 1"

    @pytest.mark.parametrize("sql", _gabarito_queries())
    def test_accepts_every_reference_query(self, sql):
        assert validate_select(sql)


class TestRejected:
    @pytest.mark.parametrize("sql", FORBIDDEN_STATEMENTS)
    def test_rejects_forbidden_statements(self, sql):
        with pytest.raises(UnsafeQueryError):
            validate_select(sql)

    @pytest.mark.parametrize("sql", MULTIPLE_STATEMENTS)
    def test_rejects_multiple_statements(self, sql):
        with pytest.raises(UnsafeQueryError, match="uma instrução"):
            validate_select(sql)

    @pytest.mark.parametrize("sql", HIDDEN_WRITES)
    def test_rejects_writes_anywhere_in_the_tree(self, sql):
        with pytest.raises(UnsafeQueryError, match="DELETE"):
            validate_select(sql)

    @pytest.mark.parametrize("sql", EMPTY)
    def test_rejects_empty_input(self, sql):
        with pytest.raises(UnsafeQueryError, match="vazia"):
            validate_select(sql)

    @pytest.mark.parametrize("sql", NOT_SQL)
    def test_rejects_unparseable_text(self, sql):
        with pytest.raises(UnsafeQueryError, match="interpretar"):
            validate_select(sql)

    @pytest.mark.parametrize("sql", FORBIDDEN_FUNCTIONS)
    def test_rejects_forbidden_functions(self, sql):
        with pytest.raises(UnsafeQueryError, match="Função não permitida"):
            validate_select(sql)


class TestMessages:
    def test_names_the_forbidden_command(self):
        with pytest.raises(UnsafeQueryError, match="DROP"):
            validate_select("DROP TABLE x")

    def test_names_command_fallback_keyword(self):
        with pytest.raises(UnsafeQueryError, match="VACUUM"):
            validate_select("VACUUM")

    def test_parser_message_has_no_terminal_escape_codes(self):
        with pytest.raises(UnsafeQueryError) as error:
            validate_select("olá, quais são os filmes?")

        assert "\x1b" not in str(error.value)

    def test_error_is_a_value_error(self):
        assert issubclass(UnsafeQueryError, ValueError)


class TestSelectInto:
    @pytest.mark.parametrize(
        "sql",
        ["SELECT 1 INTO x", "SELECT * INTO copia FROM dim_movies"],
        ids=["literal", "from-table"],
    )
    def test_rejects_select_into(self, sql):
        with pytest.raises(UnsafeQueryError, match="INTO"):
            validate_select(sql)


GOLD_TABLES = {
    "dim_movies",
    "fact_movies_performance",
    "dim_genres",
    "dim_people",
    "dim_companies",
    "dim_reviews",
    "movie_reviews",
    "bridge_movie_genre",
    "bridge_movie_person",
    "bridge_movie_company",
}


class TestTableAllowlist:
    def test_default_allowlist_is_the_ten_gold_tables(self):
        assert SQLITE_ALLOWED_TABLES == GOLD_TABLES
        assert "alembic_version" not in SQLITE_ALLOWED_TABLES
        assert {"main"} == SQLITE_ALLOWED_SCHEMAS

    @pytest.mark.parametrize(
        "sql",
        [
            pytest.param("SELECT * FROM filmes", id="unknown-table"),
            pytest.param("SELECT name FROM sqlite_master", id="sqlite-master"),
            pytest.param("SELECT * FROM alembic_version", id="alembic-version"),
            pytest.param(
                "SELECT m.titulo FROM dim_movies m JOIN segredo s ON s.id = m.sk_movie_id",
                id="unknown-table-in-join",
            ),
            pytest.param(
                "SELECT titulo FROM dim_movies WHERE sk_movie_id IN (SELECT id FROM sqlite_schema)",
                id="unknown-table-in-subquery",
            ),
        ],
    )
    def test_rejects_tables_outside_the_allowlist(self, sql):
        with pytest.raises(UnsafeQueryError, match="Tabela não permitida"):
            validate_select(sql)

    def test_error_lists_the_allowed_tables(self):
        with pytest.raises(UnsafeQueryError) as error:
            validate_select("SELECT * FROM filmes")

        message = str(error.value)
        assert "filmes" in message
        for table in GOLD_TABLES:
            assert table in message

    @pytest.mark.parametrize(
        "sql",
        [
            pytest.param("SELECT * FROM pragma_table_info('x')", id="pragma-table-info"),
            pytest.param("SELECT * FROM pragma_database_list", id="pragma-no-args"),
            pytest.param("SELECT * FROM generate_series(1, 10)", id="generate-series"),
            pytest.param(
                "SELECT j.value FROM dim_movies m, json_each(m.titulo) j", id="json-each-column"
            ),
            pytest.param(
                "SELECT value FROM json_each((SELECT group_concat(nome_genero) FROM dim_genres))",
                id="json-each-subquery",
            ),
            pytest.param(
                "SELECT j.value FROM dim_movies m, json_tree(m.titulo) j", id="json-tree-column"
            ),
        ],
    )
    def test_rejects_table_valued_functions(self, sql):
        with pytest.raises(UnsafeQueryError, match="valor de tabela|Tabela não permitida"):
            validate_select(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            pytest.param('SELECT value FROM json_each(\'["English", "French"]\')', id="json-each"),
            pytest.param("SELECT value FROM json_tree('{\"a\": 1}')", id="json-tree"),
            pytest.param("SELECT value FROM json_each('{\"a\": [1]}', '$.a')", id="json-each-path"),
            pytest.param(
                "SELECT titulo FROM dim_movies WHERE titulo NOT IN (SELECT nome_genero "
                "FROM dim_genres UNION ALL SELECT value FROM json_each('[\"x\"]'))",
                id="invalid-names-pattern",
            ),
        ],
    )
    def test_accepts_json_functions_over_string_literals(self, sql):
        assert validate_select(sql)

    def test_table_valued_function_message(self):
        with pytest.raises(UnsafeQueryError, match="valor de tabela"):
            validate_select("SELECT * FROM pragma_table_info('x')")

    @pytest.mark.parametrize(
        "sql",
        [
            pytest.param("SELECT titulo FROM main.dim_movies", id="main-prefix"),
            pytest.param("SELECT titulo FROM MAIN.DIM_MOVIES", id="upper-case"),
            pytest.param('SELECT titulo FROM "Dim_Movies"', id="quoted-mixed-case"),
        ],
    )
    def test_accepts_allowed_tables_ignoring_case(self, sql):
        assert validate_select(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            pytest.param("SELECT * FROM temp.x", id="temp-schema"),
            pytest.param("SELECT * FROM temp.dim_movies", id="temp-allowed-name"),
            pytest.param("SELECT * FROM other.dim_movies", id="attached-schema"),
            pytest.param("SELECT * FROM a.main.dim_movies", id="catalog-prefix"),
        ],
    )
    def test_rejects_schema_prefix_outside_allowed_schemas(self, sql):
        with pytest.raises(UnsafeQueryError, match="Schema não permitido"):
            validate_select(sql)

    def test_accepts_cte_name_that_is_not_a_table(self):
        sql = (
            "WITH top_filmes AS (SELECT titulo FROM dim_movies LIMIT 3) "
            "SELECT titulo FROM top_filmes"
        )

        assert validate_select(sql)

    def test_accepts_recursive_cte(self):
        sql = (
            "WITH RECURSIVE anos(a) AS "
            "(SELECT 2016 UNION ALL SELECT a + 1 FROM anos WHERE a < 2020) "
            "SELECT a FROM anos"
        )

        assert validate_select(sql)

    def test_rejects_cte_that_reads_a_forbidden_table(self):
        sql = "WITH s AS (SELECT name FROM sqlite_master) SELECT name FROM s"

        with pytest.raises(UnsafeQueryError, match="sqlite_master"):
            validate_select(sql)

    def test_cte_name_does_not_allow_a_schema_prefix(self):
        with pytest.raises(UnsafeQueryError):
            validate_select("WITH c AS (SELECT 1 AS a) SELECT a FROM temp.c")

    def test_custom_allowlist(self):
        tables = frozenset({"movies"})

        assert validate_select("SELECT * FROM movies", allowed_tables=tables)
        with pytest.raises(UnsafeQueryError):
            validate_select("SELECT * FROM dim_movies", allowed_tables=tables)

    def test_custom_schemas(self):
        schemas = frozenset({"gold"})

        assert validate_select("SELECT * FROM gold.dim_movies", allowed_schemas=schemas)
        with pytest.raises(UnsafeQueryError):
            validate_select("SELECT * FROM main.dim_movies", allowed_schemas=schemas)
