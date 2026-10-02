from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from cinedata_agent.config import DEFAULT_LLM_MODELS, Settings, get_settings

FAKE_KEY = "sk-or-v1-fake-key-for-tests"
ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def make_settings() -> Settings:
    return Settings(_env_file=None)


class TestDefaults:
    def test_loads_without_env_file(self):
        settings = make_settings()

        assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
        assert settings.llm_models == list(DEFAULT_LLM_MODELS)
        assert settings.db_backend == "sqlite"
        assert settings.db_path == Path("data/cinerocket.db")
        assert settings.query_timeout_seconds == 30
        assert settings.max_rows == 200
        assert settings.max_llm_calls_per_question == 3
        assert settings.max_requests_per_question == 6
        assert settings.cache_dir == Path(".cache")
        assert settings.log_level == "INFO"

    def test_secrets_default_to_empty(self):
        settings = make_settings()

        assert settings.openrouter_api_key.get_secret_value() == ""
        assert settings.databricks_token.get_secret_value() == ""

    def test_default_models_end_with_openrouter_free(self):
        assert DEFAULT_LLM_MODELS[-1] == "openrouter/free"

    def test_default_models_fallback_order(self):
        assert DEFAULT_LLM_MODELS == (
            "nvidia/nemotron-3.5-lightning:free",
            "qwen/qwen3.8-27b:free",
            "google/gemma-4-26b-a4b-it:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
            "openrouter/free",
        )

    def test_env_example_lists_the_default_models(self):
        lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        values = [line.split("=", 1)[1] for line in lines if line.startswith("LLM_MODELS=")]

        assert values == [",".join(DEFAULT_LLM_MODELS)]

    def test_env_example_sets_the_request_cap(self):
        lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()

        assert "MAX_REQUESTS_PER_QUESTION=6" in lines

    def test_request_cap_reads_from_env(self, monkeypatch):
        monkeypatch.setenv("MAX_REQUESTS_PER_QUESTION", "9")

        assert make_settings().max_requests_per_question == 9

    @pytest.mark.parametrize("raw", ["0", "-1"])
    def test_request_cap_must_be_positive(self, monkeypatch, raw):
        monkeypatch.setenv("MAX_REQUESTS_PER_QUESTION", raw)

        with pytest.raises(ValidationError):
            make_settings()


class TestLlmModels:
    def test_parses_comma_separated_list(self, monkeypatch):
        monkeypatch.setenv("LLM_MODELS", "a/model:free,b/model:free")

        assert make_settings().llm_models == ["a/model:free", "b/model:free"]

    def test_strips_spaces_and_drops_empty_items(self, monkeypatch):
        monkeypatch.setenv("LLM_MODELS", " a/model:free , ,b/model:free, ")

        assert make_settings().llm_models == ["a/model:free", "b/model:free"]

    def test_drops_repeated_models_keeping_first_position(self, monkeypatch):
        monkeypatch.setenv(
            "LLM_MODELS", "b/model:free, a/model:free,,b/model:free , a/model:free,c"
        )

        assert make_settings().llm_models == ["b/model:free", "a/model:free", "c"]

    def test_rejects_a_value_that_is_not_text_or_a_list(self):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, llm_models=42)

    def test_drops_repeated_models_given_as_a_list(self):
        settings = Settings(_env_file=None, llm_models=["x:free", " x:free ", "", "y:free"])

        assert settings.llm_models == ["x:free", "y:free"]

    @pytest.mark.parametrize("raw", ["", " , ,"])
    def test_rejects_empty_list(self, monkeypatch, raw):
        monkeypatch.setenv("LLM_MODELS", raw)

        with pytest.raises(ValidationError):
            make_settings()


class TestReferenceDate:
    def test_unset_returns_today(self):
        assert make_settings().reference_date == date.today()

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_blank_returns_today(self, monkeypatch, raw):
        monkeypatch.setenv("REFERENCE_DATE", raw)

        assert make_settings().reference_date == date.today()

    def test_explicit_iso_date(self, monkeypatch):
        monkeypatch.setenv("REFERENCE_DATE", "2026-10-01")

        assert make_settings().reference_date == date(2026, 10, 1)

    def test_rejects_invalid_date(self, monkeypatch):
        monkeypatch.setenv("REFERENCE_DATE", "01/10/2026")

        with pytest.raises(ValidationError):
            make_settings()


class TestValidation:
    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("QUERY_TIMEOUT_SECONDS", "0"),
            ("MAX_ROWS", "-1"),
            ("MAX_LLM_CALLS_PER_QUESTION", "0"),
            ("DB_BACKEND", "postgres"),
            ("LOG_LEVEL", "LOUD"),
        ],
    )
    def test_rejects_invalid_values(self, monkeypatch, name, value):
        monkeypatch.setenv(name, value)

        with pytest.raises(ValidationError):
            make_settings()

    def test_accepts_databricks_backend(self, monkeypatch):
        monkeypatch.setenv("DB_BACKEND", "databricks")

        assert make_settings().db_backend == "databricks"

    def test_log_level_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "debug")

        assert make_settings().log_level == "DEBUG"

    def test_settings_are_immutable(self):
        settings = make_settings()

        with pytest.raises(ValidationError):
            settings.max_rows = 10


class TestSecrets:
    def test_api_key_not_exposed_in_repr_or_str(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-fake-token")

        settings = make_settings()

        assert settings.openrouter_api_key.get_secret_value() == FAKE_KEY
        for text in (repr(settings), str(settings)):
            assert FAKE_KEY not in text
            assert "dapi-fake-token" not in text


class TestEnvFile:
    def test_reads_values_from_given_env_file(self, tmp_path):
        env_file = tmp_path / "synthetic.env"
        env_file.write_text("MAX_ROWS=50\nREFERENCE_DATE=2026-10-01\n", encoding="utf-8")

        settings = Settings(_env_file=env_file)

        assert settings.max_rows == 50
        assert settings.reference_date == date(2026, 10, 1)

    def test_environment_overrides_env_file(self, tmp_path, monkeypatch):
        env_file = tmp_path / "synthetic.env"
        env_file.write_text("MAX_ROWS=50\n", encoding="utf-8")
        monkeypatch.setenv("MAX_ROWS", "75")

        assert Settings(_env_file=env_file).max_rows == 75


class TestGetSettings:
    def test_returns_cached_instance(self):
        get_settings.cache_clear()

        assert get_settings() is get_settings()

        get_settings.cache_clear()
