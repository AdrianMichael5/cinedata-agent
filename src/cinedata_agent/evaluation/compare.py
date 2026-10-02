"""Compare the agent's last result with a reference result, without asking any model.

The key entity is the first text column of the reference (or the leading text columns, for
pairs such as actor and director); without text columns, the first column (a year, say).
"""

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from cinedata_agent.db.base import QueryResult

TOP_K = 5
RECALL_THRESHOLD = 0.8
VALUE_TOLERANCE = 0.01
AGREE_TOLERANCE = 1e-6
_DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class ResultTable:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    @classmethod
    def from_query_result(cls, result: QueryResult) -> "ResultTable":
        return cls(columns=tuple(result.columns), rows=tuple(tuple(row) for row in result.rows))


@dataclass(frozen=True)
class Comparison:
    top1_ok: bool
    recall: float
    k: int
    # Top-1 numbers within 1% (None when there is nothing numeric to compare); informative only.
    value_ok: bool | None
    key_columns: tuple[str, ...]
    matched_columns: tuple[str, ...]
    approved: bool


def normalize_key(value: Any) -> str:
    """Lowercase, no accents, collapsed spaces; 2016.0 and 2016 both become "2016"."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    decomposed = unicodedata.normalize("NFKD", str(value))
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.lower().split())


def compare_results(expected: ResultTable, actual: ResultTable | None) -> Comparison:
    key_indexes = _key_indexes(expected)
    key_columns = tuple(expected.columns[index] for index in key_indexes)
    k = min(TOP_K, len(expected.rows))
    matched = _match_columns(expected, key_indexes, actual) if actual and actual.rows else None
    if actual is None or matched is None:
        return Comparison(False, 0.0, k, None, key_columns, (), False)

    expected_keys = [_row_key(row, key_indexes) for row in expected.rows]
    actual_keys = [_row_key(row, matched) for row in actual.rows]
    top1_ok = bool(expected_keys) and actual_keys[0] == expected_keys[0]
    recall = len(set(expected_keys[:k]) & set(actual_keys[:k])) / k if k else 1.0
    return Comparison(
        top1_ok=top1_ok,
        recall=recall,
        k=k,
        value_ok=_top1_values_ok(expected, key_indexes, actual, matched),
        key_columns=key_columns,
        matched_columns=tuple(actual.columns[index] for index in matched),
        approved=top1_ok and recall >= RECALL_THRESHOLD,
    )


def results_agree(stored: ResultTable, computed: ResultTable) -> bool:
    """Whether a result saved in gabarito.json still matches what its SQL returns today."""
    if tuple(stored.columns) != tuple(computed.columns) or len(stored.rows) != len(computed.rows):
        return False
    for stored_row, computed_row in zip(stored.rows, computed.rows, strict=True):
        if len(stored_row) != len(computed_row):
            return False
        if not all(
            _same_value(left, right) for left, right in zip(stored_row, computed_row, strict=True)
        ):
            return False
    return True


def _key_indexes(table: ResultTable) -> tuple[int, ...]:
    text_columns = [index for index in range(len(table.columns)) if _is_text(table, index)]
    if not text_columns:
        return (0,)
    indexes = [text_columns[0]]
    while indexes[-1] + 1 in text_columns:
        indexes.append(indexes[-1] + 1)
    return tuple(indexes)


def _is_text(table: ResultTable, index: int) -> bool:
    values = [row[index] for row in table.rows if row[index] is not None]
    if not values or not all(isinstance(value, str) for value in values):
        return False
    return not all(_DATE_LIKE.match(value) for value in values)


def _match_columns(
    expected: ResultTable, key_indexes: tuple[int, ...], actual: ResultTable
) -> tuple[int, ...] | None:
    """For each key column, the unused agent column sharing the most normalized values."""
    chosen: list[int] = []
    for key_index in key_indexes:
        wanted = {normalize_key(row[key_index]) for row in expected.rows}
        best, best_overlap = None, 0
        for index in range(len(actual.columns)):
            if index in chosen:
                continue
            overlap = len(wanted & {normalize_key(row[index]) for row in actual.rows})
            if overlap > best_overlap:
                best, best_overlap = index, overlap
        if best is None:
            return None
        chosen.append(best)
    return tuple(chosen)


def _row_key(row: tuple[Any, ...], indexes: tuple[int, ...]) -> tuple[str, ...]:
    return tuple(normalize_key(row[index]) for index in indexes)


def _top1_values_ok(
    expected: ResultTable,
    key_indexes: tuple[int, ...],
    actual: ResultTable,
    matched: tuple[int, ...],
) -> bool | None:
    expected_numbers = _numbers(expected, exclude=key_indexes)
    actual_numbers = _numbers(actual, exclude=matched)
    if not expected_numbers or not actual_numbers:
        return None
    same_name = [
        (value, actual_numbers[name])
        for name, value in expected_numbers.items()
        if name in actual_numbers
    ]
    if same_name:
        return all(_close(left, right) for left, right in same_name)
    return any(
        _close(left, right)
        for left in expected_numbers.values()
        for right in actual_numbers.values()
    )


def _numbers(table: ResultTable, exclude: tuple[int, ...]) -> dict[str, float]:
    top = table.rows[0]
    return {
        normalize_key(table.columns[index]): float(top[index])
        for index in range(len(table.columns))
        if index not in exclude and _is_number(top[index])
    }


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=VALUE_TOLERANCE, abs_tol=1e-9)


def _same_value(left: Any, right: Any) -> bool:
    if _is_number(left) and _is_number(right):
        return math.isclose(float(left), float(right), rel_tol=AGREE_TOLERANCE, abs_tol=1e-6)
    return bool(left == right)
