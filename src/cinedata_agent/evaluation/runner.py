"""Run the agent on the gabarito questions within the daily quota and judge each answer."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar

from rich.console import Console

from cinedata_agent.agent import Agent, ChatModel
from cinedata_agent.cache import AnswerCache, CachedAnswer
from cinedata_agent.config import Settings
from cinedata_agent.db.base import Database
from cinedata_agent.db.errors import DatabaseError
from cinedata_agent.evaluation.compare import ResultTable, results_agree
from cinedata_agent.evaluation.gabarito import EvalQuestion, Reference, judge
from cinedata_agent.evaluation.report import print_plan, print_run, write_reports
from cinedata_agent.llm.errors import OpenRouterError, QuotaExhaustedError
from cinedata_agent.llm.openrouter_account import QuotaInfo

EVAL_REFERENCE_DATE = "2026-10-01"
EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_INTERRUPTED = 3
STATUS_COMPLETE = "completa"
STATUS_INTERRUPTED = "interrompida"

T = TypeVar("T")


class CountingChatModel(ChatModel, Protocol):
    @property
    def requests_sent(self) -> int: ...


@dataclass(frozen=True)
class EvalOptions:
    ids: tuple[str, ...] | None
    limit: int | None
    sleep_seconds: float
    use_cache: bool
    dry_run: bool


@dataclass(frozen=True)
class EvalDependencies:
    settings: Settings
    # None in --dry-run, which never opens the database.
    database: Database | None
    reference_database: Database | None
    make_llm: Callable[[], CountingChatModel]
    get_quota: Callable[[], QuotaInfo]
    cache: AnswerCache
    sleep: Callable[[float], None]
    console: Console
    output_dir: Path
    now: Callable[[], datetime]


@dataclass(frozen=True)
class QuestionOutcome:
    id: str
    question: str
    approved: bool
    top1_ok: bool
    recall: float
    k: int
    value_ok: bool | None
    matched_role: str | None
    matched_label: str | None
    key_columns: list[str]
    matched_columns: list[str]
    model: str | None
    llm_calls: int
    requests: int
    elapsed_s: float
    from_cache: bool
    sql_executed: list[str]
    answer_text: str
    error: str | None
    reference_checks: dict[str, bool | None]


@dataclass(frozen=True)
class EvalRun:
    started_at: datetime
    finished_at: datetime
    reference_date: str
    status: str
    stop_reason: str | None
    options: dict[str, Any]
    questions: list[QuestionOutcome] = field(default_factory=list)


@dataclass(frozen=True)
class _AgentRun:
    actual: ResultTable | None
    model: str | None
    llm_calls: int
    requests: int
    sql_executed: list[str]
    answer_text: str
    error: str | None
    from_cache: bool


def evaluation_settings(**overrides: Any) -> Settings:
    """Settings with REFERENCE_DATE pinned to the date the gabarito was computed for."""
    return Settings(**{**overrides, "REFERENCE_DATE": EVAL_REFERENCE_DATE})


def run_evaluation(
    questions: tuple[EvalQuestion, ...], options: EvalOptions, deps: EvalDependencies
) -> int:
    try:
        selected = _select(questions, options)
    except ValueError as error:
        deps.console.print(str(error), style="red", markup=False)
        return EXIT_REFUSED
    plan = [(question, _cached(question, options, deps)) for question in selected]
    uncached = sum(1 for _, cached in plan if cached is None)
    if options.dry_run:
        print_plan(deps, plan, uncached)
        return EXIT_OK
    refusal = _initial_quota_refusal(deps, uncached) if uncached else None
    if refusal is not None:
        deps.console.print(refusal, style="red", markup=False)
        return EXIT_REFUSED

    started = deps.now()
    outcomes, stop_reason = _run_questions(plan, options, deps)
    run = EvalRun(
        started_at=started,
        finished_at=deps.now(),
        reference_date=deps.settings.reference_date.isoformat(),
        status=STATUS_COMPLETE if stop_reason is None else STATUS_INTERRUPTED,
        stop_reason=stop_reason,
        options={
            "ids": list(options.ids) if options.ids else None,
            "limit": options.limit,
            "sleep_seconds": options.sleep_seconds,
            "use_cache": options.use_cache,
        },
        questions=outcomes,
    )
    write_reports(run, deps.output_dir, deps.now())
    print_run(run, deps.console)
    return EXIT_OK if stop_reason is None else EXIT_INTERRUPTED


def _select(questions: tuple[EvalQuestion, ...], options: EvalOptions) -> list[EvalQuestion]:
    selected = list(questions)
    if options.ids:
        known = {question.id for question in questions}
        unknown = [question_id for question_id in options.ids if question_id not in known]
        if unknown:
            raise ValueError(f"Pergunta(s) inexistente(s) no gabarito: {', '.join(unknown)}.")
        selected = [question for question in questions if question.id in options.ids]
    if options.limit is not None:
        selected = selected[: options.limit]
    return selected


def _cached(
    question: EvalQuestion, options: EvalOptions, deps: EvalDependencies
) -> CachedAnswer | None:
    if not options.use_cache:
        return None
    return deps.cache.get(deps.cache.key_for(deps.settings, question.question))


def _initial_quota_refusal(deps: EvalDependencies, uncached: int) -> str | None:
    per_question = deps.settings.max_llm_calls_per_question
    try:
        quota = deps.get_quota()
    except OpenRouterError as error:
        return f"Avaliação recusada: não foi possível consultar a cota ({error})."
    needed = uncached * per_question
    if quota.remaining < needed:
        return (
            f"Avaliação recusada: restam {quota.remaining} requisições e {uncached} pergunta(s) "
            f"sem cache podem precisar de {needed} ({per_question} por pergunta). Use --ids ou "
            "--limit para rodar menos perguntas, ou espere o reset às 21h (Brasília)."
        )
    return None


def _quota_stop_reason(deps: EvalDependencies, question: EvalQuestion) -> str | None:
    cap = deps.settings.max_requests_per_question
    try:
        quota = deps.get_quota()
    except OpenRouterError as error:
        return f"Não foi possível consultar a cota antes de {question.id} ({error})."
    if quota.remaining < cap:
        return (
            f"Cota insuficiente antes de {question.id}: restam {quota.remaining} requisições, "
            f"menos que MAX_REQUESTS_PER_QUESTION={cap}."
        )
    return None


def _run_questions(
    plan: list[tuple[EvalQuestion, CachedAnswer | None]],
    options: EvalOptions,
    deps: EvalDependencies,
) -> tuple[list[QuestionOutcome], str | None]:
    outcomes: list[QuestionOutcome] = []
    llm: CountingChatModel | None = None
    asked_model = False
    for question, cached in plan:
        if cached is None:
            stop_reason = _quota_stop_reason(deps, question)
            if stop_reason is not None:
                return outcomes, stop_reason
            if asked_model and options.sleep_seconds > 0:
                deps.sleep(options.sleep_seconds)
            llm = llm or deps.make_llm()
            asked_model = True
        deps.console.print(f"{question.id}: {question.question}", style="dim", markup=False)
        try:
            outcomes.append(_evaluate(question, cached, llm, deps))
        except QuotaExhaustedError as error:
            return outcomes, str(error)
    return outcomes, None


def _evaluate(
    question: EvalQuestion,
    cached: CachedAnswer | None,
    llm: CountingChatModel | None,
    deps: EvalDependencies,
) -> QuestionOutcome:
    started = time.perf_counter()
    references, checks, reference_error = _reference_tables(question, deps)
    run = _from_cache(cached) if cached is not None else _ask_agent(question, llm, deps)
    verdict = judge(references, run.actual) if references else None
    comparison = verdict.comparison if verdict else None
    matched = verdict.matched if verdict else None
    return QuestionOutcome(
        id=question.id,
        question=question.question,
        approved=bool(verdict and verdict.approved and run.error is None),
        top1_ok=bool(comparison and comparison.top1_ok),
        recall=comparison.recall if comparison else 0.0,
        k=comparison.k if comparison else 0,
        value_ok=comparison.value_ok if comparison else None,
        matched_role=matched.role.value if matched else None,
        matched_label=matched.label if matched else None,
        key_columns=list(comparison.key_columns) if comparison else [],
        matched_columns=list(comparison.matched_columns) if comparison else [],
        model=run.model,
        llm_calls=run.llm_calls,
        requests=run.requests,
        elapsed_s=round(time.perf_counter() - started, 2),
        from_cache=run.from_cache,
        sql_executed=run.sql_executed,
        answer_text=run.answer_text,
        error=run.error or reference_error,
        reference_checks=checks,
    )


def _reference_tables(
    question: EvalQuestion, deps: EvalDependencies
) -> tuple[list[tuple[Reference, ResultTable]], dict[str, bool | None], str | None]:
    """Run every reference SQL locally; check it against the result saved in gabarito.json."""
    database = _require(deps.reference_database)
    tables: list[tuple[Reference, ResultTable]] = []
    checks: dict[str, bool | None] = {}
    errors: list[str] = []
    for reference in question.references:
        try:
            table = ResultTable.from_query_result(database.run_query(reference.sql))
        except DatabaseError as error:
            checks[reference.label] = False
            errors.append(f"referência '{reference.label}' falhou: {error}")
            continue
        tables.append((reference, table))
        checks[reference.label] = (
            results_agree(reference.stored, table) if reference.stored is not None else None
        )
    return tables, checks, "; ".join(errors) or None


def _from_cache(cached: CachedAnswer) -> _AgentRun:
    return _AgentRun(
        actual=ResultTable(tuple(cached.columns), tuple(tuple(row) for row in cached.rows)),
        model=cached.model_used,
        llm_calls=cached.llm_calls,
        requests=0,
        sql_executed=list(cached.sql_executed),
        answer_text=cached.text,
        error=None,
        from_cache=True,
    )


def _ask_agent(
    question: EvalQuestion, llm: CountingChatModel | None, deps: EvalDependencies
) -> _AgentRun:
    model = _require(llm)
    before = model.requests_sent
    agent = Agent(deps.settings, _require(deps.database), model)
    try:
        answer = agent.ask(question.question)
    except QuotaExhaustedError:
        raise
    except (OpenRouterError, DatabaseError) as error:
        return _AgentRun(
            actual=None,
            model=agent.last_run.model_used,
            llm_calls=agent.last_run.llm_calls,
            requests=model.requests_sent - before,
            sql_executed=[],
            answer_text="",
            error=f"{type(error).__name__}: {error}",
            from_cache=False,
        )
    requests = model.requests_sent - before
    key = deps.cache.key_for(deps.settings, question.question)
    deps.cache.put(key, question.question, answer, requests)
    actual = ResultTable.from_query_result(answer.last_result) if answer.last_result else None
    return _AgentRun(
        actual=actual,
        model=answer.model_used,
        llm_calls=answer.llm_calls,
        requests=requests,
        sql_executed=list(answer.sql_executed),
        answer_text=answer.text,
        error=None,
        from_cache=False,
    )


def _require(value: T | None) -> T:
    if value is None:
        raise RuntimeError("Evaluation dependency missing outside --dry-run.")
    return value
