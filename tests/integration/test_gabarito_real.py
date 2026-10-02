"""The evaluator against the real data/cinerocket.db. Run with: pytest -m integration"""

from pathlib import Path
from typing import Any

import pytest

from cinedata_agent.db.sqlite import SQLiteDatabase
from cinedata_agent.evaluation.compare import ResultTable, results_agree
from cinedata_agent.evaluation.gabarito import EvalQuestion, Role, judge, load_gabarito

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = PROJECT_ROOT / "data" / "cinerocket.db"
GABARITO = PROJECT_ROOT / "eval" / "gabarito.json"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not REAL_DB.is_file(), reason=f"{REAL_DB} not found"),
]


@pytest.fixture(scope="module")
def database() -> SQLiteDatabase:
    # Same limits as eval/run_eval.py: Q09's naive variant alone takes about 150 s.
    return SQLiteDatabase(REAL_DB, timeout_seconds=300, max_rows=10_000)


def _questions() -> list[Any]:
    return [pytest.param(question, id=question.id) for question in load_gabarito(GABARITO)]


@pytest.mark.parametrize("question", _questions())
def test_reference_sql_still_matches_the_saved_results(database, question: EvalQuestion):
    for reference in question.references:
        computed = ResultTable.from_query_result(database.run_query(reference.sql))

        assert reference.stored is not None
        assert results_agree(reference.stored, computed), reference.label


@pytest.mark.parametrize("question", _questions())
def test_the_expected_answer_is_approved_as_expected(database, question: EvalQuestion):
    tables = [
        (reference, ResultTable.from_query_result(database.run_query(reference.sql)))
        for reference in question.references
    ]

    verdict = judge(tables, tables[0][1])

    assert verdict.approved
    assert verdict.matched is not None
    assert verdict.matched.role is Role.EXPECTED
