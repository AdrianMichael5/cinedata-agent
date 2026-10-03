"""Question loop: the model writes SQL through run_sql and answers in Portuguese."""

import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

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
from cinedata_agent.formatting import SQL_WAIT_LABEL
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
    "Esta é a última etapa: responda agora em português usando apenas os resultados já obtidos."
)
ToolChoice = Literal["auto", "none"]
LAST_CALL_TOOL_CHOICE: ToolChoice = "none"
DEFAULT_TOOL_CHOICE: ToolChoice = "auto"
EMPTY_ANSWER_REASON = "resposta vazia"
EMPTY_ANSWER_NUDGE = (
    "Sua resposta veio vazia. Chame a ferramenta run_sql ou escreva a resposta final em português."
)
DISCARDED_ANSWER_PREFIX = "resposta do modelo descartada: "
DEGENERATE_ANSWER_NUDGE = (
    "A resposta anterior foi descartada por parecer degenerada (texto repetitivo ou raciocínio "
    "vazado). Escreva agora a resposta final de novo, em português, direta e sem repetições, "
    "usando os resultados que já tem."
)

# A model that runs out of useful content sometimes repeats a short fragment, leaks its
# reasoning in English before the answer, or simply never stops writing.
DEGENERATE_MAX_CHARS = 6000
DEGENERATE_MIN_REPEATS = 20

SQL_CLOCK: Callable[[], float] = time.perf_counter
LEAKED_REASONING_MARKERS = ("I need to", "Let's", "The query returned", "We need")


def is_degenerate(text: str) -> str | None:
    """Why a model's final answer should be discarded, or None when it looks fine."""
    stripped = text.strip()
    if len(stripped) > DEGENERATE_MAX_CHARS:
        return f"resposta com mais de {DEGENERATE_MAX_CHARS} caracteres"
    marker = _leaked_reasoning_marker(stripped)
    if marker is not None:
        return f'raciocínio vazado no início da resposta ("{marker}")'
    fragment = _repeated_fragment(stripped)
    if fragment is not None:
        return f'trecho "{fragment}" repetido {DEGENERATE_MIN_REPEATS} vezes ou mais seguidas'
    return None


def _leaked_reasoning_marker(text: str) -> str | None:
    lowered = text.lower()
    for marker in LEAKED_REASONING_MARKERS:
        if lowered.startswith(marker.lower()):
            return marker
    return None


def _repeated_fragment(text: str, min_repeats: int = DEGENERATE_MIN_REPEATS) -> str | None:
    """A whitespace-separated token repeated min_repeats times or more in a row."""
    tokens = text.split()
    run_start = 0
    for index in range(1, len(tokens) + 1):
        if index < len(tokens) and tokens[index] == tokens[run_start]:
            continue
        if index - run_start >= min_repeats:
            return tokens[run_start]
        run_start = index
    return None


class ChatModel(Protocol):
    """What the agent needs from an LLM client (LLMClient in production, FakeLLM in tests)."""

    def complete(
        self,
        messages: Sequence[ChatCompletionMessageParam],
        tools: Sequence[ChatCompletionToolUnionParam],
        max_requests: int | None = None,
        skip_models: Collection[str] = (),
        question_id: str | None = None,
        tool_choice: ToolChoice = "auto",
    ) -> LLMResponse: ...


@dataclass(frozen=True)
class RunStats:
    """Progress of the current or last question, available even when ask() raised."""

    model_used: str | None
    llm_calls: int


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
    # Why the answer is incomplete (budget spent, empty answer, LLM down); None when complete.
    warning: str | None = None
    question_id: str = ""
    # Milliseconds spent inside the database for this question, summed over every query.
    sql_ms: int = 0


@dataclass
class _Conversation:
    """Mutable state of one question while the loop runs."""

    messages: list[ChatCompletionMessageParam]
    # Shared by every request of this question in the request log.
    question_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    sql_executed: list[str] = field(default_factory=list)
    sql_log: list[SqlRecord] = field(default_factory=list)
    last_result: QueryResult | None = None
    sql_ms: int = 0
    model_used: str | None = None
    llm_calls: int = 0
    # HTTP requests sent for this question (fallback attempts included) and why they failed.
    requests_used: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    # Models that gave an empty answer: not asked again for this question.
    skip_models: set[str] = field(default_factory=set)
    # Set when the answer was built in Python because the model could not finish.
    warning: str | None = None
    # A degenerate answer is retried with the next model at most once per question.
    degenerate_retried: bool = False


class Agent:
    """Answers one question within MAX_LLM_CALLS_PER_QUESTION and MAX_REQUESTS_PER_QUESTION."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        llm: ChatModel,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self._db = db
        self._llm = llm
        self._max_calls = settings.max_llm_calls_per_question
        self._max_requests = settings.max_requests_per_question
        self._system_prompt = build_system_prompt(settings)
        self._progress = progress or _ignore_progress
        self._conversation: _Conversation | None = None

    @property
    def last_run(self) -> RunStats:
        """Calls and model of the current or last question, also after an error."""
        if self._conversation is None:
            return RunStats(model_used=None, llm_calls=0)
        return RunStats(self._conversation.model_used, self._conversation.llm_calls)

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
        self._conversation = conversation
        while conversation.llm_calls < self._max_calls:
            is_final_attempt = 0 < conversation.llm_calls == self._max_calls - 1
            if is_final_attempt:
                _append_user_message(conversation.messages, LAST_CALL_NOTE)
            if conversation.sql_log:
                self._progress("Redigindo resposta…")
            tool_choice = LAST_CALL_TOOL_CHOICE if is_final_attempt else DEFAULT_TOOL_CHOICE
            try:
                response = self._complete(conversation, tool_choice)
            except (AllModelsFailedError, RequestBudgetExceededError) as error:
                # Key, balance and quota errors still propagate; an outage or a spent request
                # budget after a successful query should not throw that result away.
                if conversation.last_result is None:
                    raise
                logger.info("LLM stopped after a successful query: %s", error)
                return _answer(conversation, _fallback_text(conversation, _stop_warning(error)))
            conversation.llm_calls += 1
            conversation.model_used = response.model_used
            logger.info(
                "LLM call %d/%d for the question answered by %s",
                conversation.llm_calls,
                self._max_calls,
                response.model_used,
            )
            final_text = self._handle_message(conversation, response)
            if final_text is not None:
                return _answer(conversation, final_text)
        return _answer(conversation, _fallback_text(conversation, _budget_warning(self._max_calls)))

    def _complete(self, conversation: _Conversation, tool_choice: ToolChoice) -> LLMResponse:
        """One model call limited to the question's remaining request budget."""
        remaining = self._max_requests - conversation.requests_used
        try:
            response = self._llm.complete(
                conversation.messages,
                [RUN_SQL_TOOL],
                max_requests=remaining,
                skip_models=frozenset(conversation.skip_models),
                question_id=conversation.question_id,
                tool_choice=tool_choice,
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

    def _handle_message(self, conversation: _Conversation, response: LLMResponse) -> str | None:
        """Return the final answer text, or None when the loop must call the model again."""
        message = response.message
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
            return self._handle_empty_answer(conversation, response)

        sql = _sql_block(text) if not conversation.sql_executed else None
        if sql is None:
            reason = is_degenerate(text)
            if reason is not None:
                return self._handle_degenerate_answer(conversation, response, reason)
            return text
        # Fallback for models that print SQL instead of calling the tool.
        outcome = self._execute(conversation, sql)
        conversation.messages.append({"role": "assistant", "content": text})
        _append_user_message(conversation.messages, FINAL_ANSWER_REQUEST.format(outcome=outcome))
        return None

    def _handle_empty_answer(
        self, conversation: _Conversation, response: LLMResponse
    ) -> str | None:
        """An empty answer (or reasoning only) is a failure of that model for this question."""
        conversation.skip_models.add(response.requested_model)
        conversation.failures.append((response.requested_model, EMPTY_ANSWER_REASON))
        if conversation.last_result is not None:
            warning = f"Aviso: o modelo {response.model_used} devolveu uma resposta vazia."
            return _fallback_text(conversation, warning)
        _append_user_message(conversation.messages, EMPTY_ANSWER_NUDGE)
        return None

    def _handle_degenerate_answer(
        self, conversation: _Conversation, response: LLMResponse, reason: str
    ) -> str | None:
        """Discard a degenerate final answer; retried once, then answered in Python."""
        conversation.failures.append((response.requested_model, f"resposta degenerada: {reason}"))
        can_retry = (
            not conversation.degenerate_retried
            and conversation.llm_calls < self._max_calls
            and conversation.requests_used < self._max_requests
        )
        if can_retry:
            conversation.degenerate_retried = True
            conversation.skip_models.add(response.requested_model)
            _append_user_message(conversation.messages, DEGENERATE_ANSWER_NUDGE)
            return None
        return _fallback_text(conversation, f"{DISCARDED_ANSWER_PREFIX}{reason}")

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
        self._progress(SQL_WAIT_LABEL)
        try:
            expanded = expand_macros(query)
        except UnsafeQueryError as error:
            return _reject(conversation, query, error)
        started = SQL_CLOCK()
        try:
            result = self._db.run_query(expanded)
        except RECOVERABLE_QUERY_ERRORS as error:
            return _reject(conversation, expanded, error)
        finally:
            conversation.sql_ms += round((SQL_CLOCK() - started) * 1000)
        executed = result.sql or expanded
        conversation.sql_executed.append(executed)
        conversation.sql_log.append(SqlRecord(sql=executed))
        conversation.last_result = result
        return format_tool_result(result)


def _ignore_progress(_: str) -> None:
    return None


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
    """Answer built in Python when the model cannot finish: the warning plus the last result.

    Records the warning on the conversation, so the answer is known to be incomplete.
    """
    conversation.warning = warning
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
        warning=conversation.warning,
        question_id=conversation.question_id,
        sql_ms=conversation.sql_ms,
    )
