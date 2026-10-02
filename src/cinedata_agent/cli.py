"""Command-line interface for the CineData agent."""

import logging
from datetime import UTC, datetime, timedelta
from importlib import metadata
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from rich.text import Text

from cinedata_agent.agent import Agent
from cinedata_agent.config import Settings, get_settings
from cinedata_agent.db.errors import DatabaseError
from cinedata_agent.db.factory import get_database
from cinedata_agent.formatting import result_summary, result_table
from cinedata_agent.llm.client import LLMClient
from cinedata_agent.llm.errors import OpenRouterError
from cinedata_agent.llm.openrouter_account import (
    ModelsReport,
    ModelStatus,
    get_quota,
    list_free_tool_models,
    make_http_client,
    next_quota_reset,
)

app = typer.Typer(
    help="Agente Text-to-SQL do catálogo CineData.",
    no_args_is_help=True,
    add_completion=False,
)
# emoji=False: model ids such as "a/model:free: ..." would otherwise render ":free:" as an emoji.
console = Console(emoji=False)
err_console = Console(stderr=True, emoji=False)

DISTRIBUTION = "cinedata-agent"

STATUS_STYLES: dict[ModelStatus, str] = {
    ModelStatus.OK: "green",
    ModelStatus.ROUTER: "yellow",
    ModelStatus.NOT_FREE: "yellow",
    ModelStatus.NO_TOOLS: "red",
    ModelStatus.MISSING: "red",
}


def _fail(message: str) -> NoReturn:
    """Print an error (never as rich markup: SQLite messages may contain brackets) and exit 1."""
    err_console.print(message, style="red", markup=False)
    raise typer.Exit(code=1)


def _load_settings() -> Settings:
    """Read settings; on invalid values name the variables but never echo what was typed."""
    try:
        return get_settings()
    except ValidationError as error:
        names = sorted({str(issue["loc"][0]).upper() for issue in error.errors() if issue["loc"]})
        _fail(
            "Configuração inválida no .env ou nas variáveis de ambiente: "
            f"{', '.join(names)}. Corrija o valor e tente de novo."
        )


def _print_version(value: bool) -> None:
    if value:
        console.print(f"{DISTRIBUTION} {metadata.version(DISTRIBUTION)}", markup=False)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_print_version,
            is_eager=True,
            help="Mostra a versão instalada e sai.",
        ),
    ] = False,
) -> None:
    # sqlglot warns "contains unsupported syntax" on valid SQLite; the validator reports errors.
    logging.getLogger("sqlglot").setLevel(logging.ERROR)


@app.command(help="Responde a uma pergunta sobre o catálogo em linguagem natural.")
def ask(
    question: Annotated[str, typer.Argument(help="Pergunta em português.")],
    show_sql: Annotated[
        bool, typer.Option("--show-sql", help="Mostra as SQLs executadas.")
    ] = False,
) -> None:
    settings = _load_settings()
    if not question.strip():
        _fail("A pergunta está vazia: escreva o que quer saber sobre o catálogo.")
    try:
        # Database first: a missing file must fail before any request is spent.
        database = get_database(settings)
        llm = LLMClient(settings)
        answer = Agent(settings, database, llm).ask(question)
    except (DatabaseError, NotImplementedError, OpenRouterError) as error:
        _fail(str(error))

    console.print(answer.text, markup=False, highlight=False)
    console.print()
    console.print(
        f"Modelo: {answer.model_used or '-'} · Chamadas ao LLM: {answer.llm_calls} · "
        f"Requisições ao OpenRouter: {llm.requests_sent}",
        style="dim",
        markup=False,
    )
    if show_sql:
        _print_sql(answer.sql_executed)


def _print_sql(queries: list[str]) -> None:
    if not queries:
        console.print("Nenhuma SQL executada.", style="dim")
        return
    for position, query in enumerate(queries, start=1):
        console.print(f"\nSQL {position}:", style="bold")
        console.print(query, markup=False, highlight=False, soft_wrap=True)


@app.command(help="Executa uma consulta SQL somente leitura no banco.")
def sql(
    query: Annotated[str, typer.Argument(help="Instrução SELECT ou WITH.")],
) -> None:
    settings = _load_settings()
    try:
        result = get_database(settings).run_query(query)
    except (DatabaseError, NotImplementedError) as error:
        _fail(str(error))

    console.print(result_table(result))
    console.print(result_summary(result), markup=False)


@app.command(help="Mostra o uso da cota diária do OpenRouter (não gasta requisições).")
def quota() -> None:
    settings = _load_settings()
    try:
        with make_http_client() as client:
            info = get_quota(settings, client=client)
    except OpenRouterError as error:
        _fail(str(error))

    reset = next_quota_reset()
    console.print("Cota diária de modelos gratuitos do OpenRouter", style="bold")
    console.print(f"  Usadas:    {info.used}")
    console.print(f"  Limite:    {info.limit}")
    console.print(f"  Restantes: {info.remaining}")
    console.print(
        f"Próximo reset: {reset:%d/%m/%Y} às {reset:%H:%M} (horário de Brasília), "
        f"em {_format_duration(reset - datetime.now(UTC))}."
    )


@app.command(help="Lista os modelos gratuitos com suporte a tools (não gasta requisições).")
def models() -> None:
    settings = _load_settings()
    try:
        with make_http_client() as client:
            report = list_free_tool_models(settings, client=client)
    except OpenRouterError as error:
        _fail(str(error))

    console.print(_free_models_table(report))
    console.print(_diagnostics_table(report))
    if not any(d.status is ModelStatus.OK for d in report.diagnostics):
        err_console.print(
            "Nenhum modelo da LLM_MODELS aceita tools com certeza: ajuste a lista no .env.",
            style="yellow",
        )


def _free_models_table(report: ModelsReport) -> Table:
    table = Table(title=f"Modelos gratuitos com tools ({len(report.free_tool_models)})")
    table.add_column("Modelo")
    table.add_column("Contexto", justify="right")
    table.add_column("tool_choice")
    for model in report.free_tool_models:
        context = str(model.context_length) if model.context_length is not None else "?"
        table.add_row(Text(model.id), context, "sim" if model.supports_tool_choice else "não")
    return table


def _diagnostics_table(report: ModelsReport) -> Table:
    table = Table(title="Diagnóstico da LLM_MODELS (ordem de fallback)")
    table.add_column("#", justify="right")
    table.add_column("Modelo")
    table.add_column("Situação")
    for position, diagnosis in enumerate(report.diagnostics, start=1):
        table.add_row(
            str(position),
            Text(diagnosis.model_id),
            Text(diagnosis.message, style=STATUS_STYLES[diagnosis.status]),
        )
    return table


def _format_duration(delta: timedelta) -> str:
    total_minutes = max(int(delta.total_seconds() // 60), 0)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes:02d}min"
