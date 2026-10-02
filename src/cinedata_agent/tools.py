"""The run_sql tool offered to the model and the compact text it gets back."""

from typing import Any

from openai.types.chat import ChatCompletionFunctionToolParam

from cinedata_agent.db.base import QueryResult

RUN_SQL_TOOL_NAME = "run_sql"
QUERY_ARGUMENT = "query"
# Rows sent back to the model per query: enough to answer rankings, small enough for the context.
TOOL_RESULT_MAX_ROWS = 50
# Long text columns (synopses) would bloat a message that is resent on every call.
TOOL_RESULT_MAX_CELL_CHARS = 200
COLUMN_SEPARATOR = " | "

RUN_SQL_TOOL: ChatCompletionFunctionToolParam = {
    "type": "function",
    "function": {
        "name": RUN_SQL_TOOL_NAME,
        "description": (
            "Executa uma consulta SQL somente leitura (uma única instrução SELECT ou "
            "WITH ... SELECT, dialeto SQLite) no banco do catálogo CineData e devolve as "
            "colunas e as linhas do resultado. Aceita o marcador {{NOMES_INVALIDOS}}. "
            "Em caso de erro, devolve a mensagem para você corrigir a SQL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                QUERY_ARGUMENT: {
                    "type": "string",
                    "description": "A instrução SQL completa a executar.",
                }
            },
            "required": [QUERY_ARGUMENT],
            "additionalProperties": False,
        },
    },
}


def format_tool_result(result: QueryResult) -> str:
    """Render a result as plain pipe-separated text: header, up to 50 rows and a row count."""
    shown = result.rows[:TOOL_RESULT_MAX_ROWS]
    lines = [_summary(result, len(shown)), "", COLUMN_SEPARATOR.join(result.columns)]
    lines.extend(COLUMN_SEPARATOR.join(_cell(value) for value in row) for row in shown)
    return "\n".join(lines)


def _summary(result: QueryResult, shown: int) -> str:
    total = len(result.rows)
    summary = f"Resultado: {total} linha(s)."
    if shown < total:
        summary += f" Mostrando as {shown} primeiras."
    if result.truncated:
        summary += (
            f" AVISO: resultado truncado no limite de {total} linhas (MAX_ROWS); "
            "há mais linhas no banco. Use agregação, filtros ou LIMIT."
        )
    return summary


def _cell(value: Any) -> str:
    if value is None:
        return "NULL"
    # Newlines inside a value would look like extra rows to the model.
    text = " ".join(str(value).split())
    if len(text) > TOOL_RESULT_MAX_CELL_CHARS:
        return text[: TOOL_RESULT_MAX_CELL_CHARS - 1] + "…"
    return text
