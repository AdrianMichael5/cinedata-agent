"""Command-line interface for the CineData agent."""

import logging
import sys
import time
from datetime import UTC, datetime, timedelta
from importlib import metadata
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from rich.text import Text

from cinedata_agent.agent import Agent, AgentAnswer, RunStats, SqlRecord
from cinedata_agent.cache import AnswerCache, CachedAnswer
from cinedata_agent.config import Settings, get_settings
from cinedata_agent.db.errors import DatabaseError
from cinedata_agent.db.factory import get_database
from cinedata_agent.formatting import SQL_WAIT_LABEL, SqlWait, result_summary, result_table
from cinedata_agent.llm.client import LLMClient
from cinedata_agent.llm.errors import OpenRouterError
from cinedata_agent.llm.openrouter_account import (
    BRASILIA_TZ,
    ModelsReport,
    ModelStatus,
    get_quota,
    list_free_tool_models,
    make_http_client,
    next_quota_reset,
)
from cinedata_agent.llm.request_log import (
    RequestLog,
    SqlTimingRecord,
    now_utc_iso,
    read_records,
    summarize,
)

app = typer.Typer(
    help="Agente Text-to-SQL do catálogo CineData.",
    no_args_is_help=True,
    add_completion=False,
)
# emoji=False: model ids such as "a/model:free: ..." would otherwise render ":free:" as an emoji.
console = Console(emoji=False)
err_console = Console(stderr=True, emoji=False)
logger = logging.getLogger(__name__)

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
    ensure_utf8_streams()
    # sqlglot warns "contains unsupported syntax" on valid SQLite; the validator reports errors.
    logging.getLogger("sqlglot").setLevel(logging.ERROR)


def ensure_utf8_streams() -> None:
    """Switch stdout/stderr to UTF-8 when they are not.

    Windows pipes and some consoles default to cp1252, which cannot encode characters models
    often write ("‑" U+2011, typographic quotes, emojis): rich then raised UnicodeEncodeError
    before printing anything, so the answer and the footer were lost.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if callable(reconfigure) and encoding != "utf8":
            reconfigure(encoding="utf-8", errors="replace")


@app.command(help="Responde a uma pergunta sobre o catálogo em linguagem natural.")
def ask(
    question: Annotated[str, typer.Argument(help="Pergunta em português.")],
    show_sql: Annotated[
        bool, typer.Option("--show-sql", help="Mostra as SQLs executadas.")
    ] = False,
    no_cache: Annotated[
        bool,
        typer.Option("--no-cache", help="Ignora a resposta guardada e consulta o modelo de novo."),
    ] = False,
) -> None:
    settings = _load_settings()
    if not question.strip():
        _fail("A pergunta está vazia: escreva o que quer saber sobre o catálogo.")
    cache = AnswerCache.from_settings(settings)
    try:
        key = cache.key_for(settings, question)
    except NotImplementedError as error:
        _fail_with_footer(str(error), None, None)

    cached = None if no_cache else cache.get(key)
    if cached is not None:
        _print_cached_answer(cached, show_sql)
        return

    answer, requests_sent = _run_agent(settings, question)
    console.print(answer.text, markup=False, highlight=False)
    console.print()
    console.print(
        _footer(answer.model_used, answer.llm_calls, requests_sent), style="dim", markup=False
    )
    if show_sql:
        _print_sql(answer.sql_log)
    log_sql_timing(settings, answer)
    # --no-cache skips the lookup only: a fresh complete answer still refreshes the entry.
    cache.put(key, question, answer, requests_sent)


def _run_agent(settings: Settings, question: str) -> tuple[AgentAnswer, int]:
    """Answer with the model; on any error print it with the footer and exit 1."""
    llm: LLMClient | None = None
    agent: Agent | None = None
    try:
        # Transient spinner on stderr: progress never mixes with the answer on stdout.
        with err_console.status("Preparando…") as status:

            def progress(text: str) -> None:
                status.update(SqlWait(time.monotonic) if text == SQL_WAIT_LABEL else text)

            # Database first: a missing file must fail before any request is spent.
            database = get_database(settings)
            llm = LLMClient(settings, progress=progress)
            agent = Agent(settings, database, llm, progress=progress)
            answer = agent.ask(question)
    except (DatabaseError, NotImplementedError, OpenRouterError) as error:
        _fail_with_footer(str(error), llm, agent)
    except Exception as error:
        # Last line of defense: a clear message and the footer, never a silent exit.
        logger.debug("Unexpected error in ask", exc_info=True)
        _fail_with_footer(f"Erro inesperado ({type(error).__name__}): {error}", llm, agent)
    return answer, llm.requests_sent


def _print_cached_answer(cached: CachedAnswer, show_sql: bool) -> None:
    created = cached.created_at.astimezone(BRASILIA_TZ)
    console.print(cached.text, markup=False, highlight=False)
    console.print()
    console.print(
        f"Modelo: {cached.model_used or '-'} · (do cache, 0 requisições) · resposta de "
        f"{created:%d/%m/%Y %H:%M} com {cached.llm_calls} chamada(s) ao LLM",
        style="dim",
        markup=False,
    )
    if show_sql:
        _print_sql([SqlRecord(sql=sql) for sql in cached.sql_executed])


def log_sql_timing(settings: Settings, answer: AgentAnswer) -> None:
    if not answer.sql_log:
        return
    RequestLog(settings.request_log_path).append(
        SqlTimingRecord(
            timestamp=now_utc_iso(),
            question_id=answer.question_id,
            sql_ms=answer.sql_ms,
            queries=len(answer.sql_log),
        )
    )


def _footer(model: str | None, llm_calls: int, requests_sent: int) -> str:
    return (
        f"Modelo: {model or '-'} · Chamadas ao LLM: {llm_calls} · "
        f"Requisições ao OpenRouter: {requests_sent}"
    )


def _fail_with_footer(message: str, llm: LLMClient | None, agent: Agent | None) -> NoReturn:
    """Print the error and what was spent so far, then exit 1."""
    stats = agent.last_run if agent is not None else RunStats(model_used=None, llm_calls=0)
    requests_sent = llm.requests_sent if llm is not None else 0
    err_console.print(message, style="red", markup=False)
    err_console.print(
        _footer(stats.model_used, stats.llm_calls, requests_sent), style="dim", markup=False
    )
    raise typer.Exit(code=1)


cache_app = typer.Typer(help="Gerencia o cache de respostas.", no_args_is_help=True)
app.add_typer(cache_app, name="cache")


@cache_app.command("clear", help="Apaga todas as respostas guardadas no cache.")
def cache_clear() -> None:
    settings = _load_settings()
    cache = AnswerCache.from_settings(settings)
    removed = cache.clear()
    console.print(f"{removed} entrada(s) removida(s) de {cache.directory}.", markup=False)


@app.command(help="Soma as requisições ao OpenRouter do log local (não gasta requisições).")
def requests(
    today: Annotated[
        bool,
        typer.Option(
            "--today", help="Só o dia atual da cota, que começa à 00h UTC (21h em Brasília)."
        ),
    ] = False,
) -> None:
    settings = _load_settings()
    records = read_records(settings.request_log_path)
    if not records:
        console.print(f"Nenhuma requisição registrada em {settings.request_log_path}.")
        return
    day = datetime.now(UTC).date() if today else None
    summary = summarize(records, day)
    title = (
        f"Requisições ao OpenRouter em {day.isoformat()} (dia da cota em UTC)"
        if day is not None
        else "Requisições ao OpenRouter (log inteiro)"
    )
    table = Table(title=title, box=None)
    table.add_column("Status HTTP")
    table.add_column("Requisições", justify="right")
    for status, count in summary.by_status.items():
        table.add_row(status, str(count))
    console.print(table)
    console.print(f"Total: {summary.total}", style="bold")


def _print_sql(records: list[SqlRecord]) -> None:
    """Print each query as it ran (macros expanded); rejected ones carry the reason."""
    if not records:
        console.print("Nenhuma SQL executada.", style="dim")
        return
    for position, record in enumerate(records, start=1):
        if record.rejection is None:
            console.print(f"\nSQL {position}:", style="bold")
        else:
            console.print(f"\nSQL {position} (rejeitada):", style="bold red")
            console.print(f"Motivo: {record.rejection}", style="red", markup=False)
        console.print(record.sql, markup=False, highlight=False, soft_wrap=True)


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
