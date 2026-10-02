"""Terminal table, JSON and Markdown reports of an evaluation run."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from cinedata_agent.llm.errors import OpenRouterError

if TYPE_CHECKING:
    from cinedata_agent.cache import CachedAnswer
    from cinedata_agent.evaluation.gabarito import EvalQuestion
    from cinedata_agent.evaluation.runner import EvalDependencies, EvalRun, QuestionOutcome

LATEST_MARKDOWN = "latest.md"
TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
VALUE_LABELS = {True: "dentro de 1%", False: "fora de 1%", None: "não comparável"}


def summarize_run(run: EvalRun) -> dict[str, int]:
    questions = run.questions
    approved = [item for item in questions if item.approved]
    failed = [item for item in questions if not item.approved]
    with_trap = [item for item in questions if item.has_trap]
    return {
        "total": len(questions),
        "approved": len(approved),
        "approved_expected": sum(1 for item in approved if item.matched_role == "esperada"),
        "approved_alternative": sum(1 for item in approved if item.matched_role == "alternativa"),
        "failed_trap": sum(1 for item in failed if item.matched_role == "armadilha"),
        "failed_no_match": sum(1 for item in failed if item.matched_role != "armadilha"),
        "trap_questions": len(with_trap),
        "traps_avoided": sum(1 for item in with_trap if item.matched_role != "armadilha"),
        "from_cache": sum(1 for item in questions if item.from_cache),
        "requests": sum(item.requests for item in questions),
        "errors": sum(1 for item in questions if item.error),
    }


def write_reports(run: EvalRun, output_dir: Path, now: datetime) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {**asdict(run), "summary": summarize_run(run)}
    json_path = output_dir / f"{now.strftime(TIMESTAMP_FORMAT)}.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    markdown_path = output_dir / LATEST_MARKDOWN
    markdown_path.write_text(render_markdown(run), encoding="utf-8")
    return json_path, markdown_path


def print_plan(
    deps: EvalDependencies, plan: list[tuple[EvalQuestion, CachedAnswer | None]], uncached: int
) -> None:
    console, settings = deps.console, deps.settings
    console.print(
        f"Avaliação (dry-run): REFERENCE_DATE {settings.reference_date.isoformat()}", style="bold"
    )
    table = Table(box=None)
    table.add_column("ID")
    table.add_column("Cache")
    table.add_column("Pergunta")
    for question, cached in plan:
        table.add_row(question.id, "sim" if cached else "não", Text(question.question))
    console.print(table)
    typical = uncached * settings.max_llm_calls_per_question
    worst = uncached * settings.max_requests_per_question
    console.print(
        f"{uncached} pergunta(s) sem cache: cerca de {2 * uncached} a {typical} requisições; "
        f"no máximo {worst} (MAX_REQUESTS_PER_QUESTION={settings.max_requests_per_question}).",
        markup=False,
    )
    try:
        quota = deps.get_quota()
    except OpenRouterError as error:
        console.print(f"Cota atual: indisponível ({error})", markup=False)
        return
    console.print(f"Cota atual: {quota.remaining} de {quota.limit} restantes.", markup=False)


def print_run(run: EvalRun, console: Console) -> None:
    table = Table(title=f"Avaliação {run.status}")
    for column in ("ID", "Aprovada", "Top 1", "Recall", "Bateu com", "Modelo", "Req.", "Tempo"):
        table.add_column(column)
    for item in run.questions:
        table.add_row(
            item.id,
            Text(_yes(item.approved), style="green" if item.approved else "red"),
            _yes(item.top1_ok),
            f"{item.recall:.2f}@{item.k}",
            Text(_matched(item)),
            Text(item.model or "-"),
            "cache" if item.from_cache else str(item.requests),
            f"{item.elapsed_s:.1f}s",
        )
    console.print(table)
    summary = summarize_run(run)
    console.print(
        f"Placar: {summary['approved']}/{summary['total']} aprovadas · armadilhas evitadas: "
        f"{summary['traps_avoided']} de {summary['trap_questions']}",
        style="bold",
    )
    if run.stop_reason:
        console.print(f"Interrompida: {run.stop_reason}", style="yellow", markup=False)


def render_markdown(run: EvalRun) -> str:
    summary = summarize_run(run)
    stop = f" ({run.stop_reason})" if run.stop_reason else ""
    lines = [
        "# Avaliação do agente CineData",
        "",
        f"- Execução: {run.started_at:%Y-%m-%d %H:%M} UTC · REFERENCE_DATE {run.reference_date}",
        f"- Status: {run.status}{stop}",
        f"- Placar: **{summary['approved']}/{summary['total']}** aprovadas",
        f"  - Aprovadas pela esperada: {summary['approved_expected']}",
        f"  - Aprovadas por alternativa válida: {summary['approved_alternative']}",
        f"  - Reprovadas por armadilha: {summary['failed_trap']}",
        f"  - Reprovadas sem correspondência: {summary['failed_no_match']}",
        f"- Armadilhas evitadas: {summary['traps_avoided']} de {summary['trap_questions']}",
        f"- Requisições ao OpenRouter: {summary['requests']} · do cache: {summary['from_cache']}",
        "",
        "| ID | Aprovada | Top 1 | Recall | Bateu com | Modelo | Requisições | Tempo |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {item.id} | {_yes(item.approved)} | {_yes(item.top1_ok)} | "
        f"{item.recall:.2f}@{item.k} | {_cell(_matched(item))} | {_cell(item.model or '-')} | "
        f"{'cache' if item.from_cache else item.requests} | {item.elapsed_s:.1f}s |"
        for item in run.questions
    ]
    for item in run.questions:
        lines += ["", *_question_details(item)]
    return "\n".join(lines) + "\n"


def _question_details(item: QuestionOutcome) -> list[str]:
    references = "; ".join(f"{label}: {_check(ok)}" for label, ok in item.reference_checks.items())
    lines = [
        f"## {item.id}: {item.question}",
        "",
        f"- Resultado: {'aprovada' if item.approved else 'reprovada'} "
        f"(bateu com: {_matched(item)})",
        f"- Top 1: {_yes(item.top1_ok)} · recall@{item.k}: {item.recall:.2f} · "
        f"valor do top 1: {VALUE_LABELS[item.value_ok]}",
        f"- Chave: {', '.join(item.key_columns) or '-'} → coluna do agente: "
        f"{', '.join(item.matched_columns) or 'nenhuma'}",
        f"- Referências: {references or '-'}",
    ]
    if item.value_check is not None:
        lines.append(f"- Valor do top 1 (checagem obrigatória): {VALUE_LABELS[item.value_check]}")
    if item.error:
        lines.append(f"- Erro: {item.error}")
    lines += ["", "SQL executada:", ""]
    lines += [f"```sql\n{sql}\n```" for sql in item.sql_executed] or ["(nenhuma)"]
    answer = item.answer_text.strip() or "(sem resposta)"
    lines += ["", "Resposta do agente:", "", *(f"> {line}" for line in answer.splitlines())]
    return lines


def _matched(item: QuestionOutcome) -> str:
    if item.matched_role is None:
        return "nenhuma"
    if item.matched_role == "armadilha":
        return f"caiu na armadilha: {item.matched_label}"
    return f"{item.matched_role}: {item.matched_label}"


def _check(ok: bool | None) -> str:
    if ok is None:
        return "sem resultado salvo"
    return "confere com o gabarito.json" if ok else "**não bate com o gabarito.json**"


def _yes(flag: bool) -> str:
    return "sim" if flag else "não"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")
