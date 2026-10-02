"""OpenRouter exceptions. Messages are in Portuguese and must never include the API key."""

from collections.abc import Sequence
from datetime import datetime


class OpenRouterError(Exception):
    """Base class for all OpenRouter errors."""


class AuthenticationError(OpenRouterError):
    """The API key is missing or was rejected (HTTP 401). Never retried."""


class OpenRouterAPIError(OpenRouterError):
    """Unexpected status, malformed response or network failure."""


class PaymentRequiredError(OpenRouterError):
    """HTTP 402: the account balance is negative. Never retried."""


class QuotaExhaustedError(OpenRouterError):
    """HTTP 429 without provider signals: the free daily quota is used up. Never retried."""

    def __init__(self, message: str, reset_at: datetime) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class AllModelsFailedError(OpenRouterError):
    """Every model in LLM_MODELS failed with a retryable error; keeps one reason per attempt."""

    def __init__(self, reasons: Sequence[tuple[str, str]]) -> None:
        self.reasons = tuple(reasons)
        details = "; ".join(f"{model}: {reason}" for model, reason in self.reasons)
        super().__init__(f"Nenhum modelo da LLM_MODELS respondeu. {details}.")
