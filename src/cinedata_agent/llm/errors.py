"""OpenRouter exceptions. Messages are in Portuguese and must never include the API key."""


class OpenRouterError(Exception):
    """Base class for all OpenRouter errors."""


class AuthenticationError(OpenRouterError):
    """The API key is missing or was rejected (HTTP 401). Never retried."""


class OpenRouterAPIError(OpenRouterError):
    """Unexpected status, malformed response or network failure."""
