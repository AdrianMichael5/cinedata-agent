"""Prompt examples against the real data/cinerocket.db. Run with: pytest -m integration"""

from pathlib import Path

import pytest

from cinedata_agent.config import Settings
from cinedata_agent.db.sqlite import SQLiteDatabase
from cinedata_agent.prompts.builder import PromptExample, build_system_prompt, prompt_examples
from cinedata_agent.prompts.vocabulary import GENRES

REAL_DB = Path(__file__).resolve().parents[2] / "data" / "cinerocket.db"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not REAL_DB.is_file(), reason=f"{REAL_DB} not found"),
]


def _examples() -> list[PromptExample]:
    settings = Settings(_env_file=None, REFERENCE_DATE="2026-10-01")
    return prompt_examples(build_system_prompt(settings))


@pytest.fixture(scope="module")
def database() -> SQLiteDatabase:
    return SQLiteDatabase(REAL_DB, timeout_seconds=120, max_rows=200)


@pytest.mark.parametrize("example", _examples(), ids=lambda example: example.question[:40])
def test_example_sql_runs_and_returns_rows(database, example):
    result = database.run_query(example.sql)

    assert len(result.rows) >= 1, example.question


def test_prompt_genres_match_the_database(database):
    rows = database.run_query("SELECT nome_genero FROM dim_genres").rows

    assert {row[0] for row in rows} == set(GENRES)
