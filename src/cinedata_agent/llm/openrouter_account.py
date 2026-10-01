"""OpenRouter account helpers that never call a model: quota (/key) and catalog (/models)."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from cinedata_agent.config import Settings
from cinedata_agent.llm.errors import AuthenticationError, OpenRouterAPIError

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = 15.0
BRASILIA_TZ = ZoneInfo("America/Sao_Paulo")
QUOTA_FIELD = "free_model_daily_requests"
FREE_SUFFIX = ":free"
ROUTER_MODELS = frozenset({"openrouter/free"})
INVALID_KEY_MESSAGE = "Chave inválida ou ausente: confira OPENROUTER_API_KEY no .env"


@dataclass(frozen=True)
class QuotaInfo:
    used: int
    limit: int
    remaining: int


@dataclass(frozen=True)
class FreeToolModel:
    id: str
    context_length: int | None
    supports_tool_choice: bool


class ModelStatus(StrEnum):
    OK = "ok"
    NO_TOOLS = "no_tools"
    MISSING = "missing"
    NOT_FREE = "not_free"
    ROUTER = "router"


STATUS_MESSAGES: dict[ModelStatus, str] = {
    ModelStatus.OK: "aceita tools",
    ModelStatus.NO_TOOLS: "não aceita tools",
    ModelStatus.MISSING: "não encontrado no catálogo do OpenRouter",
    ModelStatus.NOT_FREE: "aceita tools, mas não é gratuito (consome créditos)",
    ModelStatus.ROUTER: "roteador, sem garantia de tools",
}


@dataclass(frozen=True)
class ModelDiagnosis:
    model_id: str
    status: ModelStatus

    @property
    def message(self) -> str:
        return STATUS_MESSAGES[self.status]


@dataclass(frozen=True)
class ModelsReport:
    free_tool_models: tuple[FreeToolModel, ...]
    diagnostics: tuple[ModelDiagnosis, ...]


def make_http_client() -> httpx.Client:
    """Real HTTP client used by the CLI; tests replace it with an httpx.MockTransport client."""
    return httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)


def get_quota(settings: Settings, client: httpx.Client | None = None) -> QuotaInfo:
    """Read today's free-model request usage. Does not consume quota."""
    key = settings.openrouter_api_key.get_secret_value().strip()
    if not key:
        raise AuthenticationError(INVALID_KEY_MESSAGE)

    with _client_scope(client) as http:
        payload = _get_json(http, _url(settings, "key"), "/key", {"Authorization": f"Bearer {key}"})
    return _parse_quota(payload)


def list_free_tool_models(settings: Settings, client: httpx.Client | None = None) -> ModelsReport:
    """List free models that accept tools and diagnose each model in LLM_MODELS."""
    with _client_scope(client) as http:
        payload = _get_json(http, _url(settings, "models"), "/models", headers={})
    catalog = _parse_catalog(payload)

    free_tool_models = tuple(
        sorted(
            (
                FreeToolModel(
                    id=model_id,
                    context_length=_optional_int(entry.get("context_length")),
                    supports_tool_choice="tool_choice" in _parameters(entry),
                )
                for model_id, entry in catalog.items()
                if model_id.endswith(FREE_SUFFIX) and "tools" in _parameters(entry)
            ),
            key=lambda model: model.id,
        )
    )
    diagnostics = tuple(
        ModelDiagnosis(model_id, _diagnose(model_id, catalog)) for model_id in settings.llm_models
    )
    return ModelsReport(free_tool_models=free_tool_models, diagnostics=diagnostics)


def next_quota_reset(now: datetime | None = None) -> datetime:
    """Next midnight UTC (when the free quota resets), expressed in Brasília time."""
    current = datetime.now(UTC) if now is None else now
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    tomorrow_utc = current.astimezone(UTC).date() + timedelta(days=1)
    return datetime.combine(tomorrow_utc, time.min, tzinfo=UTC).astimezone(BRASILIA_TZ)


@contextmanager
def _client_scope(client: httpx.Client | None) -> Iterator[httpx.Client]:
    if client is not None:
        yield client
        return
    with make_http_client() as owned:
        yield owned


def _url(settings: Settings, path: str) -> str:
    return f"{settings.openrouter_base_url.rstrip('/')}/{path}"


def _get_json(client: httpx.Client, url: str, label: str, headers: dict[str, str]) -> Any:
    # Error messages name the endpoint only: never the headers, which carry the key.
    try:
        response = client.get(url, headers=headers)
    except httpx.HTTPError as error:
        raise OpenRouterAPIError(
            f"Falha de rede ao acessar {label} do OpenRouter ({type(error).__name__})."
        ) from error

    logger.debug("GET %s -> HTTP %s", label, response.status_code)
    if response.status_code == 401:
        raise AuthenticationError(INVALID_KEY_MESSAGE)
    if not response.is_success:
        raise OpenRouterAPIError(f"O OpenRouter respondeu HTTP {response.status_code} em {label}.")
    try:
        return response.json()
    except ValueError as error:
        raise OpenRouterAPIError(f"A resposta de {label} não é um JSON válido.") from error


def _parse_quota(payload: Any) -> QuotaInfo:
    container = payload.get("data", payload) if isinstance(payload, dict) else None
    block = container.get(QUOTA_FIELD) if isinstance(container, dict) else None
    if not isinstance(block, dict):
        raise OpenRouterAPIError(f"A resposta de /key não tem o campo {QUOTA_FIELD}.")

    values: list[Any] = [block.get(name) for name in ("used", "limit", "remaining")]
    if not all(_is_int(value) for value in values):
        raise OpenRouterAPIError(
            f"O campo {QUOTA_FIELD} veio incompleto ou com valores que não são inteiros."
        )
    used, limit, remaining = values
    return QuotaInfo(used=used, limit=limit, remaining=remaining)


def _parse_catalog(payload: Any) -> dict[str, dict[str, Any]]:
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise OpenRouterAPIError("A resposta de /models não está no formato esperado.")
    return {
        entry["id"]: entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }


def _diagnose(model_id: str, catalog: dict[str, dict[str, Any]]) -> ModelStatus:
    if model_id in ROUTER_MODELS:
        return ModelStatus.ROUTER
    entry = catalog.get(model_id)
    if entry is None:
        return ModelStatus.MISSING
    if "tools" not in _parameters(entry):
        return ModelStatus.NO_TOOLS
    if not model_id.endswith(FREE_SUFFIX):
        return ModelStatus.NOT_FREE
    return ModelStatus.OK


def _parameters(entry: dict[str, Any]) -> list[Any]:
    parameters = entry.get("supported_parameters")
    return parameters if isinstance(parameters, list) else []


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _optional_int(value: Any) -> int | None:
    return value if _is_int(value) else None
