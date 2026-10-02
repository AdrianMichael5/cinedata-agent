"""Question loop: the model writes SQL through run_sql and answers in Portuguese."""

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from openai.types.chat import (
    ChatCompletionMessage,
    ChatCompletionMessageFunctionToolCall,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCallUnion,
    ChatCompletionToolUnionParam,
)

from cinedata_agent.config import Settings
from cinedata_agent.db.base import Database, QueryResult
from cinedata_agent.db.errors import QueryExecutionError, QueryTimeoutError, UnsafeQueryError
from cinedata_agent.db.schema import expand_macros
from cinedata_agent.llm.client import OK_OUTCOME, LLMResponse
from cinedata_agent.llm.errors import AllModelsFailedError, RequestBudgetExceededError
from cinedata_agent.prompts.builder import build_system_prompt
from cinedata_agent.tools import (
    QUERY_ARGUMENT,
    RUN_SQL_TOOL,
    RUN_SQL_TOOL_NAME,
    format_tool_result,
)

logger = logging.getLogger(__name__)

# Some models name the argument "sql" even when the schema says "query".
QUERY_ARGUMENT_ALIASES = (QUERY_ARGUMENT, "sql")
SQL_BLOCK = re.compile(r"```sql[ \t]*\n(?P<sql>.*?)```", re.S | re.I)

# Errors the model can fix by rewriting the SQL; anything else (missing database) goes up.
RECOVERABLE_QUERY_ERRORS = (UnsafeQueryError, QueryExecutionError, QueryTimeoutError)

UNKNOWN_TOOL_MESSAGE = (
    f"ERRO: ferramenta desconhecida. A única ferramenta disponível é {RUN_SQL_TOOL_NAME}."
)
BAD_ARGUMENTS_MESSAGE = (
    "ERRO: argumentos inválidos para run_sql. Envie um objeto JSON no formato "
    '{"query": "SELECT ..."} com a SQL completa.'
)
FINAL_ANSWER_REQUEST = (
    "Resultado da SQL do bloco ```sql da sua mensagem anterior:\n\n{outcome}\n\n"
    "Com base nesse resultado, escreva agora a resposta final à pergunta, em português."
)
LAST_CALL_NOTE = (
    "Esta é a sua última chamada: não chame run_sql de novo. Escreva agora a resposta final "
    "em português com os resultados que já tem, dizendo o que ficou faltando, se for o caso."
)
EMPTY_ANSWER_NUDGE = (
    "Sua resposta veio vazia. Chame a ferramenta run_sql ou escreva a resposta final em português."
)


class ChatModel(Protocol):
    """What the agent needs from an LLM client (LLMClient in production, FakeLLM in tests)."""

    def complete(
        self,
        messages: Sequence[ChatCompletionMessageParam],
        tools: Sequence[ChatCompletionToolUnionParam],
        max_requests: int | None = None,
    ) -> LLMResponse: ...


@dataclass(frozen=True)
class SqlRecord:
    """One query sent to the database, macros expanded; rejection is None when it ran."""

    sql: str
    rejection: str | None = None


@dataclass(frozen=True)
class AgentAnswer:
    text: str
    # The SQL that actually ran (QueryResult.sql), in order.
    sql_executed: list[str]
    model_used: str | None
    llm_calls: int
    last_result: QueryResult | None
    # Every query attempted, rejected ones included, in order.
    sql_log: list[SqlRecord] = field(default_factory=list)


@dataclass
class _Conversation:
    """Mutable state of one question while the loop runs."""

    messages: list[ChatCompletionMessageParam]
    sql_executed: list[str] = field(default_factory=list)
    sql_log: list[SqlRecord] = field(default_factory=list)
    last_result: QueryResult | None = None
    model_used: str | None = None
    llm_calls: int = 0
    # HTTP requests sent for this question (fallback attempts included) and why they failed.
    requests_used: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


class Agent:
    """Answers one question within MAX_LLM_CALLS_PER_QUESTION and MAX_REQUESTS_PER_QUESTION."""

    def __init__(self, settings: Settings, db: Database, llm: ChatModel) -> None:
        self._db = db
        self._llm = llm
        self._max_calls = settings.max_llm_calls_per_question
        self._max_requests = settings.max_requests_per_question
        self._system_prompt = build_system_prompt(settings)

    def ask(self, question: str) -> AgentAnswer:
        """Run the tool-calling loop; quota and key errors from the LLM propagate untouched."""
        question = question.strip()
        if not question:
            raise ValueError("A pergunta está vazia: escreva o que quer saber sobre o catálogo.")

        conversation = _Conversation(
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": question},
            ]
        )
        while conversation.llm_calls < self._max_calls:
            if 0 < conversation.llm_calls == self._max_calls - 1:
                _append_user_message(conversation.messages, LAST_CALL_NOTE)
            try:
                response = self._complete(conversation)
            except (AllModelsFailedError, RequestBudgetExceededError) as error:
                # Key, balance and quota errors still propagate; an outage or a spent request
                # budget after a successful query should not throw that result away.
                if conversation.last_result is None:
                    raise
                logger.warning("LLM stopped after a successful query: %s", error)
                return _answer(conversation, _fallback_text(conversation, _stop_warning(error)))
            conversation.llm_calls += 1
            conversation.model_used = response.model_used
            logger.info(
                "LLM call %d/%d for the question answered by %s",
                conversation.llm_calls,
                self._max_calls,
                response.model_used,
            )
            final_text = self._handle_message(conversation, response.message)
            if final_text is not None:
                return _answer(conversation, final_text)
        return _answer(conversation, _fallback_text(conversation, _budget_warning(self._max_calls)))

    def _complete(self, conversation: _Conversation) -> LLMResponse:
        """One model call limited to the question's remaining request budget."""
        remaining = self._max_requests - conversation.requests_used
        try:
            response = self._llm.complete(
                conversation.messages, [RUN_SQL_TOOL], max_requests=remaining
            )
        except RequestBudgetExceededError as error:
            # The client only knows this call; report the whole question.
            raise RequestBudgetExceededError(
                used=conversation.requests_used + error.used,
                reasons=[*conversation.failures, *error.reasons],
            ) from error
        conversation.requests_used += len(response.attempts)
        conversation.failures.extend(
            (attempt.requested_model, attempt.outcome)
            for attempt in response.attempts
            if attempt.outcome != OK_OUTCOME
        )
        return response

    def _handle_message(
        self, conversation: _Conversation, message: ChatCompletionMessage
    ) -> str | None:
        """Return the final answer text, or None when the loop must call the model again."""
        if message.tool_calls:
            call_ids = _unique_call_ids(message.tool_calls)
            conversation.messages.append(_assistant_tool_calls(message, call_ids))
            for call, call_id in zip(message.tool_calls, call_ids, strict=True):
                conversation.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": self._run_tool_call(conversation, call),
                    }
                )
            return None

        text = (message.content or "").strip()
        if not text:
            _append_user_message(conversation.messages, EMPTY_ANSWER_NUDGE)
            return None

        sql = _sql_block(text) if not conversation.sql_executed else None
        if sql is None:
            return text
        # Fallback for models that print SQL instead of calling the tool.
        outcome = self._execute(conversation, sql)
        conversation.messages.append({"role": "assistant", "content": text})
        _append_user_message(conversation.messages, FINAL_ANSWER_REQUEST.format(outcome=outcome))
        return None

    def _run_tool_call(
        self, conversation: _Conversation, call: ChatCompletionMessageToolCallUnion
    ) -> str:
        if not isinstance(call, ChatCompletionMessageFunctionToolCall):
            return UNKNOWN_TOOL_MESSAGE
        if call.function.name != RUN_SQL_TOOL_NAME:
            return UNKNOWN_TOOL_MESSAGE
        query = _query_argument(call.function.arguments)
        if query is None:
            return BAD_ARGUMENTS_MESSAGE
        return self._execute(conversation, query)

    def _execute(self, conversation: _Conversation, query: str) -> str:
        """Run one query; recoverable errors become text the model can read and fix."""
        try:
            expanded = expand_macros(query)
        except UnsafeQueryError as error:
            return _reject(conversation, query, error)
        try:
            result = self._db.run_query(expanded)
        except RECOVERABLE_QUERY_ERRORS as error:
            return _reject(conversation, expanded, error)
        executed = result.sql or expanded
        conversation.sql_executed.append(executed)
        conversation.sql_log.append(SqlRecord(sql=executed))
        conversation.last_result = result
        return format_tool_result(result)


def _reject(conversation: _Conversation, sql: str, error: Exception) -> str:
    logger.info("Query rejected (%s): %s", type(error).__name__, error)
    conversation.sql_log.append(SqlRecord(sql=sql, rejection=str(error)))
    return f"ERRO: {error}"


def _unique_call_ids(calls: Sequence[ChatCompletionMessageToolCallUnion]) -> list[str]:
    """Some free models send empty or repeated ids, which strict providers reject with a 400."""
    seen: set[str] = set()
    call_ids: list[str] = []
    for position, call in enumerate(calls, start=1):
        call_id = call.id
        suffix = 0
        while not call_id or call_id in seen:
            suffix += 1
            call_id = f"call_{position}" if suffix == 1 else f"call_{position}_{suffix}"
        seen.add(call_id)
        call_ids.append(call_id)
    return call_ids


def _assistant_tool_calls(
    message: ChatCompletionMessage, call_ids: Sequence[str]
) -> ChatCompletionMessageParam:
    # Rebuilt by hand: provider-specific extras (reasoning, refusal) are not echoed back.
    tool_calls: list[Any] = [
        {**call.model_dump(exclude_none=True), "id": call_id}
        for call, call_id in zip(message.tool_calls or [], call_ids, strict=True)
    ]
    return {"role": "assistant", "content": message.content, "tool_calls": tool_calls}


def _append_user_message(messages: list[ChatCompletionMessageParam], text: str) -> None:
    # Merge into a trailing user message: some chat templates reject two user turns in a row.
    if messages and messages[-1]["role"] == "user":
        previous = messages.pop()
        text = f"{previous['content']}\n\n{text}"
    messages.append({"role": "user", "content": text})


def _query_argument(arguments: str) -> str | None:
    try:
        parsed = json.loads(arguments)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    for name in QUERY_ARGUMENT_ALIASES:
        value = parsed.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _sql_block(text: str) -> str | None:
    match = SQL_BLOCK.search(text)
    if match is None:
        return None
    return match.group("sql").strip() or None


def _stop_warning(error: AllModelsFailedError | RequestBudgetExceededError) -> str:
    if isinstance(error, RequestBudgetExceededError):
        return f"Aviso: {error}"
    return f"Aviso: o LLM ficou indisponível antes de concluir a resposta ({error})"


def _budget_warning(max_calls: int) -> str:
    plural = "s" if max_calls != 1 else ""
    return (
        f"Aviso: o modelo não concluiu a resposta dentro do limite de {max_calls} "
        f"chamada{plural} ao LLM (MAX_LLM_CALLS_PER_QUESTION)."
    )


def _fallback_text(conversation: _Conversation, warning: str) -> str:
    """Answer built in Python when the model cannot finish: the warning plus the last result."""
    if conversation.last_result is None:
        return f"{warning} E nenhuma consulta retornou resultado: tente reformular a pergunta."
    return (
        f"{warning} Segue o último resultado obtido, sem interpretação.\n\n"
        f"SQL: {conversation.sql_executed[-1]}\n\n{format_tool_result(conversation.last_result)}"
    )


def _answer(conversation: _Conversation, text: str) -> AgentAnswer:
    return AgentAnswer(
        text=text,
        sql_executed=list(conversation.sql_executed),
        sql_log=list(conversation.sql_log),
        model_used=conversation.model_used,
        llm_calls=conversation.llm_calls,
        last_result=conversation.last_result,
    )
