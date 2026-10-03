"""Terminal rendering of query results."""

from collections.abc import Callable
from typing import Any

from rich.table import Table
from rich.text import Text

from cinedata_agent.db.base import QueryResult

SQL_WAIT_LABEL = "Executando SQL…"
SQL_WAIT_HINT = "consultas com duas junções de pessoas podem levar até 1 minuto"
SQL_WAIT_HINT_SECONDS = 10


def sql_wait_text(elapsed: float) -> str:
    seconds = int(elapsed)
    if elapsed < SQL_WAIT_HINT_SECONDS:
        return f"{SQL_WAIT_LABEL} ({SQL_WAIT_HINT}) {seconds} s"
    return f"{SQL_WAIT_LABEL} {seconds} s"


class SqlWait:
    """Rich renders this on every refresh, so the timer keeps moving while the query runs."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._started = clock()

    def __rich__(self) -> Text:
        return Text(sql_wait_text(self._clock() - self._started))


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
