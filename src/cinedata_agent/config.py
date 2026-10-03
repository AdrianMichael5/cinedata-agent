"""Application settings loaded from environment variables and an optional .env file."""

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Order from a real A/B test: qwen answered in 6-8s per call (~500 tokens); nemotron-lightning
# took 27-68s (~1000 tokens, mostly reasoning) -- moved after the faster models.
DEFAULT_LLM_MODELS: tuple[str, ...] = (
    "qwen/qwen3.8-27b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3.5-lightning:free",
    "google/gemma-4-26b-a4b-it:free",
    "openrouter/free",
)

DbBackend = Literal["sqlite", "databricks"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Immutable runtime configuration. Works with defaults only (no .env required)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_models: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_LLM_MODELS), min_length=1
    )

    db_backend: DbBackend = "sqlite"
    db_path: Path = Path("data/cinerocket.db")
    # 120 s: an actor-director pair query over bridge_movie_person takes about 55 s on the real
    # database, so the 60 s default left almost no margin.
    query_timeout_seconds: int = Field(default=120, gt=0)
    max_rows: int = Field(default=200, gt=0)

    max_llm_calls_per_question: int = Field(default=3, gt=0)
    # HTTP requests to OpenRouter per question, fallback attempts included (failures count too).
    max_requests_per_question: int = Field(default=6, gt=0)
    # Caps a single model's reply: guards against degenerate output (repetition, leaked
    # reasoning) that would otherwise run until the provider's own token limit.
    max_output_tokens: int = Field(default=2000, gt=0)
    # Sent as extra_body={"reasoning": {"effort": ...}}; blank means do not send it at all.
    # OpenRouter ignores the parameter on models that do not support it.
    llm_reasoning_effort: str = "low"
    cache_dir: Path = Path(".cache")
    # One JSON line per OpenRouter request (no key, no prompt), to compare with the quota.
    request_log_path: Path = Path("logs/requests.jsonl")
    reference_date_override: date | None = Field(default=None, validation_alias="REFERENCE_DATE")
    log_level: LogLevel = "INFO"

    databricks_server_hostname: str = ""
    databricks_http_path: str = ""
    databricks_token: SecretStr = SecretStr("")

    @field_validator("llm_models", mode="before")
    @classmethod
    def _split_models(cls, value: Any) -> Any:
        items = value.split(",") if isinstance(value, str) else value
        if not isinstance(items, list | tuple):
            return value
        # A repeated model would be tried twice in one fallback pass, burning quota for nothing.
        names = (item.strip() if isinstance(item, str) else item for item in items)
        return list(dict.fromkeys(name for name in names if name))

    @field_validator("llm_reasoning_effort", mode="before")
    @classmethod
    def _strip_reasoning_effort(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("reference_date_override", mode="before")
    @classmethod
    def _blank_date_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @property
    def reference_date(self) -> date:
        """Date used for relative windows such as "last N years"; today when not configured."""
        return self.reference_date_override or date.today()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, read once from the environment and .env."""
    return Settings()
