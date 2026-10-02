import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from cinedata_agent.agent import AgentAnswer, SqlRecord
from cinedata_agent.cache import AnswerCache, CacheKeyParts, cache_key, normalize_question
from cinedata_agent.config import Settings
from cinedata_agent.db.base import QueryResult

SQL = "SELECT titulo, receita_brl FROM dim_movies ORDER BY receita_brl DESC LIMIT 10"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"REFERENCE_DATE": "2026-10-01"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_answer(
    text: str = "Avatar lidera com R$ 15 bilhões.",
    sql: list[str] | None = None,
    warning: str | None = None,
    rows: int = 3,
) -> AgentAnswer:
    executed = [SQL] if sql is None else sql
    result = QueryResult(
        columns=("titulo", "receita_brl"),
        rows=tuple((f"Filme {index}", 1000.5 * index) for index in range(rows)),
        truncated=False,
        elapsed_ms=3.0,
        sql=executed[-1] if executed else "",
    )
    return AgentAnswer(
        text=text,
        sql_executed=executed,
        model_used="vendor/model:free",
        llm_calls=2,
        last_result=result if executed else None,
        sql_log=[SqlRecord(sql=item) for item in executed],
        warning=warning,
    )


@pytest.fixture
def cache(tmp_path) -> AnswerCache:
    return AnswerCache(tmp_path / ".cache", max_rows=200)


class TestNormalizeQuestion:
    @pytest.mark.parametrize(
        "variant",
        [
            "Top 10 filmes com maior receita em R$",
            "top 10 filmes com maior receita em r$?",
            "  TOP 10   filmes\tcom maior receita em R$ ?? ",
            "Top 10 filmes com maior receita em R$!",
            "Top 10 ﬁlmes com maior receita em R$…",
        ],
    )
    def test_equivalent_questions_normalize_alike(self, variant):
        assert normalize_question(variant) == "top 10 filmes com maior receita em r$"

    def test_inner_punctuation_is_kept(self):
        assert normalize_question("Filmes de 2010-2020, por gênero?") == (
            "filmes de 2010-2020, por gênero"
        )

    def test_different_questions_stay_different(self):
        assert normalize_question("Top 10 por receita") != normalize_question("Top 5 por receita")


class TestCacheKey:
    def parts(self, **overrides: Any) -> CacheKeyParts:
        values: dict[str, Any] = {
            "question": "Top 10 filmes?",
            "reference_date": date(2026, 10, 1),
            "system_prompt": "prompt v1",
            "backend": "sqlite",
        }
        values.update(overrides)
        return CacheKeyParts(**values)

    def test_is_a_sha256_hex_digest(self):
        key = cache_key(self.parts())

        assert len(key) == 64
        assert int(key, 16) >= 0

    def test_normalized_variants_share_a_key(self):
        assert cache_key(self.parts()) == cache_key(self.parts(question="  TOP 10 filmes "))

    @pytest.mark.parametrize(
        "change",
        [
            {"question": "Top 5 filmes?"},
            {"reference_date": date(2026, 9, 30)},
            {"system_prompt": "prompt v2"},
            {"backend": "databricks"},
        ],
        ids=["question", "reference_date", "prompt", "backend"],
    )
    def test_each_component_changes_the_key(self, change):
        assert cache_key(self.parts()) != cache_key(self.parts(**change))

    def test_rendered_prompt_change_invalidates_old_entries(self, cache):
        question = "Top 10 filmes?"
        old_settings = make_settings(max_rows=200)
        cache.put(cache.key_for(old_settings, question), question, make_answer(), 2)

        new_settings = make_settings(max_rows=150)

        assert cache.get(cache.key_for(old_settings, question)) is not None
        assert cache.get(cache.key_for(new_settings, question)) is None


class TestAnswerCache:
    def test_miss_on_an_empty_cache(self, cache):
        assert cache.get("0" * 64) is None

    def test_hit_returns_what_was_stored(self, cache, tmp_path):
        key = cache.key_for(make_settings(), "Top 10 filmes com maior receita em R$")

        stored = cache.put(key, "Top 10 filmes com maior receita em R$", make_answer(), 3)
        hit = cache.get(key)

        assert stored is True
        assert (tmp_path / ".cache" / "answers" / f"{key}.json").is_file()
        assert hit is not None
        assert hit.question == "Top 10 filmes com maior receita em R$"
        assert hit.text == "Avatar lidera com R$ 15 bilhões."
        assert hit.sql_executed == [SQL]
        assert hit.model_used == "vendor/model:free"
        assert hit.llm_calls == 2
        assert hit.requests_sent == 3
        assert hit.columns == ["titulo", "receita_brl"]
        assert hit.rows == [["Filme 0", 0.0], ["Filme 1", 1000.5], ["Filme 2", 2001.0]]
        assert hit.created_at.tzinfo is not None

    def test_rows_are_capped_at_max_rows(self, tmp_path):
        small = AnswerCache(tmp_path / ".cache", max_rows=2)
        key = small.key_for(make_settings(), "q")

        small.put(key, "q", make_answer(rows=5), 1)

        hit = small.get(key)
        assert hit is not None
        assert len(hit.rows) == 2

    @pytest.mark.parametrize(
        "answer",
        [
            make_answer(sql=[]),
            make_answer(text="   "),
            make_answer(warning="Aviso: o modelo não concluiu a resposta."),
        ],
        ids=["no_sql", "empty_text", "warning"],
    )
    def test_incomplete_answers_are_not_stored(self, cache, tmp_path, answer):
        key = cache.key_for(make_settings(), "q")

        stored = cache.put(key, "q", answer, 2)

        assert stored is False
        assert cache.get(key) is None
        assert not (tmp_path / ".cache" / "answers").exists()

    @pytest.mark.parametrize(
        "content",
        ["{not json", "[1, 2]", json.dumps({"text": "sem os outros campos"}), ""],
        ids=["invalid_json", "not_an_object", "missing_fields", "empty"],
    )
    def test_corrupted_file_is_a_miss_with_a_warning(self, cache, tmp_path, caplog, content):
        key = cache.key_for(make_settings(), "q")
        path = tmp_path / ".cache" / "answers" / f"{key}.json"
        path.parent.mkdir(parents=True)
        path.write_text(content, encoding="utf-8")
        caplog.set_level(logging.WARNING, logger="cinedata_agent.cache")

        assert cache.get(key) is None
        assert any("cache" in record.getMessage().lower() for record in caplog.records)

    @pytest.mark.parametrize(
        "change",
        [
            {"version": 999},
            {"rows": [["ok"], "não é linha"]},
            {"llm_calls": "2"},
            {"llm_calls": True},
            {"columns": ["titulo", 3]},
            {"sql_executed": [1]},
            {"created_at": "ontem"},
        ],
        ids=["version", "row", "calls_text", "calls_bool", "columns", "sql", "created_at"],
    )
    def test_entry_with_wrong_types_is_a_miss(self, cache, tmp_path, change):
        key = cache.key_for(make_settings(), "q")
        cache.put(key, "q", make_answer(), 2)
        path = tmp_path / ".cache" / "answers" / f"{key}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**data, **change}), encoding="utf-8")

        assert cache.get(key) is None

    def test_unwritable_cache_does_not_raise(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        cache = AnswerCache(blocker, max_rows=200)

        assert cache.put(cache.key_for(make_settings(), "q"), "q", make_answer(), 2) is False

    def test_clear_removes_every_entry(self, cache, tmp_path):
        for question in ("a", "b", "c"):
            cache.put(cache.key_for(make_settings(), question), question, make_answer(), 2)

        removed = cache.clear()

        assert removed == 3
        assert list((tmp_path / ".cache" / "answers").glob("*.json")) == []

    def test_clear_on_a_missing_cache_removes_nothing(self, cache):
        assert cache.clear() == 0

    def test_entry_file_is_readable_json_without_secrets(self, tmp_path):
        settings = make_settings(openrouter_api_key="sk-or-v1-secret-key-for-cache-tests")
        cache = AnswerCache(tmp_path / ".cache", max_rows=200)
        key = cache.key_for(settings, "q")

        cache.put(key, "q", make_answer(), 2)

        raw = (tmp_path / ".cache" / "answers" / f"{key}.json").read_text(encoding="utf-8")
        assert "sk-or-v1" not in raw
        assert json.loads(raw)["question"] == "q"


def test_cache_dir_comes_from_settings(tmp_path):
    settings = make_settings(cache_dir=tmp_path / "meu-cache")

    cache = AnswerCache.from_settings(settings)

    assert cache.directory == Path(tmp_path / "meu-cache")
    assert cache.max_rows == settings.max_rows
