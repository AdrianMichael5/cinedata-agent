"""MAX_REQUESTS_PER_QUESTION end to end: the real Agent and LLMClient against a fake OpenRouter."""

import json
from typing import Any

import httpx2
import pytest

from cinedata_agent.agent import Agent
from cinedata_agent.config import Settings
from cinedata_agent.db.sqlite import SQLiteDatabase
from cinedata_agent.llm.client import LLMClient
from cinedata_agent.llm.errors import RequestBudgetExceededError

MODELS = [f"vendor/model-{index}:free" for index in range(1, 9)]
QUESTION = "Quantos filmes existem no catálogo?"
COUNT_SQL = "SELECT COUNT(*) AS total FROM dim_movies"
PROVIDER_429 = {
    "error": {
        "message": "Provider returned error",
        "code": 429,
        "metadata": {"provider_name": "Chutes", "raw": "temporarily rate-limited upstream"},
    }
}


def completion(message: dict[str, Any], model: str = MODELS[0]) -> httpx2.Response:
    body = {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
    }
    return httpx2.Response(200, json=body)


def tool_call(sql: str) -> httpx2.Response:
    return completion(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "run_sql", "arguments": json.dumps({"query": sql})},
                }
            ],
        }
    )


def text(content: str) -> httpx2.Response:
    return completion({"role": "assistant", "content": content})


def busy() -> httpx2.Response:
    return httpx2.Response(429, json=PROVIDER_429)


class SequencedOpenRouter:
    """Answers requests in arrival order, whatever model they ask for; busy once the list ends."""

    def __init__(self, responses: list[httpx2.Response]) -> None:
        self.responses = list(responses)
        self.models: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.models.append(json.loads(request.content)["model"])
        return self.responses.pop(0) if self.responses else busy()


@pytest.fixture
def run(sample_db):
    def run_question(server: SequencedOpenRouter, **overrides: Any):
        values: dict[str, Any] = {
            "openrouter_api_key": "sk-or-v1-fake-key-for-budget-tests",
            "llm_models": MODELS,
            "REFERENCE_DATE": "2026-10-01",
        }
        values.update(overrides)
        settings = Settings(_env_file=None, **values)
        http_client = httpx2.Client(transport=httpx2.MockTransport(server))
        llm = LLMClient(settings, http_client=http_client)
        db = SQLiteDatabase(sample_db, timeout_seconds=5, max_rows=100)
        return Agent(settings, db, llm).ask(QUESTION), llm

    return run_question


def test_default_cap_is_six_requests(run):
    server = SequencedOpenRouter([])

    with pytest.raises(RequestBudgetExceededError) as caught:
        run(server)

    assert len(server.models) == 6
    assert server.models == MODELS[:6]
    assert caught.value.used == 6
    assert "6 requisição(ões)" in str(caught.value)
    assert "HTTP 429" in str(caught.value)


def test_sql_error_then_fallback_stops_at_the_cap(run):
    server = SequencedOpenRouter([tool_call("SELECT nome FROM dim_movies")])

    with pytest.raises(RequestBudgetExceededError) as caught:
        run(server)

    assert len(server.models) == 6
    # Call 1 used one request; call 2 got the remaining five, never more.
    assert server.models == [MODELS[0], *MODELS[:5]]
    assert caught.value.used == 6


def test_cap_reached_after_a_result_returns_the_python_answer(run):
    server = SequencedOpenRouter([tool_call(COUNT_SQL)])

    answer, llm = run(server)

    assert len(server.models) == 6
    assert llm.requests_sent == 6
    assert "MAX_REQUESTS_PER_QUESTION" in answer.text
    assert "\ntotal\n5" in answer.text
    assert answer.sql_executed == [COUNT_SQL]
    assert answer.llm_calls == 1


def test_normal_question_still_takes_two_requests(run):
    server = SequencedOpenRouter([tool_call(COUNT_SQL), text("O catálogo tem 5 filmes.")])

    answer, llm = run(server)

    assert answer.text == "O catálogo tem 5 filmes."
    assert len(server.models) == 2
    assert llm.requests_sent == 2


def test_cap_comes_from_settings(run):
    server = SequencedOpenRouter([])

    with pytest.raises(RequestBudgetExceededError):
        run(server, max_requests_per_question=3)

    assert len(server.models) == 3
