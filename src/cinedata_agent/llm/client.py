"""OpenRouter chat client that walks LLM_MODELS in order and falls back only on safe errors."""

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx2
import openai
from openai import OpenAI
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessage,
    ChatCompletionMessageParam,
    ChatCompletionToolUnionParam,
)

from cinedata_agent.config import Settings
from cinedata_agent.llm.errors import (
    AllModelsFailedError,
    AuthenticationError,
    OpenRouterAPIError,
    PaymentRequiredError,
    QuotaExhaustedError,
    RequestBudgetExceededError,
)
from cinedata_agent.llm.openrouter_account import INVALID_KEY_MESSAGE, next_quota_reset

logger = logging.getLogger(__name__)

# The SDK retries 429 and 5xx on its own by default; every retry burns the daily quota.
MAX_SDK_RETRIES = 0
REQUEST_TIMEOUT_SECONDS = 60
RAW_BODY_LOG_LIMIT = 2000
SERVER_MESSAGE_LIMIT = 200
OK_OUTCOME = "ok"
INVALID_RESPONSE = "resposta inválida do OpenRouter"

CAPACITY_METADATA_KEYS = ("provider_name", "raw")
CAPACITY_KEY_MARKERS = ("upstream", "provider")
CAPACITY_MESSAGE_MARKERS = ("upstream", "temporarily rate-limited")
EMPTY_VALUES: tuple[Any, ...] = (None, "", {}, [])
MODEL_UNAVAILABLE_MARKERS = (
    "no endpoints found",
    "not a valid model",
    "does not support tool",
    "doesn't support tool",
)
PAYMENT_REQUIRED_MESSAGE = (
    "O OpenRouter recusou a chamada por falta de saldo (HTTP 402). Modelos gratuitos não "
    "cobram: confira se o saldo da conta em openrouter.ai/settings/credits não está negativo."
)


@dataclass(frozen=True)
class Attempt:
    """One request sent to OpenRouter; outcome is "ok" or the reason (in Portuguese) it failed."""

    requested_model: str
    responded_model: str | None
    status: int | None
    elapsed_ms: int
    outcome: str


@dataclass(frozen=True)
class LLMResponse:
    message: ChatCompletionMessage
    model_used: str
    requested_model: str
    attempts: tuple[Attempt, ...]


class LLMClient:
    """Chat completions over LLM_MODELS, one request per model at most, never the same twice."""

    def __init__(self, settings: Settings, http_client: httpx2.Client | None = None) -> None:
        key = settings.openrouter_api_key.get_secret_value().strip()
        if not key:
            raise AuthenticationError(INVALID_KEY_MESSAGE)
        self._models = tuple(settings.llm_models)
        self._openai = OpenAI(
            base_url=settings.openrouter_base_url,
            api_key=key,
            max_retries=MAX_SDK_RETRIES,
            timeout=REQUEST_TIMEOUT_SECONDS,
            http_client=http_client,
        )
        self._requests_sent = 0

    @property
    def requests_sent(self) -> int:
        """Requests sent since this client was created, successful or not."""
        return self._requests_sent

    def complete(
        self,
        messages: Sequence[ChatCompletionMessageParam],
        tools: Sequence[ChatCompletionToolUnionParam],
        max_requests: int | None = None,
    ) -> LLMResponse:
        """Ask each model in order until one answers; stop at once on key, balance or quota.

        max_requests caps the requests this call may send (the question's remaining budget).
        """
        attempts: list[Attempt] = []
        for model in self._models:
            if max_requests is not None and len(attempts) >= max_requests:
                raise RequestBudgetExceededError(
                    used=len(attempts), reasons=[(a.requested_model, a.outcome) for a in attempts]
                )
            attempt, message = self._try_model(model, messages, tools)
            attempts.append(attempt)
            if message is not None:
                return LLMResponse(
                    message=message,
                    model_used=attempt.responded_model or model,
                    requested_model=model,
                    attempts=tuple(attempts),
                )
        raise AllModelsFailedError([(a.requested_model, a.outcome) for a in attempts])

    def _try_model(
        self,
        model: str,
        messages: Sequence[ChatCompletionMessageParam],
        tools: Sequence[ChatCompletionToolUnionParam],
    ) -> tuple[Attempt, ChatCompletionMessage | None]:
        self._requests_sent += 1
        started = time.perf_counter()
        try:
            completion = self._openai.chat.completions.create(
                model=model,
                messages=list(messages),
                tools=list(tools),
                tool_choice="auto",
                temperature=0,
            )
        except openai.APIStatusError as error:
            self._log(model, None, error.status_code, started)
            reason = _classify_status_error(error, model)
            return Attempt(model, None, error.status_code, _elapsed_ms(started), reason), None
        except openai.APIConnectionError as error:
            self._log(model, None, None, started)
            timed_out = isinstance(error, openai.APITimeoutError)
            reason = "tempo limite esgotado" if timed_out else "falha de conexão"
            return Attempt(model, None, None, _elapsed_ms(started), reason), None
        except (openai.OpenAIError, ValueError) as error:
            # Bodies the SDK cannot parse (empty, invalid JSON) surface here, not as status errors.
            self._log(model, None, None, started)
            logger.debug("Unparseable response from %s: %s", model, type(error).__name__)
            return Attempt(model, None, None, _elapsed_ms(started), INVALID_RESPONSE), None
        return self._read_completion(model, completion, started)

    def _read_completion(
        self, model: str, completion: object, started: float
    ) -> tuple[Attempt, ChatCompletionMessage | None]:
        # Non-JSON 200s (HTML from a gateway, a JSON list) come back as plain str/list objects.
        if not isinstance(completion, ChatCompletion):
            self._log(model, None, 200, started)
            return Attempt(model, None, 200, _elapsed_ms(started), INVALID_RESPONSE), None

        model_field: object = completion.model
        responded = model_field if isinstance(model_field, str) and model_field else None
        self._log(model, responded, 200, started)
        embedded = _embedded_error(completion)
        message = completion.choices[0].message if completion.choices else None
        if embedded is not None:
            reason = embedded
        elif not completion.choices:
            reason = "resposta sem conteúdo (choices vazio)"
        elif message is None:
            reason = "resposta sem mensagem (choices[0].message vazio)"
        else:
            return Attempt(model, responded, 200, _elapsed_ms(started), OK_OUTCOME), message
        return Attempt(model, responded, 200, _elapsed_ms(started), reason), None

    def _log(
        self, requested: str, responded: str | None, status: int | None, started: float
    ) -> None:
        logger.info(
            "OpenRouter request #%d: requested=%s responded=%s status=%s elapsed=%dms",
            self._requests_sent,
            requested,
            responded or "-",
            status if status is not None else "no-response",
            _elapsed_ms(started),
        )


def _classify_status_error(error: openai.APIStatusError, model: str) -> str:
    """Return why the next model should be tried, or raise when no model can help."""
    code = error.status_code
    body = _error_body(error)
    message = str(body.get("message") or error.message)

    if code == 401:
        raise AuthenticationError(INVALID_KEY_MESSAGE) from error
    if code == 402:
        raise PaymentRequiredError(PAYMENT_REQUIRED_MESSAGE) from error
    if code == 429:
        logger.debug("HTTP 429 body for %s: %s", model, error.response.text[:RAW_BODY_LOG_LIMIT])
        if _is_provider_capacity(body, message):
            return "HTTP 429: provider do modelo sem capacidade no momento"
        raise _quota_exhausted() from error
    if code in (400, 404) and _is_model_unavailable(message):
        return f"HTTP {code}: modelo indisponível ou sem suporte a tools"
    if code >= 500 or code == 408:
        return f"HTTP {code}: falha temporária do servidor"
    raise OpenRouterAPIError(
        f"O OpenRouter recusou a requisição para {model} com HTTP {code}: "
        f"{message[:SERVER_MESSAGE_LIMIT]}"
    ) from error


def _error_body(error: openai.APIStatusError) -> Mapping[str, Any]:
    # The SDK already unwraps {"error": {...}}; non-JSON bodies arrive as text or None.
    body = error.body
    return body if isinstance(body, Mapping) else {}


def _is_provider_capacity(body: Mapping[str, Any], message: str) -> bool:
    metadata = body.get("metadata")
    if isinstance(metadata, Mapping) and any(metadata.get(k) for k in CAPACITY_METADATA_KEYS):
        return True
    if _has_capacity_key(body):
        return True
    lowered = message.lower()
    return any(marker in lowered for marker in CAPACITY_MESSAGE_MARKERS)


def _has_capacity_key(value: Any) -> bool:
    # A key only counts when it carries a value: "provider_name": null is not a signal.
    if isinstance(value, Mapping):
        return any(
            (_is_capacity_key(key) and item not in EMPTY_VALUES) or _has_capacity_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_capacity_key(item) for item in value)
    return False


def _is_capacity_key(key: Any) -> bool:
    return any(marker in str(key).lower() for marker in CAPACITY_KEY_MARKERS)


def _is_model_unavailable(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in MODEL_UNAVAILABLE_MARKERS)


def _embedded_error(completion: ChatCompletion) -> str | None:
    """OpenRouter may answer 200 with {"error": {...}} instead of choices."""
    error = (completion.model_extra or {}).get("error")
    if not isinstance(error, Mapping):
        return None
    message = str(error.get("message") or "sem mensagem")[:SERVER_MESSAGE_LIMIT]
    return f"erro {error.get('code', '?')} no corpo de uma resposta 200: {message}"


def _quota_exhausted() -> QuotaExhaustedError:
    reset = next_quota_reset()
    return QuotaExhaustedError(
        "Cota diária de requisições gratuitas do OpenRouter esgotada (HTTP 429). "
        f"Ela renova às {reset:%H:%M} de {reset:%d/%m/%Y} (horário de Brasília).",
        reset_at=reset,
    )


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
