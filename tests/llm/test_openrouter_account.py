import logging
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from cinedata_agent.config import Settings
from cinedata_agent.llm import openrouter_account as account_module
from cinedata_agent.llm.errors import AuthenticationError, OpenRouterAPIError
from cinedata_agent.llm.openrouter_account import (
    HTTP_TIMEOUT_SECONDS,
    INVALID_KEY_MESSAGE,
    FreeToolModel,
    ModelStatus,
    QuotaInfo,
    get_quota,
    list_free_tool_models,
    make_http_client,
    next_quota_reset,
)

FAKE_KEY = "sk-or-v1-fake-key-0123456789"
BRASILIA = ZoneInfo("America/Sao_Paulo")

QUOTA_BLOCK = {"used": 12, "limit": 50, "remaining": 38}

CATALOG = {
    "data": [
        {
            "id": "z-ai/glm-5.2:free",
            "context_length": 128000,
            "supported_parameters": ["tools", "tool_choice", "temperature"],
        },
        {
            "id": "nvidia/nemotron-3.5-lightning:free",
            "context_length": 131072,
            "supported_parameters": ["tools", "max_tokens"],
        },
        {
            "id": "google/gemma-4-26b-a4b-it:free",
            "context_length": 96000,
            "supported_parameters": ["temperature", "max_tokens"],
        },
        {
            "id": "openai/gpt-5:paid",
            "context_length": 400000,
            "supported_parameters": ["tools", "tool_choice"],
        },
        {"id": "broken/model:free"},
    ]
}

Handler = Callable[[httpx.Request], httpx.Response]


def mock_client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def make_settings(monkeypatch, **env: str) -> Settings:
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def json_handler(payload: object, status: int = 200, seen: list | None = None) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=payload)

    return handler


class TestGetQuota:
    def test_reads_quota_wrapped_in_data(self, monkeypatch):
        seen: list[httpx.Request] = []
        payload = {"data": {"label": FAKE_KEY[:12], "free_model_daily_requests": QUOTA_BLOCK}}

        quota = get_quota(
            make_settings(monkeypatch), client=mock_client(json_handler(payload, seen=seen))
        )

        assert quota == QuotaInfo(used=12, limit=50, remaining=38)
        assert str(seen[0].url) == "https://openrouter.ai/api/v1/key"
        assert seen[0].method == "GET"
        assert seen[0].headers["Authorization"] == f"Bearer {FAKE_KEY}"

    def test_reads_quota_at_top_level(self, monkeypatch):
        payload = {"free_model_daily_requests": QUOTA_BLOCK}

        quota = get_quota(make_settings(monkeypatch), client=mock_client(json_handler(payload)))

        assert quota.remaining == 38

    def test_respects_custom_base_url(self, monkeypatch):
        seen: list[httpx.Request] = []
        settings = make_settings(monkeypatch, OPENROUTER_BASE_URL="https://example.test/api/v1/")
        payload = {"free_model_daily_requests": QUOTA_BLOCK}

        get_quota(settings, client=mock_client(json_handler(payload, seen=seen)))

        assert str(seen[0].url) == "https://example.test/api/v1/key"

    def test_401_raises_clear_message_without_the_key(self, monkeypatch):
        handler = json_handler({"error": {"message": "No auth credentials found"}}, status=401)

        with pytest.raises(AuthenticationError) as error:
            get_quota(make_settings(monkeypatch), client=mock_client(handler))

        assert str(error.value) == INVALID_KEY_MESSAGE
        assert (
            INVALID_KEY_MESSAGE == "Chave inválida ou ausente: confira OPENROUTER_API_KEY no .env"
        )
        assert FAKE_KEY not in str(error.value)

    def test_missing_key_fails_before_any_request(self, monkeypatch):
        seen: list[httpx.Request] = []
        monkeypatch.setenv("OPENROUTER_API_KEY", "")
        settings = Settings(_env_file=None)

        with pytest.raises(AuthenticationError, match="OPENROUTER_API_KEY"):
            get_quota(settings, client=mock_client(json_handler({}, seen=seen)))

        assert seen == []

    @pytest.mark.parametrize(
        "payload",
        [
            {"data": {"label": "x"}},
            {"data": {"free_model_daily_requests": {"used": 1, "limit": 50}}},
            {"data": {"free_model_daily_requests": {"used": "1", "limit": 50, "remaining": 49}}},
            {"data": {"free_model_daily_requests": None}},
            ["not", "a", "dict"],
        ],
        ids=["field-absent", "remaining-absent", "not-int", "null", "not-object"],
    )
    def test_missing_or_malformed_field(self, monkeypatch, payload):
        with pytest.raises(OpenRouterAPIError, match="free_model_daily_requests"):
            get_quota(make_settings(monkeypatch), client=mock_client(json_handler(payload)))

    def test_server_error(self, monkeypatch):
        with pytest.raises(OpenRouterAPIError, match="500"):
            get_quota(make_settings(monkeypatch), client=mock_client(json_handler({}, status=500)))

    def test_invalid_json(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>oops</html>")

        with pytest.raises(OpenRouterAPIError, match="JSON"):
            get_quota(make_settings(monkeypatch), client=mock_client(handler))

    def test_network_error_does_not_leak_the_key(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        with pytest.raises(OpenRouterAPIError) as error:
            get_quota(make_settings(monkeypatch), client=mock_client(handler))

        assert FAKE_KEY not in str(error.value)

    def test_key_never_logged(self, monkeypatch, caplog):
        caplog.set_level(logging.DEBUG)
        payload = {"data": {"label": "x", "free_model_daily_requests": QUOTA_BLOCK}}

        get_quota(make_settings(monkeypatch), client=mock_client(json_handler(payload)))
        with pytest.raises(AuthenticationError):
            get_quota(make_settings(monkeypatch), client=mock_client(json_handler({}, status=401)))

        assert FAKE_KEY not in caplog.text


class TestListFreeToolModels:
    def test_keeps_only_free_models_with_tools(self, monkeypatch):
        report = list_free_tool_models(
            make_settings(monkeypatch), client=mock_client(json_handler(CATALOG))
        )

        assert report.free_tool_models == (
            FreeToolModel(
                id="nvidia/nemotron-3.5-lightning:free",
                context_length=131072,
                supports_tool_choice=False,
            ),
            FreeToolModel(id="z-ai/glm-5.2:free", context_length=128000, supports_tool_choice=True),
        )

    def test_models_endpoint_is_public(self, monkeypatch):
        seen: list[httpx.Request] = []

        list_free_tool_models(
            make_settings(monkeypatch), client=mock_client(json_handler(CATALOG, seen=seen))
        )

        assert str(seen[0].url) == "https://openrouter.ai/api/v1/models"
        assert "Authorization" not in seen[0].headers

    def test_diagnoses_each_configured_model(self, monkeypatch):
        settings = make_settings(
            monkeypatch,
            LLM_MODELS=(
                "z-ai/glm-5.2:free,google/gemma-4-26b-a4b-it:free,"
                "nao/existe:free,openai/gpt-5:paid,openrouter/free"
            ),
        )

        report = list_free_tool_models(settings, client=mock_client(json_handler(CATALOG)))

        statuses = {d.model_id: d.status for d in report.diagnostics}
        assert statuses == {
            "z-ai/glm-5.2:free": ModelStatus.OK,
            "google/gemma-4-26b-a4b-it:free": ModelStatus.NO_TOOLS,
            "nao/existe:free": ModelStatus.MISSING,
            "openai/gpt-5:paid": ModelStatus.NOT_FREE,
            "openrouter/free": ModelStatus.ROUTER,
        }
        assert [d.model_id for d in report.diagnostics] == settings.llm_models

    def test_router_label_mentions_no_tools_guarantee(self, monkeypatch):
        settings = make_settings(monkeypatch, LLM_MODELS="openrouter/free")

        report = list_free_tool_models(settings, client=mock_client(json_handler(CATALOG)))

        assert report.diagnostics[0].message == "roteador, sem garantia de tools"

    def test_missing_model_message(self, monkeypatch):
        settings = make_settings(monkeypatch, LLM_MODELS="nao/existe:free")

        report = list_free_tool_models(settings, client=mock_client(json_handler(CATALOG)))

        assert "não encontrado" in report.diagnostics[0].message

    @pytest.mark.parametrize(
        "payload",
        [{"models": []}, {"data": "x"}, ["x"]],
        ids=["no-data", "data-not-list", "not-object"],
    )
    def test_malformed_catalog(self, monkeypatch, payload):
        with pytest.raises(OpenRouterAPIError, match="/models"):
            list_free_tool_models(
                make_settings(monkeypatch), client=mock_client(json_handler(payload))
            )

    def test_server_error(self, monkeypatch):
        with pytest.raises(OpenRouterAPIError, match="503"):
            list_free_tool_models(
                make_settings(monkeypatch), client=mock_client(json_handler({}, status=503))
            )


class TestNextQuotaReset:
    @pytest.mark.parametrize(
        ("now_utc", "expected_brasilia"),
        [
            (datetime(2026, 10, 1, 18, 40, tzinfo=UTC), datetime(2026, 10, 1, 21, 0)),
            (datetime(2026, 10, 2, 1, 0, tzinfo=UTC), datetime(2026, 10, 2, 21, 0)),
            (datetime(2026, 10, 2, 0, 0, tzinfo=UTC), datetime(2026, 10, 2, 21, 0)),
            (datetime(2026, 12, 31, 23, 59, tzinfo=UTC), datetime(2026, 12, 31, 21, 0)),
        ],
        ids=["afternoon", "after-reset", "exactly-midnight", "year-end"],
    )
    def test_next_midnight_utc_in_brasilia(self, now_utc, expected_brasilia):
        reset = next_quota_reset(now_utc)

        assert reset.tzinfo is not None
        assert reset.replace(tzinfo=None) == expected_brasilia
        assert reset.astimezone(UTC).hour == 0
        assert reset > now_utc

    def test_uses_brasilia_time_zone(self):
        reset = next_quota_reset(datetime(2026, 10, 1, 12, 0, tzinfo=UTC))

        assert reset.utcoffset() == BRASILIA.utcoffset(reset.replace(tzinfo=None))

    def test_rejects_naive_datetime(self):
        with pytest.raises(ValueError):
            next_quota_reset(datetime(2026, 10, 1, 12, 0))

    def test_defaults_to_now(self):
        reset = next_quota_reset()

        assert reset > datetime.now(UTC)


class TestHttpClient:
    def test_real_client_has_a_timeout(self):
        with make_http_client() as client:
            assert client.timeout.read == HTTP_TIMEOUT_SECONDS

    def test_creates_and_closes_its_own_client_when_none_is_given(self, monkeypatch):
        created: list[httpx.Client] = []

        def fake_factory() -> httpx.Client:
            client = mock_client(json_handler({"free_model_daily_requests": QUOTA_BLOCK}))
            created.append(client)
            return client

        monkeypatch.setattr(account_module, "make_http_client", fake_factory)

        assert get_quota(make_settings(monkeypatch)).used == 12
        assert created[0].is_closed
