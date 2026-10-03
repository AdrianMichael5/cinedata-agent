import json

from fakes import FakeLLM, text_message, tool_call_message

import cinedata_agent.agent as agent_module
from cinedata_agent.agent import AgentAnswer, SqlRecord
from cinedata_agent.cli import log_sql_timing
from cinedata_agent.config import Settings
from cinedata_agent.db.sqlite import SQLiteDatabase
from cinedata_agent.formatting import SQL_WAIT_HINT_SECONDS, SqlWait, sql_wait_text
from cinedata_agent.llm.request_log import RequestLog, SqlTimingRecord, read_records

HINT = "Executando SQL… (consultas com duas junções de pessoas podem levar até 1 minuto)"


def make_answer(sql_ms: int, queries: int) -> AgentAnswer:
    return AgentAnswer(
        text="ok",
        sql_executed=["SELECT 1"] * queries,
        model_used=None,
        llm_calls=1,
        last_result=None,
        sql_log=[SqlRecord(sql="SELECT 1") for _ in range(queries)],
        question_id="q-test",
        sql_ms=sql_ms,
    )


class TestWaitText:
    def test_shows_the_hint_and_the_elapsed_seconds_at_first(self):
        assert sql_wait_text(3.7) == f"{HINT} 3 s"

    def test_only_the_timer_remains_after_ten_seconds(self):
        assert sql_wait_text(12.4) == "Executando SQL… 12 s"

    def test_hint_switches_off_exactly_at_the_threshold(self):
        assert SQL_WAIT_HINT_SECONDS == 10
        assert "podem levar" in sql_wait_text(9.99)
        assert "podem levar" not in sql_wait_text(10.0)


class TestWaitRenderable:
    def test_reads_the_clock_when_rendered(self):
        ticks = iter([100.0, 103.2])
        renderable = SqlWait(lambda: next(ticks))

        assert renderable.__rich__().plain == f"{HINT} 3 s"


class TestAgentSqlTiming:
    def test_answer_sums_the_time_spent_in_each_query(self, sample_db, monkeypatch):
        ticks = iter([10.0, 10.25, 20.0, 20.5])
        monkeypatch.setattr(agent_module, "SQL_CLOCK", lambda: next(ticks))
        db = SQLiteDatabase(sample_db, timeout_seconds=5, max_rows=100)
        llm = FakeLLM(
            [
                tool_call_message("SELECT COUNT(*) AS total FROM dim_movies"),
                tool_call_message("SELECT titulo FROM dim_movies ORDER BY id"),
                text_message("ok"),
            ]
        )
        settings = Settings(_env_file=None, REFERENCE_DATE="2026-10-01")

        answer = agent_module.Agent(settings, db, llm).ask("Quantos filmes existem?")

        assert answer.sql_ms == 750
        assert answer.question_id


class TestSqlTimingLog:
    def test_timing_line_is_skipped_by_the_request_reader(self, tmp_path):
        path = tmp_path / "requests.jsonl"
        RequestLog(path).append(
            SqlTimingRecord(
                timestamp="2026-10-03T12:00:00+00:00", question_id="q", sql_ms=420, queries=2
            )
        )

        assert read_records(path) == []
        line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert line["type"] == "sql"
        assert line["sql_ms"] == 420
        assert line["queries"] == 2
        assert line["question_id"] == "q"

    def test_writes_the_timing_only_when_sql_ran(self, tmp_path):
        path = tmp_path / "requests.jsonl"
        settings = Settings(_env_file=None, request_log_path=path)

        log_sql_timing(settings, make_answer(sql_ms=0, queries=0))
        assert not path.exists()

        log_sql_timing(settings, make_answer(sql_ms=250, queries=1))
        assert json.loads(path.read_text(encoding="utf-8"))["sql_ms"] == 250
