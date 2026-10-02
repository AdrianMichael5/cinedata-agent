import json
import logging
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from cinedata_agent.config import Settings
from cinedata_agent.llm import client as client_module
from cinedata_agent.llm.client import LLMClient
from cinedata_agent.llm.errors import (
    AllModelsFailedError,
    AuthenticationError,
    OpenRouterAPIError,
    PaymentRequiredError,
    QuotaExhaustedError,
    RequestBudgetExceededError,
)

FAKE_KEY = "sk-or-v1-fake-key-for-client-tests"
MODELS = ["first/model:free", "second/model:free", "openrouter/free"]
MESSAGES: list[Any] = [{"role": "user", "content": "Quantos filmes existem?"}]
TOOLS: list[Any] = [
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": "Executa uma consulta SQL somente leitura.",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
            },
        },
    }
]

Handler = Callable[[httpx2.Request], httpx2.Response]


def completion_body(model: str, content: str | None = "Existem 5 filmes.") -> dict[str, Any]:
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


def error_body(message: str, code: int, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"message": message, "code": code}
    if metadata is not None:
        error["metadata"] = metadata
    return {"error": error}


PROVIDER_429 = error_body(
    "Provider returned error",
    429,
    {
        "raw": "first/model:free is temporarily rate-limited upstream. Please retry shortly.",
        "provider_name": "Chutes",
    },
)
QUOTA_429 = error_body(
    "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests",
    429,
    {"headers": {"X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "0"}},
)
NO_TOOLS_404 = error_body("No endpoints found that support tool use.", 404)


class ScriptedOpenRouter:
    """Fake chat endpoint: answers each model with a scripted response and records requests."""

    def __init__(self, responses: dict[str, httpx2.Response | Exception]):
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        self.requests.append(payload)
        outcome = self.responses[payload["model"]]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def models_called(self) -> list[str]:
        return [payload["model"] for payload in self.requests]


def ok(model: str) -> httpx2.Response:
    return httpx2.Response(200, json=completion_body(model))


def status(code: int, body: dict[str, Any] | str) -> httpx2.Response:
    if isinstance(body, str):
        return httpx2.Response(code, text=body)
    return httpx2.Response(code, json=body)


def make_client(server: Handler, models: list[str] | None = None, key: str = FAKE_KEY) -> LLMClient:
    settings = Settings(
        _env_file=None,
        openrouter_api_key=key,
        llm_models=models or MODELS,
    )
    http_client = httpx2.Client(transport=httpx2.MockTransport(server))
    return LLMClient(settings, http_client=http_client)


class TestSuccess:
    def test_first_model_answers(self):
        server = ScriptedOpenRouter({"first/model:free": ok("first/model:free")})
        llm = make_client(server)

        response = llm.complete(MESSAGES, TOOLS)

        assert response.message.content == "Existem 5 filmes."
        assert response.requested_model == "first/model:free"
        assert response.model_used == "first/model:free"
        assert server.models_called == ["first/model:free"]
        assert llm.requests_sent == 1
        assert [attempt.status for attempt in response.attempts] == [200]

    def test_model_used_comes_from_the_response_body(self):
        server = ScriptedOpenRouter({"openrouter/free": ok("meta-llama/llama-4-scout:free")})
        llm = make_client(server, models=["openrouter/free"])

        response = llm.complete(MESSAGES, TOOLS)

        assert response.requested_model == "openrouter/free"
        assert response.model_used == "meta-llama/llama-4-scout:free"

    def test_sends_tools_with_auto_choice_and_zero_temperature(self):
        server = ScriptedOpenRouter({"first/model:free": ok("first/model:free")})

        make_client(server).complete(MESSAGES, TOOLS)

        payload = server.requests[0]
        assert payload["messages"] == MESSAGES
        assert payload["tools"] == TOOLS
        assert payload["tool_choice"] == "auto"
        assert payload["temperature"] == 0

    def test_sends_key_as_bearer_to_the_configured_base_url(self):
        seen: list[httpx2.Request] = []

        def server(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            return ok("first/model:free")

        make_client(server).complete(MESSAGES, TOOLS)

        assert str(seen[0].url) == "https://openrouter.ai/api/v1/chat/completions"
        assert seen[0].headers["Authorization"] == f"Bearer {FAKE_KEY}"

    def test_returns_tool_calls(self):
        body = completion_body("first/model:free", content=None)
        body["choices"][0]["finish_reason"] = "tool_calls"
        body["choices"][0]["message"]["tool_calls"] = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "run_sql", "arguments": '{"sql": "SELECT 1"}'},
            }
        ]
        server = ScriptedOpenRouter({"first/model:free": httpx2.Response(200, json=body)})

        response = make_client(server).complete(MESSAGES, TOOLS)

        tool_calls = response.message.tool_calls
        assert tool_calls is not None
        assert tool_calls[0].function.name == "run_sql"


class TestSdkConfiguration:
    def test_openai_client_is_created_without_retries(self, monkeypatch):
        created: list[dict[str, Any]] = []
        real_openai = client_module.OpenAI

        def spy(**kwargs: Any) -> Any:
            created.append(kwargs)
            return real_openai(**kwargs)

        monkeypatch.setattr(client_module, "OpenAI", spy)

        make_client(ScriptedOpenRouter({}))

        assert len(created) == 1
        assert created[0]["max_retries"] == 0
        assert created[0]["timeout"] == 60
        assert created[0]["base_url"] == "https://openrouter.ai/api/v1"
        assert created[0]["api_key"] == FAKE_KEY

    def test_blank_key_fails_before_any_request(self):
        server = ScriptedOpenRouter({})

        with pytest.raises(AuthenticationError, match="OPENROUTER_API_KEY"):
            make_client(server, key="   ")

        assert server.requests == []


class TestProviderCapacity:
    def test_provider_429_moves_to_the_next_model(self):
        server = ScriptedOpenRouter(
            {
                "first/model:free": status(429, PROVIDER_429),
                "second/model:free": ok("second/model:free"),
            }
        )
        llm = make_client(server)

        response = llm.complete(MESSAGES, TOOLS)

        assert response.model_used == "second/model:free"
        assert server.models_called == ["first/model:free", "second/model:free"]
        assert llm.requests_sent == 2
        assert [attempt.status for attempt in response.attempts] == [429, 200]

    @pytest.mark.parametrize(
        "body",
        [
            error_body("Provider returned error", 429, {"provider_name": "Chutes"}),
            error_body("Provider returned error", 429, {"raw": "busy"}),
            error_body("Rate limited", 429, {"upstream_provider": "Chutes"}),
            error_body("Rate limited", 429, {"details": {"provider": "Chutes"}}),
            error_body("Rate limited", 429, {"attempts": [{"upstream_status": 429}]}),
            error_body("Model is temporarily rate-limited, retry shortly", 429),
            error_body("Upstream error from model host", 429),
        ],
        ids=[
            "provider_name",
            "raw",
            "upstream_key",
            "nested_provider_key",
            "key_inside_list",
            "temporarily",
            "msg",
        ],
    )
    def test_each_capacity_signal_is_recognized(self, body):
        server = ScriptedOpenRouter(
            {
                "first/model:free": status(429, body),
                "second/model:free": ok("second/model:free"),
            }
        )

        response = make_client(server).complete(MESSAGES, TOOLS)

        assert response.model_used == "second/model:free"

    def test_never_retries_the_same_model(self):
        server = ScriptedOpenRouter({model: status(429, PROVIDER_429) for model in MODELS})
        llm = make_client(server)

        with pytest.raises(AllModelsFailedError):
            llm.complete(MESSAGES, TOOLS)

        assert server.models_called == MODELS
        assert llm.requests_sent == 3

    def test_logs_raw_429_body_at_debug(self, caplog):
        caplog.set_level(logging.DEBUG, logger="cinedata_agent.llm.client")
        server = ScriptedOpenRouter(
            {
                "first/model:free": status(429, PROVIDER_429),
                "second/model:free": ok("second/model:free"),
            }
        )

        make_client(server).complete(MESSAGES, TOOLS)

        debug = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.DEBUG and r.name == "cinedata_agent.llm.client"
        ]
        assert any("temporarily rate-limited upstream" in line for line in debug)
        assert any("Chutes" in line for line in debug)


class TestDailyQuota:
    def test_quota_429_stops_without_trying_the_next_model(self):
        server = ScriptedOpenRouter(
            {
                "first/model:free": status(429, QUOTA_429),
                "second/model:free": ok("second/model:free"),
            }
        )
        llm = make_client(server)

        with pytest.raises(QuotaExhaustedError) as caught:
            llm.complete(MESSAGES, TOOLS)

        assert server.models_called == ["first/model:free"]
        assert llm.requests_sent == 1
        assert "21:00" in str(caught.value)
        assert caught.value.reset_at.hour == 21

    def test_null_provider_field_is_not_a_capacity_signal(self):
        body = error_body(
            "Rate limit exceeded: free-models-per-day",
            429,
            {"headers": {"X-RateLimit-Remaining": "0"}, "provider_name": None, "raw": None},
        )
        server = ScriptedOpenRouter(
            {
                "first/model:free": status(429, body),
                "second/model:free": ok("second/model:free"),
            }
        )
        llm = make_client(server)

        with pytest.raises(QuotaExhaustedError):
            llm.complete(MESSAGES, TOOLS)

        assert server.models_called == ["first/model:free"]
        assert llm.requests_sent == 1

    def test_429_without_json_body_is_treated_as_quota(self):
        server = ScriptedOpenRouter({"first/model:free": status(429, "Too Many Requests")})
        llm = make_client(server)

        with pytest.raises(QuotaExhaustedError):
            llm.complete(MESSAGES, TOOLS)

        assert llm.requests_sent == 1


class TestFatalErrors:
    def test_401_stops_and_points_to_the_env_file(self):
        server = ScriptedOpenRouter(
            {
                "first/model:free": status(401, error_body("User not found.", 401)),
                "second/model:free": ok("second/model:free"),
            }
        )
        llm = make_client(server)

        with pytest.raises(AuthenticationError, match=r"confira OPENROUTER_API_KEY no \.env"):
            llm.complete(MESSAGES, TOOLS)

        assert server.models_called == ["first/model:free"]
        assert llm.requests_sent == 1

    def test_402_stops_with_payment_required(self):
        server = ScriptedOpenRouter(
            {
                "first/model:free": status(402, error_body("Insufficient credits", 402)),
                "second/model:free": ok("second/model:free"),
            }
        )
        llm = make_client(server)

        with pytest.raises(PaymentRequiredError, match="saldo"):
            llm.complete(MESSAGES, TOOLS)

        assert server.models_called == ["first/model:free"]
        assert llm.requests_sent == 1

    @pytest.mark.parametrize(
        ("code", "message"),
        [
            (400, "messages: field required"),
            (400, "Invalid tool schema: parameter type 'foo' is not supported"),
            (403, "Input flagged by moderation"),
        ],
        ids=["bad_request", "tool_schema", "forbidden"],
    )
    def test_unexpected_4xx_stops_instead_of_burning_quota(self, code, message):
        server = ScriptedOpenRouter(
            {
                "first/model:free": status(code, error_body(message, code)),
                "second/model:free": ok("second/model:free"),
            }
        )
        llm = make_client(server)

        with pytest.raises(OpenRouterAPIError, match=str(code)):
            llm.complete(MESSAGES, TOOLS)

        assert server.models_called == ["first/model:free"]
        assert llm.requests_sent == 1


class TestModelUnavailable:
    @pytest.mark.parametrize(
        ("code", "message"),
        [
            (404, "No endpoints found that support tool use."),
            (404, "No endpoints found for first/model:free."),
            (400, "This model does not support tools"),
            (400, "first/model:free is not a valid model ID"),
        ],
    )
    def test_moves_to_the_next_model(self, code, message):
        server = ScriptedOpenRouter(
            {
                "first/model:free": status(code, error_body(message, code)),
                "second/model:free": ok("second/model:free"),
            }
        )
        llm = make_client(server)

        response = llm.complete(MESSAGES, TOOLS)

        assert response.model_used == "second/model:free"
        assert llm.requests_sent == 2


class TestTransientFailures:
    @pytest.mark.parametrize("code", [408, 500, 502, 503])
    def test_server_error_moves_to_the_next_model(self, code):
        server = ScriptedOpenRouter(
            {
                "first/model:free": status(code, error_body("Internal error", code)),
                "second/model:free": ok("second/model:free"),
            }
        )
        llm = make_client(server)

        response = llm.complete(MESSAGES, TOOLS)

        assert response.model_used == "second/model:free"
        assert server.models_called == ["first/model:free", "second/model:free"]
        assert llm.requests_sent == 2

    def test_timeout_moves_to_the_next_model(self):
        server = ScriptedOpenRouter(
            {
                "first/model:free": httpx2.ReadTimeout("timed out"),
                "second/model:free": ok("second/model:free"),
            }
        )
        llm = make_client(server)

        response = llm.complete(MESSAGES, TOOLS)

        assert response.model_used == "second/model:free"
        assert response.attempts[0].status is None
        assert "tempo" in response.attempts[0].outcome
        assert server.models_called == ["first/model:free", "second/model:free"]
        assert llm.requests_sent == 2

    def test_connection_failure_moves_to_the_next_model(self):
        server = ScriptedOpenRouter(
            {
                "first/model:free": httpx2.ConnectError("connection refused"),
                "second/model:free": ok("second/model:free"),
            }
        )
        llm = make_client(server)

        response = llm.complete(MESSAGES, TOOLS)

        assert response.model_used == "second/model:free"
        assert response.attempts[0].outcome == "falha de conexão"
        assert server.models_called == ["first/model:free", "second/model:free"]
        assert llm.requests_sent == 2

    def test_empty_choices_moves_to_the_next_model(self):
        body = completion_body("first/model:free")
        body["choices"] = []
        server = ScriptedOpenRouter(
            {
                "first/model:free": httpx2.Response(200, json=body),
                "second/model:free": ok("second/model:free"),
            }
        )
        llm = make_client(server)

        response = llm.complete(MESSAGES, TOOLS)

        assert response.model_used == "second/model:free"
        assert llm.requests_sent == 2


class TestMalformedResponses:
    @pytest.mark.parametrize(
        "response",
        [
            httpx2.Response(
                200, text="<html>gateway</html>", headers={"content-type": "text/html"}
            ),
            httpx2.Response(200, content=b"", headers={"content-type": "application/json"}),
            httpx2.Response(
                200, content=b"{not json", headers={"content-type": "application/json"}
            ),
            httpx2.Response(200, json=["not", "an", "object"]),
        ],
        ids=["html", "empty", "invalid_json", "json_list"],
    )
    def test_invalid_body_moves_to_the_next_model(self, response):
        server = ScriptedOpenRouter(
            {"first/model:free": response, "second/model:free": ok("second/model:free")}
        )
        llm = make_client(server)

        result = llm.complete(MESSAGES, TOOLS)

        assert result.model_used == "second/model:free"
        assert result.attempts[0].outcome == "resposta inválida do OpenRouter"
        assert llm.requests_sent == 2

    def test_invalid_bodies_everywhere_raise_a_typed_error(self):
        html = httpx2.Response(
            200, text="<html>portal</html>", headers={"content-type": "text/html"}
        )
        server = ScriptedOpenRouter({model: html for model in MODELS})

        with pytest.raises(AllModelsFailedError):
            make_client(server).complete(MESSAGES, TOOLS)

    def test_choice_without_message_is_not_ok(self):
        body = completion_body("first/model:free")
        body["choices"] = [{"index": 0, "finish_reason": "stop"}]
        server = ScriptedOpenRouter(
            {
                "first/model:free": httpx2.Response(200, json=body),
                "second/model:free": ok("second/model:free"),
            }
        )

        response = make_client(server).complete(MESSAGES, TOOLS)

        assert response.model_used == "second/model:free"
        assert response.attempts[0].outcome != "ok"

    def test_error_inside_a_200_body_is_reported(self):
        body = {"error": {"message": "Provider returned error", "code": 502}}
        server = ScriptedOpenRouter(
            {
                "first/model:free": httpx2.Response(200, json=body),
                "second/model:free": ok("second/model:free"),
            }
        )

        response = make_client(server).complete(MESSAGES, TOOLS)

        assert response.model_used == "second/model:free"
        assert "502" in response.attempts[0].outcome
        assert "Provider returned error" in response.attempts[0].outcome


class TestAllModelsFailed:
    def test_lists_the_reason_of_each_attempt(self):
        server = ScriptedOpenRouter(
            {
                "first/model:free": status(429, PROVIDER_429),
                "second/model:free": status(404, NO_TOOLS_404),
                "openrouter/free": status(503, error_body("Service unavailable", 503)),
            }
        )
        llm = make_client(server)

        with pytest.raises(AllModelsFailedError) as caught:
            llm.complete(MESSAGES, TOOLS)

        error = caught.value
        assert [model for model, _ in error.reasons] == MODELS
        message = str(error)
        for model in MODELS:
            assert model in message
        for code in ("429", "404", "503"):
            assert code in message
        assert llm.requests_sent == 3


class TestRequestBudget:
    def test_stops_when_the_budget_is_spent_mid_fallback(self):
        server = ScriptedOpenRouter({model: status(429, PROVIDER_429) for model in MODELS})
        llm = make_client(server)

        with pytest.raises(RequestBudgetExceededError) as caught:
            llm.complete(MESSAGES, TOOLS, max_requests=2)

        assert server.models_called == MODELS[:2]
        assert llm.requests_sent == 2
        assert caught.value.used == 2
        assert [model for model, _ in caught.value.reasons] == MODELS[:2]
        message = str(caught.value)
        assert "2 requisição(ões)" in message
        assert "MAX_REQUESTS_PER_QUESTION" in message
        assert "HTTP 429" in message

    def test_zero_budget_sends_nothing(self):
        server = ScriptedOpenRouter({"first/model:free": ok("first/model:free")})
        llm = make_client(server)

        with pytest.raises(RequestBudgetExceededError):
            llm.complete(MESSAGES, TOOLS, max_requests=0)

        assert server.requests == []
        assert llm.requests_sent == 0

    def test_budget_is_not_an_error_when_a_model_answers_in_time(self):
        server = ScriptedOpenRouter(
            {
                "first/model:free": status(429, PROVIDER_429),
                "second/model:free": ok("second/model:free"),
            }
        )

        response = make_client(server).complete(MESSAGES, TOOLS, max_requests=2)

        assert response.model_used == "second/model:free"
        assert len(response.attempts) == 2

    def test_all_models_failed_wins_when_the_list_ends_first(self):
        server = ScriptedOpenRouter({model: status(429, PROVIDER_429) for model in MODELS})

        with pytest.raises(AllModelsFailedError):
            make_client(server).complete(MESSAGES, TOOLS, max_requests=10)


class TestRequestCounter:
    def test_accumulates_across_calls(self):
        server = ScriptedOpenRouter(
            {
                "first/model:free": status(429, PROVIDER_429),
                "second/model:free": ok("second/model:free"),
            }
        )
        llm = make_client(server)

        llm.complete(MESSAGES, TOOLS)
        llm.complete(MESSAGES, TOOLS)

        assert llm.requests_sent == 4
        assert len(server.requests) == 4


class TestLogging:
    def test_logs_requested_and_responding_model_status_and_time(self, caplog):
        caplog.set_level(logging.INFO, logger="cinedata_agent.llm.client")
        server = ScriptedOpenRouter(
            {
                "first/model:free": status(503, error_body("Service unavailable", 503)),
                "second/model:free": ok("vendor/second-model-2026:free"),
            }
        )

        make_client(server).complete(MESSAGES, TOOLS)

        info = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.INFO and r.name == "cinedata_agent.llm.client"
        ]
        assert len(info) == 2
        assert "first/model:free" in info[0] and "503" in info[0] and "ms" in info[0]
        assert "second/model:free" in info[1]
        assert "vendor/second-model-2026:free" in info[1]
        assert "200" in info[1]

    def test_never_logs_or_raises_the_key(self, caplog):
        caplog.set_level(logging.DEBUG)
        server = ScriptedOpenRouter(
            {
                "first/model:free": status(429, PROVIDER_429),
                "second/model:free": status(401, error_body("User not found.", 401)),
            }
        )

        with pytest.raises(AuthenticationError) as caught:
            make_client(server).complete(MESSAGES, TOOLS)

        assert FAKE_KEY not in caplog.text
        assert FAKE_KEY not in str(caught.value)
