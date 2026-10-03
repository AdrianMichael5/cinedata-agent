from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fakes import FAKE_MODEL, FakeLLM, text_message, tool_call_message  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from cinedata_agent.api import create_app  # noqa: E402
from cinedata_agent.config import Settings  # noqa: E402
from cinedata_agent.db.sqlite import SQLiteDatabase  # noqa: E402
from cinedata_agent.llm.errors import (  # noqa: E402
    AllModelsFailedError,
    AuthenticationError,
    QuotaExhaustedError,
)

COUNT_SQL = "SELECT COUNT(*) AS total FROM dim_movies"
QUESTION = "Quantos filmes existem no catálogo?"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "REFERENCE_DATE": "2026-10-01",
        "cache_dir": tmp_path / "cache",
        "request_log_path": tmp_path / "logs" / "requests.jsonl",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_client(
    tmp_path: Path, sample_db: Path, *scripts: list[Any]
) -> tuple[TestClient, list[FakeLLM]]:
    llms: list[FakeLLM] = []
    pending = list(scripts)

    def make_llm() -> FakeLLM:
        llm = FakeLLM(pending.pop(0))
        llms.append(llm)
        return llm

    app = create_app(
        settings=make_settings(tmp_path),
        make_database=lambda: SQLiteDatabase(sample_db, timeout_seconds=5, max_rows=100),
        make_llm=make_llm,
    )
    return TestClient(app), llms


def answered() -> list[Any]:
    return [tool_call_message(COUNT_SQL), text_message("O catálogo tem 5 filmes.")]


class TestAsk:
    def test_returns_answer_sql_model_and_counts(self, tmp_path, sample_db):
        client, _ = make_client(tmp_path, sample_db, answered())

        response = client.post("/api/v1/ask", json={"question": QUESTION})

        assert response.status_code == 200
        body = response.json()
        assert body["answer"] == "O catálogo tem 5 filmes."
        assert len(body["sql"]) == 1
        assert "dim_movies" in body["sql"][0]
        assert body["model"] == FAKE_MODEL
        assert body["llm_calls"] == 2
        assert body["requests"] == 2
        assert body["from_cache"] is False
        assert body["warning"] is None

    def test_second_call_comes_from_the_cache_without_requests(self, tmp_path, sample_db):
        client, llms = make_client(tmp_path, sample_db, answered())
        client.post("/api/v1/ask", json={"question": QUESTION})

        body = client.post("/api/v1/ask", json={"question": QUESTION}).json()

        assert body["from_cache"] is True
        assert body["requests"] == 0
        assert body["answer"] == "O catálogo tem 5 filmes."
        assert len(llms) == 1

    def test_no_cache_asks_the_model_again(self, tmp_path, sample_db):
        client, llms = make_client(tmp_path, sample_db, answered(), answered())
        client.post("/api/v1/ask", json={"question": QUESTION})

        body = client.post("/api/v1/ask", json={"question": QUESTION, "no_cache": True}).json()

        assert body["from_cache"] is False
        assert len(llms) == 2

    @pytest.mark.parametrize("payload", [{"question": "   "}, {"question": ""}, {}])
    def test_empty_or_missing_question_is_422(self, tmp_path, sample_db, payload):
        client, llms = make_client(tmp_path, sample_db)

        response = client.post("/api/v1/ask", json=payload)

        assert response.status_code == 422
        assert llms == []

    def test_quota_exhausted_is_429(self, tmp_path, sample_db):
        error = QuotaExhaustedError("Cota esgotada.", reset_at=datetime(2026, 10, 4, tzinfo=UTC))
        client, _ = make_client(tmp_path, sample_db, [error])

        response = client.post("/api/v1/ask", json={"question": QUESTION})

        assert response.status_code == 429
        assert response.json()["detail"] == "Cota esgotada."

    def test_invalid_key_is_502_with_a_clear_message(self, tmp_path, sample_db):
        client, _ = make_client(tmp_path, sample_db, [AuthenticationError("Chave inválida.")])

        response = client.post("/api/v1/ask", json={"question": QUESTION})

        assert response.status_code == 502
        assert "Chave inválida." in response.json()["detail"]
        assert "OPENROUTER_API_KEY" in response.json()["detail"]

    def test_answer_with_warning_is_200_with_the_warning(self, tmp_path, sample_db):
        outage = AllModelsFailedError([("a/model:free", "HTTP 503")])
        client, _ = make_client(tmp_path, sample_db, [tool_call_message(COUNT_SQL), outage])

        response = client.post("/api/v1/ask", json={"question": QUESTION})

        assert response.status_code == 200
        assert response.json()["warning"]
        assert response.json()["from_cache"] is False


class TestHealth:
    def test_ok_when_the_database_answers(self, tmp_path, sample_db):
        client, _ = make_client(tmp_path, sample_db)

        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "db": "ok"}

    def test_reports_a_database_error(self, tmp_path):
        app = create_app(
            settings=make_settings(tmp_path),
            make_database=lambda: SQLiteDatabase(
                tmp_path / "missing.db", timeout_seconds=5, max_rows=10
            ),
            make_llm=lambda: FakeLLM([]),
        )

        response = TestClient(app).get("/health")

        assert response.status_code == 503
        assert response.json() == {"status": "erro", "db": "erro"}
