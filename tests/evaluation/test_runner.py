import io
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeLLM, text_message, tool_call_message
from rich.console import Console

from cinedata_agent.cache import AnswerCache
from cinedata_agent.db.sqlite import SQLiteDatabase
from cinedata_agent.evaluation.gabarito import load_gabarito
from cinedata_agent.evaluation.main import _databases, main, parse_args
from cinedata_agent.evaluation.runner import (
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_REFUSED,
    EvalDependencies,
    EvalOptions,
    evaluation_settings,
    run_evaluation,
)
from cinedata_agent.llm.errors import (
    AllModelsFailedError,
    AuthenticationError,
    QuotaExhaustedError,
)
from cinedata_agent.llm.openrouter_account import QuotaInfo

RECENT_SQL = "SELECT titulo, ano FROM dim_movies ORDER BY ano DESC, titulo LIMIT 3"
ALPHA_SQL = "SELECT titulo, ano FROM dim_movies ORDER BY titulo LIMIT 3"
OLD_SQL = "SELECT titulo, ano FROM dim_movies ORDER BY ano, titulo LIMIT 3"
RENAMED_SQL = (
    "SELECT ano AS lancamento, upper(titulo) AS filme FROM dim_movies "
    "ORDER BY ano DESC, titulo LIMIT 3"
)
FIXED_NOW = datetime(2026, 10, 2, 18, 30, 5, tzinfo=UTC)


def reference(title: str, sql: str, rows: list[list[Any]] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"titulo": title, "sql": sql}
    if rows is not None:
        data["resultado"] = {"colunas": ["titulo", "ano"], "linhas": rows}
    return data


GABARITO = [
    {
        "id": "Q01",
        "categoria": "Teste",
        "pergunta": "Quais os 3 filmes mais recentes?",
        "decisoes": [],
        "principal": reference(
            "Mais recentes", RECENT_SQL, [["Barbie", 2023], ["Avatar", 2022], ["Elvis", 2022]]
        ),
        "variantes": [reference("Alfabética", ALPHA_SQL)],
    },
    {
        "id": "Q03",
        "categoria": "Teste",
        "pergunta": "Quais os 3 filmes mais antigos?",
        "decisoes": [],
        "principal": reference("Sem filtro", ALPHA_SQL),
        "variantes": [reference("Com orçamento >= US$ 100 mil", OLD_SQL)],
    },
    {
        "id": "Q04",
        "categoria": "Teste",
        "pergunta": "Quais os 3 primeiros filmes em ordem alfabética?",
        "decisoes": [],
        "principal": reference("Alfabética", ALPHA_SQL, [["Avatar", 2022], ["Barbie", 2023]]),
        "variantes": [],
    },
]


def answer(sql: str, text: str = "Resposta.") -> list:
    return [tool_call_message(sql), text_message(text)]


class Harness:
    def __init__(self, tmp_path: Path, sample_db: Path) -> None:
        self.gabarito_path = tmp_path / "gabarito.json"
        self.gabarito_path.write_text(json.dumps(GABARITO, ensure_ascii=False), encoding="utf-8")
        self.output_dir = tmp_path / "results"
        self.settings = evaluation_settings(_env_file=None, db_path=sample_db)
        self.database = SQLiteDatabase(sample_db, timeout_seconds=5, max_rows=200)
        self.cache = AnswerCache(tmp_path / ".cache", max_rows=200)
        self.script: list = []
        self.llms: list[FakeLLM] = []
        self.quotas: list[Any] = [50]
        self.quota_error: Exception | None = None
        self.sleeps: list[float] = []
        self.output = io.StringIO()

    def make_llm(self) -> FakeLLM:
        llm = FakeLLM(self.script)
        self.llms.append(llm)
        return llm

    def get_quota(self) -> QuotaInfo:
        if self.quota_error is not None:
            raise self.quota_error
        remaining = self.quotas.pop(0) if len(self.quotas) > 1 else self.quotas[0]
        if isinstance(remaining, Exception):
            raise remaining
        return QuotaInfo(used=50 - remaining, limit=50, remaining=remaining)

    def run(self, ids: str | None = None, **options: Any) -> int:
        values: dict[str, Any] = {
            "ids": tuple(ids.split(",")) if ids else None,
            "limit": None,
            "sleep_seconds": 0.0,
            "use_cache": True,
            "dry_run": False,
        }
        values.update(options)
        deps = EvalDependencies(
            settings=self.settings,
            database=self.database,
            reference_database=self.database,
            make_llm=self.make_llm,
            get_quota=self.get_quota,
            cache=self.cache,
            sleep=self.sleeps.append,
            console=Console(file=self.output, width=200, emoji=False),
            output_dir=self.output_dir,
            now=lambda: FIXED_NOW,
        )
        return run_evaluation(load_gabarito(self.gabarito_path), EvalOptions(**values), deps)

    @property
    def report(self) -> dict[str, Any]:
        [path] = self.output_dir.glob("*.json")
        return json.loads(path.read_text(encoding="utf-8"))

    @property
    def markdown(self) -> str:
        return (self.output_dir / "latest.md").read_text(encoding="utf-8")

    @property
    def requests_sent(self) -> int:
        return sum(llm.requests_sent for llm in self.llms)


@pytest.fixture
def harness(tmp_path, sample_db) -> Harness:
    return Harness(tmp_path, sample_db)


def outcome(report: dict[str, Any], question_id: str) -> dict[str, Any]:
    return next(item for item in report["questions"] if item["id"] == question_id)


class TestApproval:
    def test_matching_answer_is_approved_against_the_expected_reference(self, harness):
        harness.script = answer(RECENT_SQL, "Barbie, Avatar e Elvis.")

        code = harness.run(ids="Q01")

        q01 = outcome(harness.report, "Q01")
        assert code == EXIT_OK
        assert q01["approved"] is True
        assert q01["top1_ok"] is True
        assert q01["recall"] == 1.0
        assert q01["matched_role"] == "esperada"
        assert q01["matched_label"] == "Mais recentes"
        assert q01["requests"] == 2
        assert q01["from_cache"] is False
        assert q01["answer_text"] == "Barbie, Avatar e Elvis."
        assert q01["sql_executed"] == [RECENT_SQL]

    def test_wrong_answer_is_rejected(self, harness):
        # Elvis, Duna, Coco: neither the expected ranking nor the alphabetical variant.
        harness.script = answer("SELECT titulo, ano FROM dim_movies ORDER BY id DESC LIMIT 3")

        harness.run(ids="Q01")

        q01 = outcome(harness.report, "Q01")
        assert q01["approved"] is False
        assert q01["matched_role"] is None

    def test_renamed_and_reordered_columns_still_match(self, harness):
        harness.script = answer(RENAMED_SQL)

        harness.run(ids="Q01")

        q01 = outcome(harness.report, "Q01")
        assert q01["approved"] is True
        assert q01["matched_columns"] == ["filme"]

    def test_principal_is_accepted_as_alternative_where_the_variant_is_expected(self, harness):
        harness.script = answer(ALPHA_SQL)

        harness.run(ids="Q03")

        q03 = outcome(harness.report, "Q03")
        assert q03["approved"] is True
        assert q03["matched_role"] == "alternativa"
        assert q03["matched_label"] == "Sem filtro"

    def test_variant_alternative_is_recorded_for_other_questions(self, harness):
        harness.script = answer(ALPHA_SQL)

        harness.run(ids="Q01")

        q01 = outcome(harness.report, "Q01")
        assert q01["matched_role"] == "alternativa"
        assert q01["matched_label"] == "Alfabética"


class TestReferences:
    def test_stored_results_are_checked_against_the_reference_sql(self, harness):
        harness.script = answer(RECENT_SQL) + answer(ALPHA_SQL)

        harness.run(ids="Q01,Q04")

        checks_q01 = outcome(harness.report, "Q01")["reference_checks"]
        checks_q04 = outcome(harness.report, "Q04")["reference_checks"]
        assert checks_q01 == {"Mais recentes": True, "Alfabética": None}
        assert checks_q04 == {"Alfabética": False}
        assert "não bate" in harness.markdown


class TestFailures:
    def test_broken_reference_sql_is_reported(self, harness, tmp_path):
        broken = json.loads(harness.gabarito_path.read_text(encoding="utf-8"))
        broken[0]["variantes"][0]["sql"] = "SELECT nada FROM dim_movies"
        harness.gabarito_path.write_text(json.dumps(broken), encoding="utf-8")
        harness.script = answer(RECENT_SQL)

        harness.run(ids="Q01")

        q01 = outcome(harness.report, "Q01")
        assert q01["approved"] is True
        assert q01["reference_checks"]["Alfabética"] is False
        assert "referência 'Alfabética' falhou" in q01["error"]

    def test_agent_error_fails_the_question_and_the_run_goes_on(self, harness):
        failure = AllModelsFailedError([("a/model:free", "HTTP 503: falha temporária")])
        harness.script = [failure, *answer(ALPHA_SQL)]

        code = harness.run(ids="Q01,Q04")

        assert code == EXIT_OK
        q01 = outcome(harness.report, "Q01")
        assert q01["approved"] is False
        assert q01["error"].startswith("AllModelsFailedError")
        assert outcome(harness.report, "Q04")["approved"] is True
        assert "Erro: AllModelsFailedError" in harness.markdown

    def test_missing_database_outside_dry_run_is_a_programming_error(self, harness):
        harness.database = None

        with pytest.raises(RuntimeError):
            harness.run(ids="Q01")


class TestMain:
    def write_eval_dir(self, tmp_path: Path) -> Path:
        eval_dir = tmp_path / "eval"
        eval_dir.mkdir()
        (eval_dir / "gabarito.json").write_text(
            json.dumps(GABARITO, ensure_ascii=False), encoding="utf-8"
        )
        return eval_dir

    def test_dry_run_end_to_end_without_a_key(self, tmp_path, capsys):
        eval_dir = self.write_eval_dir(tmp_path)

        code = main(["--dry-run"], eval_dir=eval_dir)

        output = " ".join(capsys.readouterr().out.split())
        assert code == EXIT_OK
        assert "REFERENCE_DATE 2026-10-01" in output
        assert "3 pergunta(s) sem cache" in output
        assert "Cota atual: indisponível" in output
        assert not (eval_dir / "results").exists()

    def test_missing_database_is_refused(self, tmp_path, monkeypatch, capsys):
        eval_dir = self.write_eval_dir(tmp_path)
        monkeypatch.setenv("DB_PATH", str(tmp_path / "nada.db"))

        code = main([], eval_dir=eval_dir)

        assert code == EXIT_REFUSED
        assert "não encontrado" in capsys.readouterr().out

    def test_missing_gabarito_is_refused(self, tmp_path, capsys):
        code = main(["--dry-run"], eval_dir=tmp_path / "vazio")

        assert code == EXIT_REFUSED
        assert "Não foi possível iniciar" in capsys.readouterr().out

    def test_opens_both_databases_outside_dry_run(self, sample_db):
        settings = evaluation_settings(_env_file=None, db_path=sample_db)

        agent_db, reference_db = _databases(settings, dry_run=False)

        assert isinstance(agent_db, SQLiteDatabase) and isinstance(reference_db, SQLiteDatabase)
        assert agent_db.timeout_seconds == settings.query_timeout_seconds
        assert reference_db.max_rows > settings.max_rows
        assert reference_db.timeout_seconds == 300

    @pytest.mark.parametrize("raw", ["0", "-2", "x"])
    def test_limit_must_be_positive(self, raw):
        with pytest.raises(SystemExit):
            parse_args(["--limit", raw])


class TestCache:
    def test_second_run_uses_the_cache_and_no_quota(self, harness):
        harness.script = answer(RECENT_SQL)
        harness.run(ids="Q01")
        harness.quota_error = AuthenticationError("sem chave")

        code = harness.run(ids="Q01")

        assert code == EXIT_OK
        assert harness.requests_sent == 2
        q01 = outcome(harness.report, "Q01")
        assert q01["from_cache"] is True
        assert q01["requests"] == 0
        assert q01["approved"] is True

    def test_no_cache_asks_the_model_again(self, harness):
        harness.script = answer(RECENT_SQL) + answer(RECENT_SQL)
        harness.run(ids="Q01")

        harness.run(ids="Q01", use_cache=False)

        assert harness.requests_sent == 4


class TestQuota:
    def test_refuses_when_remaining_is_below_three_per_uncached_question(self, harness):
        harness.quotas = [5]
        harness.script = answer(RECENT_SQL) + answer(ALPHA_SQL)

        code = harness.run(ids="Q01,Q04")

        assert code == EXIT_REFUSED
        assert harness.requests_sent == 0
        assert "recusad" in harness.output.getvalue().lower()
        assert not harness.output_dir.exists()

    def test_quota_error_before_starting_refuses(self, harness):
        harness.quota_error = AuthenticationError("Chave inválida ou ausente")

        code = harness.run(ids="Q01")

        assert code == EXIT_REFUSED
        assert "Chave inválida" in harness.output.getvalue()

    def test_stops_before_a_question_when_remaining_is_below_the_request_cap(self, harness):
        harness.quotas = [50, 50, 5]
        harness.script = answer(RECENT_SQL) + answer(ALPHA_SQL)

        code = harness.run(ids="Q01,Q04")

        assert code == EXIT_INTERRUPTED
        report = harness.report
        assert report["status"] == "interrompida"
        assert "MAX_REQUESTS_PER_QUESTION" in report["stop_reason"]
        assert [item["id"] for item in report["questions"]] == ["Q01"]
        assert harness.requests_sent == 2

    def test_quota_lookup_failing_mid_run_stops_with_the_partial_report(self, harness):
        harness.quotas = [50, 50, AuthenticationError("Chave inválida ou ausente"), 50]
        harness.script = answer(RECENT_SQL) + answer(ALPHA_SQL)

        code = harness.run(ids="Q01,Q04")

        assert code == EXIT_INTERRUPTED
        assert "Não foi possível consultar a cota" in harness.report["stop_reason"]
        assert [item["id"] for item in harness.report["questions"]] == ["Q01"]

    def test_quota_exhausted_mid_run_saves_the_partial_report(self, harness):
        reset = datetime(2026, 10, 3, tzinfo=UTC)
        harness.script = [*answer(RECENT_SQL), QuotaExhaustedError("Cota esgotada.", reset)]

        code = harness.run(ids="Q01,Q04")

        assert code == EXIT_INTERRUPTED
        report = harness.report
        assert report["status"] == "interrompida"
        assert "Cota esgotada" in report["stop_reason"]
        assert [item["id"] for item in report["questions"]] == ["Q01"]
        assert "interrompida" in harness.markdown


class TestOptions:
    def test_dry_run_lists_questions_estimate_and_quota_without_calling_the_model(self, harness):
        harness.quotas = [37]

        code = harness.run(dry_run=True)

        text = " ".join(harness.output.getvalue().split())
        assert code == EXIT_OK
        assert harness.llms == []
        for question_id in ("Q01", "Q03", "Q04"):
            assert question_id in text
        assert "3 pergunta(s) sem cache" in text
        assert "37" in text
        assert not harness.output_dir.exists()

    def test_dry_run_shows_unavailable_quota(self, harness):
        harness.quota_error = AuthenticationError("Chave inválida ou ausente")

        code = harness.run(dry_run=True)

        assert code == EXIT_OK
        assert "indisponível" in harness.output.getvalue()

    def test_limit_takes_the_first_questions(self, harness):
        harness.script = answer(RECENT_SQL)

        harness.run(limit=1)

        assert [item["id"] for item in harness.report["questions"]] == ["Q01"]

    def test_unknown_id_is_refused(self, harness):
        code = harness.run(ids="Q99")

        assert code == EXIT_REFUSED
        assert "Q99" in harness.output.getvalue()

    def test_sleeps_between_questions_that_call_the_model(self, harness):
        harness.script = answer(RECENT_SQL) + answer(ALPHA_SQL)

        harness.run(ids="Q01,Q04", sleep_seconds=4.0)

        assert harness.sleeps == [4.0]


class TestOutputs:
    def test_writes_timestamped_json_and_latest_markdown(self, harness):
        harness.script = answer(RECENT_SQL, "Barbie lidera.") + answer("SELECT 1 AS x")

        harness.run(ids="Q01,Q04")

        assert (harness.output_dir / "20261002T183005Z.json").is_file()
        report = harness.report
        assert report["reference_date"] == "2026-10-01"
        assert report["summary"]["total"] == 2
        assert report["summary"]["approved"] == 1
        markdown = harness.markdown
        assert "1/2" in markdown
        assert "Barbie lidera." in markdown
        assert RECENT_SQL in markdown

    def test_terminal_table_lists_each_question(self, harness):
        harness.script = answer(RECENT_SQL)

        harness.run(ids="Q01")

        text = harness.output.getvalue()
        assert "Q01" in text
        assert "fake/model:free" in text


class TestSettingsAndArguments:
    def test_reference_date_is_forced(self, monkeypatch):
        monkeypatch.setenv("REFERENCE_DATE", "2020-01-01")

        settings = evaluation_settings(_env_file=None)

        assert settings.reference_date == date(2026, 10, 1)

    def test_parse_args(self):
        options = parse_args(
            ["--ids", "q01, Q04", "--limit", "2", "--sleep", "4", "--no-cache", "--dry-run"]
        )

        assert options.ids == ("Q01", "Q04")
        assert options.limit == 2
        assert options.sleep_seconds == 4.0
        assert options.use_cache is False
        assert options.dry_run is True

    def test_parse_args_defaults(self):
        options = parse_args([])

        assert options.ids is None
        assert options.limit is None
        assert options.use_cache is True
        assert options.dry_run is False
