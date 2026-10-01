"""Terminal rendering of query results."""

from typing import Any

from rich.table import Table
from rich.text import Text

from cinedata_agent.db.base import QueryResult


def format_value(value: Any) -> Text:
    # Text (not str) so values such as "[Rec]" are never parsed as rich markup.
    if value is None:
        return Text("NULL", style="dim")
    return Text(str(value))


def result_table(result: QueryResult) -> Table:
    table = Table(show_lines=False)
    for column in result.columns:
        table.add_column(Text(column))
    for row in result.rows:
        table.add_row(*(format_value(value) for value in row))
    return table


def result_summary(result: QueryResult) -> str:
    summary = f"{len(result.rows)} linha(s) em {result.elapsed_ms:.0f} ms"
    if result.truncated:
        summary += (
            f" — resultado truncado: há mais linhas além das {len(result.rows)} exibidas "
            "(limite MAX_ROWS)"
        )
    return summary
