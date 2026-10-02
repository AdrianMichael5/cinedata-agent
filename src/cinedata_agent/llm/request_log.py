"""Append-only JSON Lines log of OpenRouter requests, to compare local usage with the quota.

Each line holds metadata only: never the API key, the prompt or the model's answer.
"""

import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass, fields
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

NO_RESPONSE = "sem resposta"


@dataclass(frozen=True)
class RequestRecord:
    timestamp: str
    question_id: str | None
    requested_model: str
    responded_model: str | None
    status: int | None
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    error_type: str | None
    finish_reason: str | None
    reasoning_tokens: int | None

    @property
    def utc_day(self) -> date:
        """The quota day: OpenRouter resets the free quota at midnight UTC."""
        return datetime.fromisoformat(self.timestamp).astimezone(UTC).date()


@dataclass(frozen=True)
class RequestSummary:
    day: date | None
    total: int
    by_status: dict[str, int]


class RequestLog:
    """Writes one line per request; a write failure is logged and never breaks the request."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: RequestRecord) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        except OSError as error:
            logger.debug("Could not write the request log %s: %s", self.path, error)


def now_utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def read_records(path: Path) -> list[RequestRecord]:
    """Read the log, skipping lines that are not complete records."""
    if not path.is_file():
        return []
    parsed = (_parse(line) for line in path.read_text(encoding="utf-8").splitlines())
    return [record for record in parsed if record is not None]


def summarize(records: list[RequestRecord], day: date | None) -> RequestSummary:
    """Count requests by HTTP status, for one UTC day or for the whole log."""
    selected = [record for record in records if day is None or record.utc_day == day]
    counts = Counter(
        str(record.status) if record.status is not None else NO_RESPONSE for record in selected
    )
    return RequestSummary(day=day, total=len(selected), by_status=dict(sorted(counts.items())))


_FIELD_NAMES = frozenset(field.name for field in fields(RequestRecord))


def _parse(line: str) -> RequestRecord | None:
    try:
        data: Any = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.keys() >= _FIELD_NAMES:
        return None
    if not isinstance(data["timestamp"], str) or not _is_iso_timestamp(data["timestamp"]):
        return None
    return RequestRecord(**{name: data[name] for name in _FIELD_NAMES})


def _is_iso_timestamp(value: str) -> bool:
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True
