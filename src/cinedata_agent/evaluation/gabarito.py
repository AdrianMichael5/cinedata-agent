"""Read eval/gabarito.json and decide which reference result each question is judged against.

In Q03, Q05 and Q14 the expected answer is the variant with a minimum filter; the main answer
is an accepted alternative. Elsewhere the main answer is expected and the variants are
alternatives. gabarito.json itself is never changed: this mapping lives here.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from cinedata_agent.evaluation.compare import Comparison, ResultTable, compare_results

EXPECTED_VARIANTS: dict[str, str] = {
    "Q03": "Com orçamento >= US$ 100 mil",
    "Q05": "Com >= 100 votos em cada base",
    "Q14": "Com >= 3 avaliações",
}


class Role(StrEnum):
    EXPECTED = "esperada"
    ALTERNATIVE = "alternativa"


@dataclass(frozen=True)
class Reference:
    label: str
    sql: str
    stored: ResultTable | None
    role: Role
    source: str  # "principal" or "variante"


@dataclass(frozen=True)
class EvalQuestion:
    id: str
    category: str
    question: str
    # The expected reference first, then the accepted alternatives.
    references: tuple[Reference, ...]


@dataclass(frozen=True)
class Verdict:
    approved: bool
    # The comparison with the reference that matched, or with the expected one.
    comparison: Comparison
    matched: Reference | None


def load_gabarito(path: Path) -> tuple[EvalQuestion, ...]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"O gabarito em {path} deve ser uma lista de perguntas.")
    return tuple(_question(item) for item in data)


def judge(
    references: Sequence[tuple[Reference, ResultTable]], actual: ResultTable | None
) -> Verdict:
    """Approve against the expected reference or, failing that, the first matching alternative."""
    comparisons = [(reference, compare_results(table, actual)) for reference, table in references]
    for reference, comparison in comparisons:
        if comparison.approved:
            return Verdict(approved=True, comparison=comparison, matched=reference)
    return Verdict(approved=False, comparison=comparisons[0][1], matched=None)


def _question(item: dict[str, Any]) -> EvalQuestion:
    question_id = item["id"]
    principal = _reference(item["principal"], "principal")
    variants = [_reference(variant, "variante") for variant in item.get("variantes", [])]
    expected_label = EXPECTED_VARIANTS.get(question_id)
    if expected_label is None:
        ordered = [principal, *variants]
    else:
        expected = next((ref for ref in variants if ref.label == expected_label), None)
        if expected is None:
            raise ValueError(
                f"{question_id}: a variante esperada '{expected_label}' não está no gabarito."
            )
        ordered = [expected, principal, *(ref for ref in variants if ref is not expected)]
    references = tuple(
        Reference(
            ref.label,
            ref.sql,
            ref.stored,
            Role.EXPECTED if position == 0 else Role.ALTERNATIVE,
            ref.source,
        )
        for position, ref in enumerate(ordered)
    )
    return EvalQuestion(
        id=question_id,
        category=item.get("categoria", ""),
        question=item["pergunta"],
        references=references,
    )


def _reference(item: dict[str, Any], source: str) -> Reference:
    stored = item.get("resultado")
    table = None
    if isinstance(stored, dict):
        table = ResultTable(
            columns=tuple(stored["colunas"]),
            rows=tuple(tuple(row) for row in stored["linhas"]),
        )
    return Reference(
        label=item["titulo"],
        sql=item["sql"].strip(),
        stored=table,
        role=Role.ALTERNATIVE,
        source=source,
    )
