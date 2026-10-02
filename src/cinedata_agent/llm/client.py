"""OpenRouter chat client that walks LLM_MODELS in order and falls back only on safe errors."""

import logging
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
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
from cinedata_agent.llm.request_log import RequestLog, RequestRecord, now_utc_iso

logger = logging.getLogger(__name__)

# The SDK retries 429 and 5xx on its own by default; every retry burns the daily quota.
MAX_SDK_RETRIES = 0
REQUEST_TIMEOUT_SECONDS = 60
RAW_BODY_LOG_LIMIT = 2000
SERVER_MESSAGE_LIMIT = 200
OK_OUTCOME = "ok"
INVALID_RESPONSE = "resposta inválida do OpenRouter"
FREE_SUFFIX = ":free"

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

Progress = Callable[[str], None]


class ErrorKind(StrEnum):
    """Why a request did not produce an answer; written to the request log as error_type."""

    PROVIDER_CAPACITY = "provider_capacity"
    QUOTA_EXHAUSTED = "quota_exhausted"
    AUTHENTICATION = "authentication"
    PAYMENT_REQUIRED = "payment_required"
    MODEL_UNAVAILABLE = "model_unavailable"
    SERVER_ERROR = "server_error"
    BAD_REQUEST = "bad_request"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    INVALID_RESPONSE = "invalid_response"
    EMBEDDED_ERROR = "embedded_error"
    EMPTY_CHOICES = "empty_choices"
    NO_MESSAGE = "no_message"


TRANSPORT_REASONS: dict[ErrorKind, str] = {
    ErrorKind.TIMEOUT: "tempo limite esgotado",
    ErrorKind.CONNECTION: "falha de conexão",
    ErrorKind.INVALID_RESPONSE: INVALID_RESPONSE,
    ErrorKind.EMPTY_CHOICES: "resposta sem conteúdo (choices vazio)",
    ErrorKind.NO_MESSAGE: "resposta sem mensagem (choices[0].message vazio)",
}


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


@dataclass(frozen=True)
class _RequestContext:
    model: str
    question_id: str | None
    started: float


class LLMClient:
    """Chat completions over LLM_MODELS, one request per model at most, never the same twice."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx2.Client | None = None,
        progress: Progress | None = None,
        request_log: RequestLog | None = None,
    ) -> None:
        key = settings.openrouter_api_key.get_secret_value().strip()
        if not key:
            raise AuthenticationError(INVALID_KEY_MESSAGE)
        self._models = tuple(settings.llm_models)
        self._max_output_tokens = settings.max_output_tokens
        self._openai = OpenAI(
            base_url=settings.openrouter_base_url,
            api_key=key,
            max_retries=MAX_SDK_RETRIES,
            timeout=REQUEST_TIMEOUT_SECONDS,
            http_client=http_client,
        )
        self._progress = progress or _ignore_progress
        self._request_log = request_log or RequestLog(settings.request_log_path)
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
        skip_models: Collection[str] = (),
        question_id: str | None = None,
    ) -> LLMResponse:
        """Ask each model in order until one answers; stop at once on key, balance or quota.

        max_requests caps the requests this call may send (the question's remaining budget);
        skip_models are models that already failed this question (an empty answer, say).
        """
        attempts: list[Attempt] = []
        for model in (name for name in self._models if name not in skip_models):
            if max_requests is not None and len(attempts) >= max_requests:
                raise RequestBudgetExceededError(
                    used=len(attempts), reasons=[(a.requested_model, a.outcome) for a in attempts]
                )
            attempt, message = self._try_model(model, messages, tools, question_id)
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
        question_id: str | None,
    ) -> tuple[Attempt, ChatCompletionMessage | None]:
        self._requests_sent += 1
        self._progress(f"Chamando {short_model_name(model)}…")
        context = _RequestContext(model, question_id, time.perf_counter())
        try:
            completion = self._openai.chat.completions.create(
                model=model,
                messages=list(messages),
                tools=list(tools),
                tool_choice="auto",
                temperature=0,
                max_tokens=self._max_output_tokens,
            )
        except openai.APIStatusError as error:
            kind = _status_error_kind(error)
            self._record(context, None, error.status_code, kind)
            reason = _status_error_outcome(kind, error, model)
            return Attempt(model, None, error.status_code, _elapsed_ms(context), reason), None
        except openai.APIConnectionError as error:
            timed_out = isinstance(error, openai.APITimeoutError)
            kind = ErrorKind.TIMEOUT if timed_out else ErrorKind.CONNECTION
            return self._transport_failure(context, kind, status=None), None
        except (openai.OpenAIError, ValueError) as error:
            # Bodies the SDK cannot parse (empty, invalid JSON) surface here, not as status errors.
            logger.debug("Unparseable response from %s: %s", model, type(error).__name__)
            return self._transport_failure(context, ErrorKind.INVALID_RESPONSE, status=None), None
        return self._read_completion(context, completion)

    def _read_completion(
        self, context: _RequestContext, completion: object
    ) -> tuple[Attempt, ChatCompletionMessage | None]:
        # Non-JSON 200s (HTML from a gateway, a JSON list) come back as plain str/list objects.
        if not isinstance(completion, ChatCompletion):
            return self._transport_failure(context, ErrorKind.INVALID_RESPONSE, status=200), None

        model_field: object = completion.model
        responded = model_field if isinstance(model_field, str) and model_field else None
        message = completion.choices[0].message if completion.choices else None
        embedded = _embedded_error(completion)
        if embedded is not None:
            kind: ErrorKind | None = ErrorKind.EMBEDDED_ERROR
            reason = embedded
        elif not completion.choices:
            kind, reason = ErrorKind.EMPTY_CHOICES, TRANSPORT_REASONS[ErrorKind.EMPTY_CHOICES]
        elif message is None:
            kind, reason = ErrorKind.NO_MESSAGE, TRANSPORT_REASONS[ErrorKind.NO_MESSAGE]
        else:
            kind, reason = None, OK_OUTCOME
        self._record(context, responded, 200, kind, usage=completion.usage)
        attempt = Attempt(context.model, responded, 200, _elapsed_ms(context), reason)
        return attempt, (message if kind is None else None)

    def _transport_failure(
        self, context: _RequestContext, kind: ErrorKind, status: int | None
    ) -> Attempt:
        self._record(context, None, status, kind)
        return Attempt(context.model, None, status, _elapsed_ms(context), TRANSPORT_REASONS[kind])

    def _record(
        self,
        context: _RequestContext,
        responded: str | None,
        status: int | None,
        kind: ErrorKind | None,
        usage: Any = None,
    ) -> None:
        """Log one request (never the key or the prompt) to the logger and the JSONL file."""
        elapsed = _elapsed_ms(context)
        logger.info(
            "OpenRouter request #%d: requested=%s responded=%s status=%s elapsed=%dms",
            self._requests_sent,
            context.model,
            responded or "-",
            status if status is not None else "no-response",
            elapsed,
        )
        self._request_log.append(
            RequestRecord(
                timestamp=now_utc_iso(),
                question_id=context.question_id,
                requested_model=context.model,
                responded_model=responded,
                status=status,
                latency_ms=elapsed,
                prompt_tokens=_token_count(usage, "prompt_tokens"),
                completion_tokens=_token_count(usage, "completion_tokens"),
                error_type=kind.value if kind is not None else None,
            )
        )


def short_model_name(model: str) -> str:
    """'nvidia/nemotron-3.5-lightning:free' -> 'nemotron-3.5-lightning' for progress messages."""
    name = model.rsplit("/", 1)[-1].removesuffix(FREE_SUFFIX)
    return model if not name or name == "free" else name


def _ignore_progress(_: str) -> None:
    return None


def _status_error_kind(error: openai.APIStatusError) -> ErrorKind:
    code = error.status_code
    body = _error_body(error)
    message = str(body.get("message") or error.message)
    if code == 401:
        return ErrorKind.AUTHENTICATION
    if code == 402:
        return ErrorKind.PAYMENT_REQUIRED
    if code == 429:
        capacity = _is_provider_capacity(body, message)
        return ErrorKind.PROVIDER_CAPACITY if capacity else ErrorKind.QUOTA_EXHAUSTED
    if code in (400, 404) and _is_model_unavailable(message):
        return ErrorKind.MODEL_UNAVAILABLE
    if code >= 500 or code == 408:
        return ErrorKind.SERVER_ERROR
    return ErrorKind.BAD_REQUEST


def _status_error_outcome(kind: ErrorKind, error: openai.APIStatusError, model: str) -> str:
    """Return why the next model should be tried, or raise when no model can help."""
    code = error.status_code
    if code == 429:
        logger.debug("HTTP 429 body for %s: %s", model, error.response.text[:RAW_BODY_LOG_LIMIT])
    if kind is ErrorKind.AUTHENTICATION:
        raise AuthenticationError(INVALID_KEY_MESSAGE) from error
    if kind is ErrorKind.PAYMENT_REQUIRED:
        raise PaymentRequiredError(PAYMENT_REQUIRED_MESSAGE) from error
    if kind is ErrorKind.QUOTA_EXHAUSTED:
        raise _quota_exhausted() from error
    if kind is ErrorKind.PROVIDER_CAPACITY:
        return "HTTP 429: provider do modelo sem capacidade no momento"
    if kind is ErrorKind.MODEL_UNAVAILABLE:
        return f"HTTP {code}: modelo indisponível ou sem suporte a tools"
    if kind is ErrorKind.SERVER_ERROR:
        return f"HTTP {code}: falha temporária do servidor"
    message = str(_error_body(error).get("message") or error.message)
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


def _token_count(usage: Any, name: str) -> int | None:
    value = getattr(usage, name, None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _quota_exhausted() -> QuotaExhaustedError:
    reset = next_quota_reset()
    return QuotaExhaustedError(
        "Cota diária de requisições gratuitas do OpenRouter esgotada (HTTP 429). "
        f"Ela renova às {reset:%H:%M} de {reset:%d/%m/%Y} (horário de Brasília).",
        reset_at=reset,
    )


def _elapsed_ms(context: _RequestContext) -> int:
    return round((time.perf_counter() - context.started) * 1000)
