"""Command line of eval/run_eval.py: parse options and wire the real dependencies."""

import argparse
import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

from cinedata_agent.cache import AnswerCache
from cinedata_agent.cli import ensure_utf8_streams
from cinedata_agent.config import Settings
from cinedata_agent.db.base import Database
from cinedata_agent.db.errors import DatabaseError
from cinedata_agent.db.sqlite import SQLiteDatabase
from cinedata_agent.evaluation.gabarito import load_gabarito
from cinedata_agent.evaluation.runner import (
    EXIT_REFUSED,
    EvalDependencies,
    EvalOptions,
    evaluation_settings,
    run_evaluation,
)
from cinedata_agent.llm.client import LLMClient
from cinedata_agent.llm.openrouter_account import get_quota

DEFAULT_SLEEP_SECONDS = 4.0
# Reference SQL may return more rows than the agent's MAX_ROWS (whole rankings, every genre).
REFERENCE_MAX_ROWS = 10_000
# Q09's references take 36 s and 148 s on the real database: more than the agent's limit.
REFERENCE_TIMEOUT_SECONDS = 300


def parse_args(argv: Sequence[str] | None) -> EvalOptions:
    parser = argparse.ArgumentParser(
        prog="python eval/run_eval.py",
        description="Avalia o agente nas perguntas do eval/gabarito.json.",
    )
    parser.add_argument("--ids", help="Perguntas separadas por vírgula, por exemplo Q01,Q04.")
    parser.add_argument("--limit", type=_positive_int, help="Avalia só as N primeiras.")
    parser.add_argument(
        "--sleep",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help="Segundos de pausa entre perguntas que chamam o modelo (limite de 20/min).",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="Ignora as respostas guardadas no cache."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Lista as perguntas, a estimativa de requisições e a cota, sem chamar o modelo.",
    )
    args = parser.parse_args(argv)
    ids = tuple(item.strip().upper() for item in (args.ids or "").split(",") if item.strip())
    return EvalOptions(
        ids=ids or None,
        limit=args.limit,
        sleep_seconds=args.sleep,
        use_cache=not args.no_cache,
        dry_run=args.dry_run,
    )


def main(argv: Sequence[str] | None = None, eval_dir: Path = Path("eval")) -> int:
    ensure_utf8_streams()
    logging.getLogger("sqlglot").setLevel(logging.ERROR)
    options = parse_args(argv)
    console = Console(emoji=False)
    settings = evaluation_settings()
    try:
        questions = load_gabarito(eval_dir / "gabarito.json")
        database, reference_database = _databases(settings, options.dry_run)
    except (OSError, ValueError, DatabaseError) as error:
        console.print(f"Não foi possível iniciar a avaliação: {error}", style="red", markup=False)
        return EXIT_REFUSED
    deps = EvalDependencies(
        settings=settings,
        database=database,
        reference_database=reference_database,
        make_llm=lambda: LLMClient(settings),
        get_quota=lambda: get_quota(settings),
        cache=AnswerCache.from_settings(settings),
        sleep=time.sleep,
        console=console,
        output_dir=eval_dir / "results",
        now=lambda: datetime.now(UTC),
    )
    return run_evaluation(questions, options, deps)


def _databases(settings: Settings, dry_run: bool) -> tuple[Database | None, Database | None]:
    if dry_run:
        return None, None
    agent_database = SQLiteDatabase(
        settings.db_path,
        timeout_seconds=settings.query_timeout_seconds,
        max_rows=settings.max_rows,
    )
    reference_database = SQLiteDatabase(
        settings.db_path,
        timeout_seconds=max(settings.query_timeout_seconds, REFERENCE_TIMEOUT_SECONDS),
        max_rows=REFERENCE_MAX_ROWS,
    )
    return agent_database, reference_database


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("use um número inteiro maior que zero")
    return number
