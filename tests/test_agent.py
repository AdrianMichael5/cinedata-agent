import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fakes import FAKE_MODEL, FakeLLM, text_message, tool_call_message, tool_calls_message
from openai.types.chat import ChatCompletionMessage

from cinedata_agent.agent import Agent, AgentAnswer, SqlRecord
from cinedata_agent.config import Settings
from cinedata_agent.db.base import QueryResult
from cinedata_agent.db.errors import QueryTimeoutError
from cinedata_agent.db.sqlite import SQLiteDatabase
from cinedata_agent.llm.errors import (
    AllModelsFailedError,
    AuthenticationError,
    PaymentRequiredError,
    QuotaExhaustedError,
)
from cinedata_agent.prompts.builder import build_system_prompt
from cinedata_agent.tools import (
    RUN_SQL_TOOL,
    TOOL_RESULT_MAX_CELL_CHARS,
    TOOL_RESULT_MAX_ROWS,
    format_tool_result,
)

QUESTION = "Quantos filmes existem no catálogo?"
COUNT_SQL = "SELECT COUNT(*) AS total FROM dim_movies"
TITLES_SQL = "SELECT titulo FROM dim_movies ORDER BY id"
NUMBERS_SQL = (
    "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 120) SELECT x FROM n"
)


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"REFERENCE_DATE": "2026-10-01", "max_llm_calls_per_question": 3}
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def db(sample_db) -> SQLiteDatabase:
    return SQLiteDatabase(sample_db, timeout_seconds=5, max_rows=100)


def ask(db, script, question: str = QUESTION, **settings: Any) -> tuple[AgentAnswer, FakeLLM]:
    llm = FakeLLM(script)
    answer = Agent(make_settings(**settings), db, llm).ask(question)
    return answer, llm


def tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [message for message in messages if message["role"] == "tool"]


class SpyDatabase:
    """Records the SQL it receives; answers with a fixed result or raises a scripted error."""

    dialect = "sqlite"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.queries: list[str] = []

    def run_query(self, sql: str) -> QueryResult:
        self.queries.append(sql)
        if self.error is not None:
            raise self.error
        return QueryResult(
            columns=("total",), rows=((7,),), truncated=False, elapsed_ms=1.0, sql=sql
        )


class TestHappyPath:
    def test_tool_call_then_text_answer_takes_two_calls(self, db):
        answer, llm = ask(
            db, [tool_call_message(COUNT_SQL), text_message("O catálogo tem 5 filmes.")]
        )

        assert answer.text == "O catálogo tem 5 filmes."
        assert answer.sql_executed == [COUNT_SQL]
        assert answer.model_used == FAKE_MODEL
        assert answer.llm_calls == 2
        assert llm.requests_sent == 2
        assert answer.last_result is not None
        assert answer.last_result.rows == ((5,),)

    def test_first_call_sends_system_prompt_question_and_run_sql_tool(self, db):
        _, llm = ask(db, [text_message("Não sei.")])

        settings = make_settings()
        assert llm.calls[0] == [
            {"role": "system", "content": build_system_prompt(settings)},
            {"role": "user", "content": QUESTION},
        ]
        assert llm.tools[0] == [RUN_SQL_TOOL]

    def test_query_result_goes_back_as_tool_message(self, db):
        _, llm = ask(db, [tool_call_message(COUNT_SQL), text_message("5 filmes.")])

        second_call = llm.calls[1]
        assistant = second_call[2]
        assert assistant["role"] == "assistant"
        assert assistant["tool_calls"][0]["id"] == "call_1"
        assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {
            "query": COUNT_SQL
        }
        [tool] = tool_messages(second_call)
        assert tool["tool_call_id"] == "call_1"
        assert tool["content"].endswith("\ntotal\n5")

    def test_text_without_tool_call_is_the_final_answer(self, db):
        answer, llm = ask(db, [text_message("Pergunta fora do escopo do catálogo.")])

        assert answer.text == "Pergunta fora do escopo do catálogo."
        assert answer.sql_executed == []
        assert answer.last_result is None
        assert answer.llm_calls == 1
        assert llm.requests_sent == 1

    def test_blank_question_is_rejected_without_calling_the_llm(self, db):
        llm = FakeLLM([])

        with pytest.raises(ValueError, match="vazia"):
            Agent(make_settings(), db, llm).ask("   ")

        assert llm.requests_sent == 0


class TestRunSqlTool:
    def test_schema_is_in_portuguese_with_a_query_argument(self):
        function = RUN_SQL_TOOL["function"]

        assert RUN_SQL_TOOL["type"] == "function"
        assert function["name"] == "run_sql"
        assert "somente leitura" in function["description"]
        assert function["parameters"]["required"] == ["query"]
        assert function["parameters"]["properties"]["query"]["type"] == "string"

    def test_macros_are_expanded_before_run_query(self):
        spy = SpyDatabase()
        sql = "SELECT COUNT(*) FROM dim_people WHERE nome_pessoa NOT IN {{NOMES_INVALIDOS}}"

        answer, _ = ask(spy, [tool_call_message(sql), text_message("7 pessoas.")])

        assert len(spy.queries) == 1
        assert "{{" not in spy.queries[0]
        assert "json_each" in spy.queries[0]
        assert answer.sql_executed == spy.queries


class TestSqlRecords:
    def test_records_the_sql_that_actually_ran(self, db):
        answer, _ = ask(
            db, [tool_call_message("select titulo\nfrom dim_movies -- todos"), text_message("Ok.")]
        )

        assert answer.sql_executed == ["SELECT titulo FROM dim_movies"]
        assert answer.sql_executed == [answer.last_result.sql]
        assert answer.sql_log == [SqlRecord(sql="SELECT titulo FROM dim_movies", rejection=None)]

    def test_records_expanded_macros(self, db):
        sql = "SELECT nome_genero FROM dim_genres WHERE nome_genero NOT IN {{NOMES_INVALIDOS}}"

        answer, _ = ask(db, [tool_call_message(sql), text_message("Nenhum.")])

        [executed] = answer.sql_executed
        assert "{{" not in executed
        assert "json_each" in executed.lower()

    def test_rejected_sql_is_logged_expanded_with_the_reason(self, db):
        bad = "DELETE FROM dim_movies WHERE titulo NOT IN {{NOMES_INVALIDOS}}"

        answer, _ = ask(
            db, [tool_call_message(bad), tool_call_message(COUNT_SQL), text_message("5 filmes.")]
        )

        rejected, executed = answer.sql_log
        assert rejected.rejection is not None
        assert "{{" not in rejected.sql and "json_each" in rejected.sql
        assert rejected.sql.startswith("DELETE FROM dim_movies")
        assert executed == SqlRecord(sql=COUNT_SQL, rejection=None)
        assert answer.sql_executed == [COUNT_SQL]

    def test_sql_error_reason_is_kept(self, db):
        answer, _ = ask(
            db, [tool_call_message("SELECT nome FROM dim_movies"), text_message("Falhou.")]
        )

        [record] = answer.sql_log
        assert record.sql == "SELECT nome FROM dim_movies"
        assert record.rejection is not None and "nome" in record.rejection
        assert answer.sql_executed == []

    def test_unknown_macro_is_logged_as_written(self, db):
        sql = "SELECT 1 WHERE 'a' NOT IN {{OUTRA}}"

        answer, _ = ask(db, [tool_call_message(sql), text_message("Ok.")])

        [record] = answer.sql_log
        assert record.sql == sql
        assert record.rejection is not None and "{{OUTRA}}" in record.rejection

    def test_sql_block_fallback_is_logged(self, db):
        answer, _ = ask(db, [text_message(f"```sql\n{COUNT_SQL}\n```"), text_message("5 filmes.")])

        assert answer.sql_log == [SqlRecord(sql=COUNT_SQL, rejection=None)]

    def test_several_tool_calls_in_one_response_all_run(self, db):
        answer, llm = ask(
            db,
            [
                tool_call_message(COUNT_SQL, TITLES_SQL),
                text_message("5 filmes, de Avatar a Elvis."),
            ],
        )

        tools = tool_messages(llm.calls[1])
        assert [tool["tool_call_id"] for tool in tools] == ["call_1", "call_2"]
        assert "Avatar" in tools[1]["content"]
        assert answer.sql_executed == [COUNT_SQL, TITLES_SQL]
        assert answer.last_result is not None
        assert answer.last_result.columns == ("titulo",)
        assert answer.llm_calls == 2


class TestErrorsGoBackToTheModel:
    def test_sql_error_is_returned_and_the_model_corrects_it(self, db):
        answer, llm = ask(
            db,
            [
                tool_call_message("SELECT nome FROM dim_movies"),
                tool_call_message(TITLES_SQL),
                text_message("Os títulos são Avatar, Barbie, Coco, Duna e Elvis."),
            ],
        )

        [error_tool] = tool_messages(llm.calls[1])
        assert error_tool["content"].startswith("ERRO")
        assert "nome" in error_tool["content"]
        assert answer.sql_executed == [TITLES_SQL]
        assert answer.llm_calls == 3

    def test_validation_error_is_returned(self, db):
        _, llm = ask(db, [tool_call_message("DELETE FROM dim_movies"), text_message("Não posso.")])

        [error_tool] = tool_messages(llm.calls[1])
        assert error_tool["content"].startswith("ERRO")

    def test_timeout_is_returned(self):
        spy = SpyDatabase(QueryTimeoutError("A consulta excedeu o tempo limite de 30 s."))

        answer, llm = ask(spy, [tool_call_message(COUNT_SQL), text_message("Demorou demais.")])

        [error_tool] = tool_messages(llm.calls[1])
        assert "tempo limite" in error_tool["content"]
        assert answer.sql_executed == []
        assert answer.llm_calls == 2

    def test_unknown_macro_is_returned(self, db):
        _, llm = ask(
            db, [tool_call_message("SELECT 1 WHERE 'a' NOT IN {{OUTRA}}"), text_message("Ok.")]
        )

        [error_tool] = tool_messages(llm.calls[1])
        assert "{{OUTRA}}" in error_tool["content"]

    @pytest.mark.parametrize(
        "raw_arguments",
        ['{"query": "SELECT 1"', "[]", '{"sql_text": "SELECT 1"}', '{"query": ""}'],
        ids=["invalid_json", "not_an_object", "missing_query", "blank_query"],
    )
    def test_bad_arguments_are_returned_without_touching_the_db(self, raw_arguments):
        spy = SpyDatabase()

        answer, llm = ask(
            spy, [tool_call_message(raw_arguments=raw_arguments), text_message("Desisto.")]
        )

        [error_tool] = tool_messages(llm.calls[1])
        assert error_tool["content"].startswith("ERRO")
        assert "query" in error_tool["content"]
        assert spy.queries == []
        assert answer.llm_calls == 2

    def test_sql_argument_alias_is_accepted(self, db):
        answer, _ = ask(
            db,
            [tool_call_message(raw_arguments=json.dumps({"sql": COUNT_SQL})), text_message("5.")],
        )

        assert answer.sql_executed == [COUNT_SQL]

    def test_unknown_tool_name_is_returned(self, db):
        answer, llm = ask(
            db, [tool_calls_message([("drop_table", "{}")]), text_message("Ok, sem consulta.")]
        )

        [error_tool] = tool_messages(llm.calls[1])
        assert "run_sql" in error_tool["content"]
        assert answer.sql_executed == []

    def test_custom_tool_call_is_returned_as_unknown(self, db):
        custom = ChatCompletionMessage.model_validate(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_x", "type": "custom", "custom": {"name": "run_sql", "input": "x"}}
                ],
            }
        )

        answer, llm = ask(db, [custom, text_message("Ok.")])

        [error_tool] = tool_messages(llm.calls[1])
        assert error_tool["tool_call_id"] == "call_x"
        assert "run_sql" in error_tool["content"]
        assert answer.sql_executed == []


class TestSqlBlockFallback:
    def test_sql_block_is_run_and_a_final_answer_is_requested(self, db):
        answer, llm = ask(
            db,
            [
                text_message(f"Vou consultar:\n```sql\n{COUNT_SQL}\n```"),
                text_message("O catálogo tem 5 filmes."),
            ],
        )

        assert answer.text == "O catálogo tem 5 filmes."
        assert answer.sql_executed == [COUNT_SQL]
        assert answer.llm_calls == 2
        follow_up = llm.calls[1][-1]
        assert follow_up["role"] == "user"
        assert "\ntotal\n5" in follow_up["content"]
        assert "resposta final" in follow_up["content"]

    def test_failed_sql_block_error_is_sent_back(self, db):
        answer, llm = ask(
            db,
            [
                text_message("```sql\nSELECT nome FROM dim_movies\n```"),
                text_message("Não consegui consultar."),
            ],
        )

        assert "ERRO" in llm.calls[1][-1]["content"]
        assert answer.text == "Não consegui consultar."
        assert answer.llm_calls == 2

    def test_sql_block_is_ignored_once_a_query_has_run(self, db):
        final = f"São 5 filmes, conforme a consulta:\n```sql\n{COUNT_SQL}\n```"

        answer, _ = ask(db, [tool_call_message(COUNT_SQL), text_message(final)])

        assert answer.text == final
        assert answer.sql_executed == [COUNT_SQL]
        assert answer.llm_calls == 2

    def test_sql_block_on_the_last_call_ends_with_the_python_answer(self, db):
        answer, llm = ask(
            db,
            [text_message(f"```sql\n{TITLES_SQL}\n```"), text_message("nunca chamado")],
            max_llm_calls_per_question=1,
        )

        assert answer.llm_calls == 1
        assert llm.requests_sent == 1
        assert answer.sql_executed == [TITLES_SQL]
        assert "não concluiu" in answer.text
        assert "Avatar" in answer.text


class TestBudget:
    def test_never_exceeds_the_call_budget(self, db):
        script = [tool_call_message(COUNT_SQL) for _ in range(4)]

        answer, llm = ask(db, script)

        assert answer.llm_calls == 3
        assert llm.requests_sent == 3
        assert len(llm.script) == 1

    def test_remaining_request_budget_is_passed_on_each_call(self, db):
        script = [tool_call_message(COUNT_SQL), tool_call_message(TITLES_SQL), text_message("5.")]

        _, llm = ask(db, script, max_requests_per_question=6)

        assert llm.budgets == [6, 5, 4]

    def test_spent_request_budget_with_a_result_returns_the_python_answer(self, db):
        script = [tool_call_message(COUNT_SQL), text_message("nunca chamado")]

        answer, llm = ask(db, script, max_requests_per_question=1)

        assert llm.budgets == [1, 0]
        assert llm.requests_sent == 1
        assert "MAX_REQUESTS_PER_QUESTION" in answer.text
        assert "\ntotal\n5" in answer.text

    def test_budget_comes_from_settings(self, db):
        script = [tool_call_message(COUNT_SQL) for _ in range(3)]

        answer, llm = ask(db, script, max_llm_calls_per_question=2)

        assert answer.llm_calls == 2
        assert llm.requests_sent == 2

    def test_exhausted_budget_answers_with_the_last_result_and_a_warning(self, db):
        script = [
            tool_call_message(TITLES_SQL),
            tool_call_message(COUNT_SQL),
            tool_call_message(COUNT_SQL + " WHERE ano > 2020"),
        ]

        answer, _ = ask(db, script)

        assert "não concluiu" in answer.text
        assert "3 chamadas" in answer.text
        assert answer.last_result is not None
        assert answer.last_result.rows == ((4,),)
        assert "total" in answer.text

    def test_exhausted_budget_without_any_result(self, db):
        script = [tool_call_message("SELECT nome FROM dim_movies") for _ in range(3)]

        answer, _ = ask(db, script)

        assert "não concluiu" in answer.text
        assert "nenhuma consulta" in answer.text
        assert answer.last_result is None
        assert answer.sql_executed == []

    def test_empty_text_is_not_a_final_answer(self, db):
        answer, llm = ask(db, [text_message("   "), text_message("5 filmes.")])

        assert answer.text == "5 filmes."
        assert answer.llm_calls == 2
        assert llm.calls[1][-1]["role"] == "user"
        assert "vazia" in llm.calls[1][-1]["content"]

    def test_last_call_asks_for_a_text_answer(self, db):
        _, llm = ask(
            db,
            [tool_call_message(COUNT_SQL), tool_call_message(TITLES_SQL), text_message("5.")],
        )

        assert not any("última chamada" in str(m.get("content")) for m in llm.calls[1])
        last = llm.calls[2][-1]
        assert last["role"] == "user"
        assert "última chamada" in last["content"]

    def test_single_call_budget_sends_no_last_call_note(self, db):
        _, llm = ask(db, [text_message("5.")], max_llm_calls_per_question=1)

        assert [m["role"] for m in llm.calls[0]] == ["system", "user"]

    def test_never_sends_two_user_messages_in_a_row(self, db):
        script = [text_message(" "), text_message(" "), text_message("5 filmes.")]

        _, llm = ask(db, script)

        for messages in llm.calls:
            roles = [m["role"] for m in messages]
            assert all(
                not (a == "user" and b == "user") for a, b in zip(roles, roles[1:], strict=False)
            ), roles
        assert "última chamada" in llm.calls[2][-1]["content"]


class TestToolCallIds:
    def test_empty_and_duplicate_ids_are_replaced_consistently(self, db):
        message = ChatCompletionMessage.model_validate(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_id,
                        "type": "function",
                        "function": {"name": "run_sql", "arguments": json.dumps({"query": sql})},
                    }
                    for tool_id, sql in (("", COUNT_SQL), ("dup", TITLES_SQL), ("dup", COUNT_SQL))
                ],
            }
        )

        _, llm = ask(db, [message, text_message("5 filmes.")])

        assistant = llm.calls[1][2]
        echoed = [call["id"] for call in assistant["tool_calls"]]
        answered = [tool["tool_call_id"] for tool in tool_messages(llm.calls[1])]
        assert echoed == answered
        assert len(set(echoed)) == 3
        assert all(echoed)
        assert echoed[1] == "dup"


class TestLlmUnavailableAfterAResult:
    def test_all_models_failed_after_a_result_returns_the_last_result(self, db):
        failure = AllModelsFailedError([("a/model:free", "HTTP 503: falha temporária")])

        answer, llm = ask(db, [tool_call_message(TITLES_SQL), failure])

        assert llm.requests_sent == 2
        assert answer.llm_calls == 1
        assert "indisponível" in answer.text
        assert "Avatar" in answer.text
        assert answer.sql_executed == [TITLES_SQL]

    def test_all_models_failed_before_any_result_is_raised(self, db):
        failure = AllModelsFailedError([("a/model:free", "HTTP 503: falha temporária")])
        llm = FakeLLM([failure])

        with pytest.raises(AllModelsFailedError):
            Agent(make_settings(), db, llm).ask(QUESTION)

    def test_payment_required_is_raised_even_after_a_result(self, db):
        llm = FakeLLM([tool_call_message(COUNT_SQL), PaymentRequiredError("saldo negativo")])

        with pytest.raises(PaymentRequiredError):
            Agent(make_settings(), db, llm).ask(QUESTION)


class TestFatalErrorsPropagate:
    def test_quota_exhausted_is_raised_without_another_call(self, db):
        reset = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
        llm = FakeLLM(
            [
                tool_call_message(COUNT_SQL),
                QuotaExhaustedError("Cota esgotada.", reset),
                text_message("nunca chamado"),
            ]
        )

        with pytest.raises(QuotaExhaustedError):
            Agent(make_settings(), db, llm).ask(QUESTION)

        assert llm.requests_sent == 2

    def test_authentication_error_is_raised(self, db):
        llm = FakeLLM([AuthenticationError("confira OPENROUTER_API_KEY no .env")])

        with pytest.raises(AuthenticationError):
            Agent(make_settings(), db, llm).ask(QUESTION)

        assert llm.requests_sent == 1


class TestToolResultFormat:
    def test_lists_columns_rows_and_total(self):
        result = QueryResult(
            columns=("titulo", "ano"),
            rows=(("Avatar", 2022), ("Barbie", None)),
            truncated=False,
            elapsed_ms=2.0,
        )

        text = format_tool_result(result)

        assert "titulo | ano" in text
        assert "Avatar | 2022" in text
        assert "Barbie | NULL" in text
        assert "2 linha(s)" in text
        assert "truncad" not in text

    def test_shows_at_most_50_rows(self, db):
        result = db.run_query(NUMBERS_SQL)

        text = format_tool_result(result)

        lines = text.splitlines()
        assert TOOL_RESULT_MAX_ROWS == 50
        assert "50" in lines and "51" not in lines
        assert "100 linha(s)" in text
        assert "50 primeiras" in text

    def test_warns_when_the_backend_truncated(self, db):
        result = db.run_query(NUMBERS_SQL)

        assert result.truncated
        assert "truncad" in format_tool_result(result)

    def test_empty_result_says_so(self):
        result = QueryResult(columns=("x",), rows=(), truncated=False, elapsed_ms=1.0)

        assert "0 linha(s)" in format_tool_result(result)

    def test_long_values_are_cut(self):
        result = QueryResult(
            columns=("sinopse",), rows=(("x" * 5000,),), truncated=False, elapsed_ms=1.0
        )

        last_line = format_tool_result(result).splitlines()[-1]

        assert TOOL_RESULT_MAX_CELL_CHARS == 200
        assert len(last_line) == TOOL_RESULT_MAX_CELL_CHARS
        assert last_line.endswith("…")

    def test_values_at_the_limit_are_kept(self):
        value = "y" * TOOL_RESULT_MAX_CELL_CHARS
        result = QueryResult(columns=("v",), rows=((value,),), truncated=False, elapsed_ms=1.0)

        assert format_tool_result(result).splitlines()[-1] == value

    def test_newlines_inside_values_do_not_break_rows(self):
        result = QueryResult(
            columns=("sinopse",), rows=(("linha 1\nlinha 2",),), truncated=False, elapsed_ms=1.0
        )

        assert "linha 1 linha 2" in format_tool_result(result)
