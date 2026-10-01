"""Checks against the real data/cinerocket.db. Run with: pytest -m integration"""

import json
from pathlib import Path
from typing import Any

import pytest

from cinedata_agent.db.sqlite import SQLiteDatabase

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = PROJECT_ROOT / "data" / "cinerocket.db"
GABARITO = PROJECT_ROOT / "eval" / "gabarito.json"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not REAL_DB.is_file(), reason=f"{REAL_DB} not found"),
]


@pytest.fixture(scope="module")
def database() -> SQLiteDatabase:
    # Generous limits: some reference queries join bridge_movie_person twice (8-25 s).
    return SQLiteDatabase(REAL_DB, timeout_seconds=120, max_rows=1000)


def _principal_queries() -> list[Any]:
    questions = json.loads(GABARITO.read_text(encoding="utf-8"))
    return [pytest.param(q["principal"], id=q["id"]) for q in questions]


def _same_value(actual: Any, expected: Any) -> bool:
    if isinstance(expected, float):
        return actual == pytest.approx(expected, rel=1e-9)
    return actual == expected


def test_dim_movies_has_expected_row_count(database):
    result = database.run_query("SELECT COUNT(*) FROM dim_movies")

    assert result.rows == ((95645,),)


@pytest.mark.parametrize("reference", _principal_queries())
def test_normalized_reference_sql_matches_gabarito(database, reference):
    expected = reference["resultado"]

    result = database.run_query(reference["sql"])

    assert list(result.columns) == expected["colunas"]
    assert len(result.rows) == len(expected["linhas"])
    for actual_row, expected_row in zip(result.rows, expected["linhas"], strict=True):
        assert all(_same_value(a, e) for a, e in zip(actual_row, expected_row, strict=True)), (
            actual_row,
            expected_row,
        )
