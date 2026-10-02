"""Read eval/gabarito.json and decide which reference result each question is judged against.

In Q03, Q05 and Q14 the expected answer is the variant with a minimum filter; elsewhere it is
the main answer. Every other reference is a valid alternative or a documented trap, as listed
in classification.py. gabarito.json itself is never changed.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cinedata_agent.evaluation.classification import REFERENCE_CLASSES, Role
from cinedata_agent.evaluation.compare import (
    Comparison,
    ResultTable,
    compare_results,
    top1_value_matches,
)

__all__ = ["EXPECTED_VARIANTS", "EvalQuestion", "Reference", "Role", "Verdict", "judge"]

EXPECTED_VARIANTS: dict[str, str] = {
    "Q03": "Com orçamento >= US$ 100 mil",
    "Q05": "Com >= 100 votos em cada base",
    "Q14": "Com >= 3 avaliações",
}
# Expected first, then valid readings; traps are only checked when nothing valid matched.
ROLE_ORDER = {Role.EXPECTED: 0, Role.VALID_ALTERNATIVE: 1, Role.TRAP: 2}


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
    # The expected reference first, then valid alternatives, then traps.
    references: tuple[Reference, ...]

    @property
    def has_trap(self) -> bool:
        return any(reference.role is Role.TRAP for reference in self.references)


@dataclass(frozen=True)
class Verdict:
    approved: bool
    # The comparison with the reference that matched, or with the expected one.
    comparison: Comparison
    matched: Reference | None
    # Mandatory top-1 value check against the expected reference; None when not required.
    value_check: bool | None = None

    @property
    def fell_into_trap(self) -> bool:
        return self.matched is not None and self.matched.role is Role.TRAP


def load_gabarito(
    path: Path, classes: Mapping[str, Mapping[str, Role]] = REFERENCE_CLASSES
) -> tuple[EvalQuestion, ...]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"O gabarito em {path} deve ser uma lista de perguntas.")
    return tuple(_question(item, classes.get(item["id"], {})) for item in data)


def judge(
    references: Sequence[tuple[Reference, ResultTable]],
    actual: ResultTable | None,
    value_column: str | None = None,
) -> Verdict:
    """Approve on the expected answer or a valid alternative; name the trap the answer fell in.

    With value_column, a reference only matches when the agent's top-1 row also carries that
    reference's top-1 value (within 1%, possibly scaled): the keys alone cannot tell them apart.
    """
    expected_reference, expected_table = references[0]
    value_check = top1_value_matches(expected_table, value_column, actual) if value_column else None
    ordered = sorted(references, key=lambda pair: ROLE_ORDER[pair[0].role])
    for reference, table in ordered:
        comparison = compare_results(table, actual)
        if comparison.approved and _value_ok(table, value_column, actual):
            approved = reference.role is not Role.TRAP
            return Verdict(approved, comparison, reference, value_check)
    return Verdict(False, compare_results(expected_table, actual), None, value_check)


def _value_ok(table: ResultTable, value_column: str | None, actual: ResultTable | None) -> bool:
    if value_column is None or value_column not in table.columns:
        return True
    return top1_value_matches(table, value_column, actual)


def _question(item: dict[str, Any], classes: Mapping[str, Role]) -> EvalQuestion:
    question_id = item["id"]
    principal = _reference(item["principal"], "principal")
    variants = [_reference(variant, "variante") for variant in item.get("variantes", [])]
    expected_label = EXPECTED_VARIANTS.get(question_id)
    if expected_label is None:
        expected, others = principal, variants
    else:
        found = next((ref for ref in variants if ref.label == expected_label), None)
        if found is None:
            raise ValueError(
                f"{question_id}: a variante esperada '{expected_label}' não está no gabarito."
            )
        expected, others = found, [principal, *(ref for ref in variants if ref is not found)]
    classified = [_with_role(ref, _class_of(question_id, ref, classes)) for ref in others]
    references = (
        _with_role(expected, Role.EXPECTED),
        *sorted(classified, key=lambda ref: ROLE_ORDER[ref.role]),
    )
    return EvalQuestion(
        id=question_id,
        category=item.get("categoria", ""),
        question=item["pergunta"],
        references=references,
    )


def _class_of(question_id: str, reference: Reference, classes: Mapping[str, Role]) -> Role:
    role = classes.get(reference.label)
    if role is None:
        raise ValueError(
            f"{question_id}: a referência '{reference.label}' não está classificada em "
            "REFERENCE_CLASSES (alternativa válida ou armadilha)."
        )
    return role


def _with_role(reference: Reference, role: Role) -> Reference:
    return Reference(reference.label, reference.sql, reference.stored, role, reference.source)


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
        role=Role.VALID_ALTERNATIVE,
        source=source,
    )
