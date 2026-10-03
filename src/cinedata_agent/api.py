"""Optional HTTP API (extra `api`): uvicorn cinedata_agent.api:create_app --factory.

No authentication: bind it to localhost only, or anyone who reaches it spends the quota.
"""

import logging
from collections.abc import Callable
from typing import Protocol

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from cinedata_agent.agent import Agent, ChatModel
from cinedata_agent.cache import AnswerCache
from cinedata_agent.config import Settings, get_settings
from cinedata_agent.db.base import Database
from cinedata_agent.db.errors import DatabaseError
from cinedata_agent.db.factory import get_database
from cinedata_agent.llm.client import LLMClient
from cinedata_agent.llm.errors import AuthenticationError, OpenRouterError, QuotaExhaustedError

logger = logging.getLogger(__name__)

HEALTH_SQL = "SELECT 1"

# Fixed client-facing texts: error details (file paths, provider bodies) stay in the server log.
AUTH_MESSAGE = (
    "O OpenRouter recusou a chave da API. Confira OPENROUTER_API_KEY no .env do servidor."
)
OPENROUTER_MESSAGE = "O OpenRouter não conseguiu responder agora. Tente de novo em alguns minutos."
DATABASE_MESSAGE = "O banco de dados não está disponível no servidor."
NOT_IMPLEMENTED_MESSAGE = "O backend de banco configurado ainda não foi implementado."
UNEXPECTED_MESSAGE = "Erro inesperado no servidor."


class CountingChatModel(ChatModel, Protocol):
    """A chat model that counts its HTTP requests and releases its connections."""

    @property
    def requests_sent(self) -> int: ...

    def close(self) -> None: ...


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    no_cache: bool = False

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A pergunta está vazia: escreva o que quer saber sobre o catálogo.")
        return value


class AskResponse(BaseModel):
    answer: str
    sql: list[str]
    model: str | None
    llm_calls: int
    requests: int
    from_cache: bool
    warning: str | None = None


class HealthResponse(BaseModel):
    status: str
    db: str


def create_app(
    settings: Settings | None = None,
    make_database: Callable[[], Database] | None = None,
    make_llm: Callable[[], CountingChatModel] | None = None,
) -> FastAPI:
    """Build the app; the arguments exist for tests, uvicorn calls it with none."""
    config = settings or get_settings()
    database_factory = make_database or (lambda: get_database(config))
    llm_factory = make_llm or (lambda: LLMClient(config))
    cache = AnswerCache.from_settings(config)

    app = FastAPI(title="CineData Agent", version="0.1.0")

    # Sync handlers on purpose: FastAPI runs them in a thread, since the agent and SQLite block.
    @app.post("/api/v1/ask", response_model=AskResponse)
    def ask(request: AskRequest) -> AskResponse:
        llm: CountingChatModel | None = None
        try:
            key = cache.key_for(config, request.question)
            cached = None if request.no_cache else cache.get(key)
            if cached is not None:
                return AskResponse(
                    answer=cached.text,
                    sql=list(cached.sql_executed),
                    model=cached.model_used,
                    llm_calls=cached.llm_calls,
                    requests=0,
                    from_cache=True,
                )
            llm = llm_factory()
            answer = Agent(config, database_factory(), llm).ask(request.question)
        except Exception as error:
            raise _http_error(error) from error
        finally:
            if llm is not None:
                llm.close()
        cache.put(key, request.question, answer, llm.requests_sent)
        return AskResponse(
            answer=answer.text,
            sql=list(answer.sql_executed),
            model=answer.model_used,
            llm_calls=answer.llm_calls,
            requests=llm.requests_sent,
            from_cache=False,
            warning=answer.warning,
        )

    @app.get("/health", response_model=HealthResponse)
    def health() -> JSONResponse:
        try:
            database_factory().run_query(HEALTH_SQL)
        except DatabaseError as error:
            logger.warning("Health check failed: database unavailable", exc_info=error)
            body = HealthResponse(status="erro", db="erro")
            return JSONResponse(body.model_dump(), status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        return JSONResponse(HealthResponse(status="ok", db="ok").model_dump())

    return app


def _http_error(error: Exception) -> HTTPException:
    """Map an error to a status and a fixed message; the detail goes to the server log."""
    if isinstance(error, QuotaExhaustedError):
        # Written by this project (reset time only), so it is safe to show.
        code, message = status.HTTP_429_TOO_MANY_REQUESTS, str(error)
    elif isinstance(error, AuthenticationError):
        code, message = status.HTTP_502_BAD_GATEWAY, AUTH_MESSAGE
    elif isinstance(error, OpenRouterError):
        code, message = status.HTTP_502_BAD_GATEWAY, OPENROUTER_MESSAGE
    elif isinstance(error, DatabaseError):
        code, message = status.HTTP_503_SERVICE_UNAVAILABLE, DATABASE_MESSAGE
    elif isinstance(error, NotImplementedError):
        code, message = status.HTTP_501_NOT_IMPLEMENTED, NOT_IMPLEMENTED_MESSAGE
    else:
        logger.error("Unexpected error in /api/v1/ask", exc_info=error)
        return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, UNEXPECTED_MESSAGE)
    logger.warning("/api/v1/ask answered HTTP %d: %s", code, type(error).__name__, exc_info=error)
    return HTTPException(code, message)
