import json
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from cinedata_agent.cli import app
from cinedata_agent.llm.request_log import RequestLog, RequestRecord, read_records, summarize

runner = CliRunner()
LOG = Path("logs") / "requests.jsonl"


def record(status: int | None, when: datetime, model: str = "a/model:free") -> RequestRecord:
    return RequestRecord(
        timestamp=when.isoformat(),
        question_id="q-1",
        requested_model=model,
        responded_model=model if status == 200 else None,
        status=status,
        latency_ms=120,
        prompt_tokens=None,
        completion_tokens=None,
        error_type=None if status == 200 else "provider_capacity",
        finish_reason="stop" if status == 200 else None,
        reasoning_tokens=None,
    )


def write(path: Path, records: list[RequestRecord], extra_lines: tuple[str, ...] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(asdict(item)) for item in records] + list(extra_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestRequestLog:
    def test_appends_json_lines_and_creates_the_folder(self, tmp_path):
        path = tmp_path / "logs" / "requests.jsonl"
        log = RequestLog(path)
        now = datetime.now(UTC)

        log.append(record(429, now))
        log.append(record(200, now))

        assert [item.status for item in read_records(path)] == [429, 200]

    def test_reading_a_missing_file_returns_nothing(self, tmp_path):
        assert read_records(tmp_path / "nada.jsonl") == []

    def test_malformed_lines_are_skipped(self, tmp_path):
        path = tmp_path / "requests.jsonl"
        bad_timestamp = {**asdict(record(200, datetime.now(UTC))), "timestamp": "ontem"}
        numeric_timestamp = {**asdict(record(200, datetime.now(UTC))), "timestamp": 1}
        write(
            path,
            [record(200, datetime.now(UTC))],
            (
                "{not json",
                "[]",
                '{"status": 200}',
                json.dumps(bad_timestamp),
                json.dumps(numeric_timestamp),
            ),
        )

        assert len(read_records(path)) == 1


class TestSummarize:
    def test_counts_by_status_for_one_utc_day(self):
        today = datetime(2026, 10, 2, 1, 30, tzinfo=UTC)
        records = [
            record(200, today),
            record(429, today),
            record(429, today),
            record(None, today),
            record(200, today - timedelta(days=1)),
        ]

        summary = summarize(records, day=date(2026, 10, 2))

        assert summary.total == 4
        assert summary.by_status == {"200": 1, "429": 2, "sem resposta": 1}

    def test_utc_day_not_local_day(self):
        # 23:30 in Brasília on Oct 1st is already Oct 2nd in UTC (the quota day).
        late_evening = datetime(2026, 10, 2, 2, 30, tzinfo=UTC)

        summary = summarize([record(200, late_evening)], day=date(2026, 10, 2))

        assert summary.total == 1

    def test_without_a_day_counts_everything(self):
        now = datetime(2026, 10, 2, tzinfo=UTC)

        summary = summarize([record(200, now), record(200, now - timedelta(days=3))], day=None)

        assert summary.total == 2


class TestRequestsCommand:
    def test_today_sums_requests_by_status(self):
        now = datetime.now(UTC)
        write(
            LOG,
            [
                record(200, now),
                record(429, now),
                record(200, now),
                record(200, now - timedelta(days=2)),
            ],
        )

        result = runner.invoke(app, ["requests", "--today"])

        assert result.exit_code == 0
        output = " ".join(result.output.split())
        assert now.date().isoformat() in output
        assert "Total: 3" in output
        assert "200 2" in output
        assert "429 1" in output

    def test_without_today_counts_the_whole_log(self):
        now = datetime.now(UTC)
        write(LOG, [record(200, now), record(200, now - timedelta(days=2))])

        result = runner.invoke(app, ["requests"])

        assert result.exit_code == 0
        assert "Total: 2" in " ".join(result.output.split())

    def test_missing_log_says_so(self):
        result = runner.invoke(app, ["requests", "--today"])

        assert result.exit_code == 0
        assert "Nenhuma requisição registrada" in result.output
