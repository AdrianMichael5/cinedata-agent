import logging
from importlib import metadata

import httpx
import pytest
from typer.testing import CliRunner

from cinedata_agent import cli
from cinedata_agent.cli import app

runner = CliRunner()

FAKE_KEY = "sk-or-v1-fake-key-for-cli-tests"


def use_mock_http(monkeypatch, handler) -> None:
    monkeypatch.setattr(
        cli, "make_http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_ask_is_still_a_stub():
    result = runner.invoke(app, ["ask", "Top 10 filmes com maior receita"])

    assert result.exit_code == 1
    assert "não implementado" in result.output


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
