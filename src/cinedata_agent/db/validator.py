"""Read-only SQL validation based on the sqlglot parser.

Only a single SELECT is accepted, optionally with WITH and UNION / INTERSECT / EXCEPT,
and it may only read allowlisted tables (or CTEs defined in the query itself).
The returned SQL is regenerated from the validated tree (without comments), so the text
that runs is exactly the text that was checked.
"""

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError

from cinedata_agent.db.errors import UnsafeQueryError
from cinedata_agent.db.schema import SQLITE_ALLOWED_SCHEMAS, SQLITE_ALLOWED_TABLES

ALLOWED_ROOTS: tuple[type[exp.Expr], ...] = (exp.Select, exp.SetOperation)

FORBIDDEN_NODES: dict[type[exp.Expr], str] = {
    exp.Insert: "INSERT",
    exp.Update: "UPDATE",
    exp.Delete: "DELETE",
    exp.Merge: "MERGE",
    exp.Drop: "DROP",
    exp.Create: "CREATE",
    exp.Alter: "ALTER",
    exp.Attach: "ATTACH",
    exp.Detach: "DETACH",
    exp.Pragma: "PRAGMA",
    exp.Analyze: "ANALYZE",
    exp.Transaction: "BEGIN",
    exp.Commit: "COMMIT",
    exp.Rollback: "ROLLBACK",
}

FORBIDDEN_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile"})

# Table-valued functions allowed in FROM, and only when every argument is a string literal.
LITERAL_TABLE_FUNCTIONS = frozenset({"json_each", "json_tree"})

READ_ONLY_HINT = "Apenas consultas de leitura (SELECT) são aceitas."
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def validate_select(
    sql: str,
    dialect: str = "sqlite",
    allowed_tables: frozenset[str] = SQLITE_ALLOWED_TABLES,
    allowed_schemas: frozenset[str] = SQLITE_ALLOWED_SCHEMAS,
) -> str:
    """Return the normalized SQL or raise UnsafeQueryError with a reason in Portuguese."""
    tree = _parse_single_statement(sql, dialect)
    _reject_forbidden_nodes(tree)
    if not isinstance(tree, ALLOWED_ROOTS):
        raise UnsafeQueryError(
            "A consulta deve ser um SELECT, com ou sem WITH "
            f"(UNION, INTERSECT e EXCEPT são aceitos). {READ_ONLY_HINT}"
        )
    if tree.find(exp.Into) is not None:
        raise UnsafeQueryError(
            f"SELECT ... INTO não é permitido: ele cria uma tabela. {READ_ONLY_HINT}"
        )
    _check_table_references(tree, allowed_tables, allowed_schemas)
    return tree.sql(dialect=dialect, comments=False)


def defined_cte_names(sql: str, dialect: str = "sqlite") -> frozenset[str]:
    """Lower-case names of every CTE defined in an already validated query."""
    tree = _parse_single_statement(sql, dialect)
    return frozenset(cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE))


def _parse_single_statement(sql: str, dialect: str) -> exp.Expr:
    try:
        parsed = sqlglot.parse(sql, dialect=dialect)
    except SqlglotError as error:
        raise UnsafeQueryError(f"Não foi possível interpretar a SQL: {_describe(error)}") from error

    # Empty statements (";;") come back as None; a comment after ";" comes back as Semicolon.
    statements = [
        node for node in parsed if node is not None and not isinstance(node, exp.Semicolon)
    ]
    if not statements:
        raise UnsafeQueryError("A consulta está vazia: envie uma única instrução SELECT.")
    if len(statements) > 1:
        raise UnsafeQueryError(
            f"Envie apenas uma instrução por vez; foram encontradas {len(statements)}."
        )
    return statements[0]


def _reject_forbidden_nodes(tree: exp.Expr) -> None:
    for node in tree.walk():
        keyword = _forbidden_keyword(node)
        if keyword:
            raise UnsafeQueryError(f"Instrução não permitida: {keyword}. {READ_ONLY_HINT}")

        function_name = _function_name(node)
        if function_name in FORBIDDEN_FUNCTIONS:
            raise UnsafeQueryError(f"Função não permitida: {function_name}.")


def _function_name(node: exp.Expr) -> str | None:
    # Unknown functions are Anonymous; known ones are typed nodes (exp.Upper...) named by sql_name.
    if isinstance(node, exp.Anonymous):
        return node.name.lower()
    if isinstance(node, exp.Func):
        return node.sql_name().lower()
    return None


def _forbidden_keyword(node: exp.Expr) -> str | None:
    # sqlglot falls back to Command for statements it does not model (VACUUM, REPLACE, EXPLAIN).
    if isinstance(node, exp.Command):
        return str(node.this).upper()
    for node_type, keyword in FORBIDDEN_NODES.items():
        if isinstance(node, node_type):
            return keyword
    return None


def _check_table_references(
    tree: exp.Expr, allowed_tables: frozenset[str], allowed_schemas: frozenset[str]
) -> None:
    tables = {name.lower() for name in allowed_tables}
    schemas = {name.lower() for name in allowed_schemas}

    for table in tree.find_all(exp.Table):
        # FROM pragma_table_info(...), json_each(...), generate_series(...): a function, not a name.
        if not isinstance(table.this, exp.Identifier):
            if _is_literal_json_function(table.this):
                continue
            raise UnsafeQueryError(
                "Funções com valor de tabela (pragma_*, generate_series...) não são permitidas; "
                "json_each e json_tree só aceitam texto literal. "
                "Consulte apenas as tabelas do catálogo."
            )
        if table.catalog or (table.db and table.db.lower() not in schemas):
            prefix = ".".join(part for part in (table.catalog, table.db) if part)
            raise UnsafeQueryError(
                f"Schema não permitido: {prefix}. Use o nome da tabela sem prefixo "
                f"(prefixos aceitos: {', '.join(sorted(allowed_schemas))})."
            )
        name = table.name.lower()
        if name in tables or (not table.db and name in _ctes_in_scope(table)):
            continue
        raise UnsafeQueryError(
            f"Tabela não permitida: {table.name}. "
            f"Tabelas disponíveis: {', '.join(sorted(allowed_tables))}."
        )


def _ctes_in_scope(table: exp.Table) -> set[str]:
    """CTE names visible from this table: only WITH clauses of its enclosing queries count.

    A CTE defined inside a sibling subquery is not visible here, and SQLite would resolve the
    name to the real table instead (e.g. FROM secret ... IN (WITH secret AS ...)).
    """
    names: set[str] = set()
    node = table.parent
    while node is not None:
        for value in node.args.values():
            if isinstance(value, exp.With):
                names.update(cte.alias_or_name.lower() for cte in value.expressions)
        node = node.parent
    return names


def _is_literal_json_function(node: exp.Expr) -> bool:
    # json_each('["English", ...]') only expands a constant list: it reads no data or schema.
    return (
        isinstance(node, exp.Anonymous)
        and node.name.lower() in LITERAL_TABLE_FUNCTIONS
        and bool(node.expressions)
        and all(isinstance(arg, exp.Literal) and arg.is_string for arg in node.expressions)
    )


def _describe(error: SqlglotError) -> str:
    """Turn a sqlglot error into a single clean line (no terminal color codes)."""
    if isinstance(error, ParseError) and error.errors:
        first = error.errors[0]
        description = (
            f"{first.get('description')} (linha {first.get('line')}, coluna {first.get('col')})"
        )
    else:
        description = str(error)
    return _ANSI_ESCAPE.sub("", description).strip()
