import logging
from datetime import UTC, datetime
from importlib import metadata

import httpx
import pytest
from fakes import FAKE_MODEL, FakeLLM, text_message, tool_call_message
from typer.testing import CliRunner

from cinedata_agent import cli
from cinedata_agent.cli import app
from cinedata_agent.llm.errors import (
    AllModelsFailedError,
    PaymentRequiredError,
    QuotaExhaustedError,
)

runner = CliRunner()

FAKE_KEY = "sk-or-v1-fake-key-for-cli-tests"


def use_mock_http(monkeypatch, handler) -> None:
    monkeypatch.setattr(
        cli, "make_http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )


COUNT_SQL = "SELECT COUNT(*) AS total FROM dim_movies"


class TestAskCommand:
    @pytest.fixture(autouse=True)
    def configure(self, monkeypatch, sample_db):
        monkeypatch.setenv("DB_PATH", str(sample_db))
        monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)

    def use_fake_llm(self, monkeypatch, script) -> FakeLLM:
        llm = FakeLLM(script)
        monkeypatch.setattr(cli, "LLMClient", lambda settings: llm)
        return llm

    def test_prints_answer_model_and_llm_calls(self, monkeypatch):
        llm = self.use_fake_llm(
            monkeypatch, [tool_call_message(COUNT_SQL), text_message("O catálogo tem 5 filmes.")]
        )

        result = runner.invoke(app, ["ask", "Quantos filmes existem?"])

        assert result.exit_code == 0
        assert "O catálogo tem 5 filmes." in result.output
        assert FAKE_MODEL in result.output
        assert "Chamadas ao LLM: 2" in result.output
        assert COUNT_SQL not in result.output
        assert llm.requests_sent == 2

    def test_show_sql_lists_executed_queries(self, monkeypatch):
        self.use_fake_llm(monkeypatch, [tool_call_message(COUNT_SQL), text_message("5 filmes.")])

        result = runner.invoke(app, ["ask", "Quantos filmes existem?", "--show-sql"])

        assert result.exit_code == 0
        assert COUNT_SQL in result.output

    def test_show_sql_says_when_nothing_ran(self, monkeypatch):
        self.use_fake_llm(monkeypatch, [text_message("Fora do escopo.")])

        result = runner.invoke(app, ["ask", "Qual a capital da França?", "--show-sql"])

        assert result.exit_code == 0
        assert "Nenhuma SQL executada" in result.output

    def test_answer_text_is_not_parsed_as_markup(self, monkeypatch):
        self.use_fake_llm(monkeypatch, [text_message("Filme [bold]Rec[/bold] lidera.")])

        result = runner.invoke(app, ["ask", "Qual filme lidera?"])

        assert "[bold]Rec[/bold]" in result.output

    @pytest.mark.parametrize(
        "error",
        [
            QuotaExhaustedError(
                "Cota [esgotada]: renova às 21:00.", datetime(2026, 10, 2, tzinfo=UTC)
            ),
            AllModelsFailedError([("a/model:free", "HTTP 503: falha temporária")]),
            PaymentRequiredError("Saldo negativo (HTTP 402)."),
        ],
        ids=["quota", "all_models_failed", "payment_required"],
    )
    def test_llm_errors_exit_with_their_message_after_one_call(self, monkeypatch, error):
        llm = self.use_fake_llm(monkeypatch, [error, text_message("nunca chamado")])

        result = runner.invoke(app, ["ask", "Quantos filmes existem?"])

        assert result.exit_code == 1
        assert str(error) in " ".join(result.output.split())
        assert llm.requests_sent == 1

    def test_missing_key_exits_before_any_request(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "")

        result = runner.invoke(app, ["ask", "Quantos filmes existem?"])

        assert result.exit_code == 1
        assert "OPENROUTER_API_KEY" in result.output

    def test_missing_database_exits_with_message(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "nada" / "cinerocket.db"))
        llm = self.use_fake_llm(monkeypatch, [])

        result = runner.invoke(app, ["ask", "Quantos filmes existem?"])

        assert result.exit_code == 1
        assert "não encontrado" in result.output
        assert llm.requests_sent == 0

    def test_blank_question_exits_without_calling_the_llm(self, monkeypatch):
        llm = self.use_fake_llm(monkeypatch, [])

        result = runner.invoke(app, ["ask", "   "])

        assert result.exit_code == 1
        assert "vazia" in result.output
        assert llm.requests_sent == 0


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_option_prints_installed_version(flag):
    result = runner.invoke(app, [flag])

    assert result.exit_code == 0
    assert result.output.strip() == f"cinedata-agent {metadata.version('cinedata-agent')}"


def test_commands_silence_sqlglot_warnings(monkeypatch, sample_db):
    sqlglot_logger = logging.getLogger("sqlglot")
    monkeypatch.setattr(sqlglot_logger, "level", logging.NOTSET)
    monkeypatch.setenv("DB_PATH", str(sample_db))

    result = runner.invoke(app, ["sql", "SELECT 1 AS um"])

    assert result.exit_code == 0
    assert sqlglot_logger.level == logging.ERROR


def test_help_lists_all_commands():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("ask", "sql", "quota", "models"):
        assert command in result.output


class TestSqlCommand:
    @pytest.fixture(autouse=True)
    def use_sample_db(self, monkeypatch, sample_db):
        monkeypatch.setenv("DB_PATH", str(sample_db))

    def test_prints_table_row_count_and_time(self):
        result = runner.invoke(app, ["sql", "SELECT id, titulo FROM dim_movies ORDER BY id"])

        assert result.exit_code == 0
        assert "Avatar" in result.output
        assert "Elvis" in result.output
        assert "5 linha(s)" in result.output
        assert "ms" in result.output
        assert "truncad" not in result.output

    def test_warns_when_result_is_truncated(self, monkeypatch):
        monkeypatch.setenv("MAX_ROWS", "2")

        result = runner.invoke(app, ["sql", "SELECT titulo FROM dim_movies ORDER BY id"])

        assert result.exit_code == 0
        assert "2 linha(s)" in result.output
        assert "truncad" in result.output
        assert "Coco" not in result.output

    def test_shows_null_values(self):
        result = runner.invoke(app, ["sql", "SELECT NULL AS vazio"])

        assert result.exit_code == 0
        assert "NULL" in result.output

    def test_blocked_sql_exits_with_reason(self):
        result = runner.invoke(app, ["sql", "DELETE FROM dim_movies"])

        assert result.exit_code == 1
        assert "não permitida" in result.output

    def test_sqlite_error_exits_with_message(self):
        result = runner.invoke(app, ["sql", "SELECT nao_existe FROM dim_movies"])

        assert result.exit_code == 1
        assert "no such column" in result.output

    def test_missing_database_explains_where_to_put_it(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "nada" / "cinerocket.db"))

        result = runner.invoke(app, ["sql", "SELECT 1"])

        assert result.exit_code == 1
        assert "cinerocket.db" in result.output

    def test_databricks_backend_reports_optional_extra(self, monkeypatch):
        monkeypatch.setenv("DB_BACKEND", "databricks")

        result = runner.invoke(app, ["sql", "SELECT 1"])

        assert result.exit_code == 1
        assert "extra opcional" in result.output


class TestQuotaCommand:
    @pytest.fixture(autouse=True)
    def configure_key(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)

    def test_shows_usage_and_next_reset(self, monkeypatch):
        payload = {
            "data": {"free_model_daily_requests": {"used": 12, "limit": 50, "remaining": 38}}
        }
        use_mock_http(monkeypatch, lambda request: httpx.Response(200, json=payload))

        result = runner.invoke(app, ["quota"])

        assert result.exit_code == 0
        for expected in ("12", "50", "38", "21:00", "Brasília"):
            assert expected in result.output
        assert FAKE_KEY not in result.output

    def test_invalid_key_exits_with_clear_message(self, monkeypatch):
        use_mock_http(monkeypatch, lambda request: httpx.Response(401, json={}))

        result = runner.invoke(app, ["quota"])

        assert result.exit_code == 1
        assert "Chave inválida ou ausente" in result.output
        assert FAKE_KEY not in result.output

    def test_missing_key_exits_without_request(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "")

        def handler(request):
            raise AssertionError("no request expected without a key")

        use_mock_http(monkeypatch, handler)

        result = runner.invoke(app, ["quota"])

        assert result.exit_code == 1
        assert "OPENROUTER_API_KEY" in result.output


class TestModelsCommand:
    CATALOG = {
        "data": [
            {
                "id": "z-ai/glm-5.2:free",
                "context_length": 128000,
                "supported_parameters": ["tools", "tool_choice"],
            },
            {
                "id": "google/gemma-4-26b-a4b-it:free",
                "context_length": 96000,
                "supported_parameters": ["temperature"],
            },
        ]
    }

    def test_shows_free_tool_models_and_diagnostics(self, monkeypatch):
        monkeypatch.setenv(
            "LLM_MODELS", "z-ai/glm-5.2:free,google/gemma-4-26b-a4b-it:free,nao/existe:free"
        )
        use_mock_http(monkeypatch, lambda request: httpx.Response(200, json=self.CATALOG))

        result = runner.invoke(app, ["models"])

        assert result.exit_code == 0
        assert "z-ai/glm-5.2:free" in result.output
        assert "128000" in result.output
        assert "não aceita tools" in result.output
        assert "não encontrado" in result.output

    def test_api_error_exits_1(self, monkeypatch):
        use_mock_http(monkeypatch, lambda request: httpx.Response(503, json={}))

        result = runner.invoke(app, ["models"])

        assert result.exit_code == 1
        assert "503" in result.output

    def test_warns_when_no_configured_model_is_usable(self, monkeypatch):
        monkeypatch.setenv("LLM_MODELS", "google/gemma-4-26b-a4b-it:free,openrouter/free")
        use_mock_http(monkeypatch, lambda request: httpx.Response(200, json=self.CATALOG))

        result = runner.invoke(app, ["models"])

        assert result.exit_code == 0
        assert "Nenhum modelo da LLM_MODELS" in result.output


class TestInvalidSettings:
    @pytest.mark.parametrize("command", [["sql", "SELECT 1"], ["quota"], ["models"]])
    def test_invalid_env_value_shows_field_without_echoing_it(self, monkeypatch, command):
        monkeypatch.setenv("MAX_ROWS", "valor-secreto-invalido")

        result = runner.invoke(app, command)

        assert result.exit_code == 1
        assert "Configuração inválida" in result.output
        assert "MAX_ROWS" in result.output
        assert "valor-secreto-invalido" not in result.output
        assert "Traceback" not in result.output
