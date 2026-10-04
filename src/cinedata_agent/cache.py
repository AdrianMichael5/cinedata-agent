"""Disk cache of complete agent answers, so a repeated question costs no OpenRouter request.

The key combines the normalized question, REFERENCE_DATE, a hash of the rendered system prompt
and the backend: when the prompt changes, older entries simply stop matching.
"""

import hashlib
import json
import logging
import os
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from cinedata_agent.agent import AgentAnswer
from cinedata_agent.config import Settings
from cinedata_agent.prompts.builder import build_system_prompt

logger = logging.getLogger(__name__)

ANSWERS_DIR = "answers"
# Bump when the stored layout or the key recipe changes: old files then stop matching.
CACHE_FORMAT_VERSION = 1


def normalize_question(question: str) -> str:
    """NFKC, lowercase, collapsed whitespace and no trailing punctuation ("?", "!", "...")."""
    text = " ".join(unicodedata.normalize("NFKC", question).lower().split())
    while text and (unicodedata.category(text[-1]).startswith("P") or text[-1].isspace()):
        text = text[:-1]
    return text


@dataclass(frozen=True)
class CacheKeyParts:
    question: str
    reference_date: date
    system_prompt: str
    backend: str


def cache_key(parts: CacheKeyParts) -> str:
    material = {
        "version": CACHE_FORMAT_VERSION,
        "question": normalize_question(parts.question),
        "reference_date": parts.reference_date.isoformat(),
        "prompt_sha256": hashlib.sha256(parts.system_prompt.encode("utf-8")).hexdigest(),
        "backend": parts.backend,
    }
    encoded = json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CachedAnswer:
    question: str
    text: str
    sql_executed: list[str]
    model_used: str | None
    llm_calls: int
    requests_sent: int
    columns: list[str]
    rows: list[list[Any]]
    created_at: datetime


def is_complete(answer: AgentAnswer, max_llm_calls: int) -> bool:
    """Only answers worth replaying:

    SQL ran, there is text, no fallback warning, the agent did not need its entire call
    budget to answer, and no SQL attempt failed after the last successful result.
    """
    if not (answer.sql_executed and answer.text.strip() and answer.warning is None):
        return False
    if answer.llm_calls >= max_llm_calls:
        return False
    return not (answer.sql_log and answer.sql_log[-1].rejection is not None)


class AnswerCache:
    """One JSON file per answer under <directory>/answers/; failures never break a command."""

    def __init__(self, directory: Path, max_rows: int, max_llm_calls: int) -> None:
        self.directory = directory
        self.max_rows = max_rows
        self.max_llm_calls = max_llm_calls

    @classmethod
    def from_settings(cls, settings: Settings) -> "AnswerCache":
        return cls(
            settings.cache_dir,
            max_rows=settings.max_rows,
            max_llm_calls=settings.max_llm_calls_per_question,
        )

    def key_for(self, settings: Settings, question: str) -> str:
        return cache_key(
            CacheKeyParts(
                question=question,
                reference_date=settings.reference_date,
                system_prompt=build_system_prompt(settings),
                backend=settings.db_backend,
            )
        )

    def get(self, key: str) -> CachedAnswer | None:
        """The stored answer, or None on a miss; a damaged file is a miss with a warning."""
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            logger.warning("Ignoring unreadable cache entry %s: %s", path.name, error)
            return None
        cached = _parse(data)
        if cached is None:
            logger.warning("Ignoring cache entry %s with an unexpected layout", path.name)
        return cached

    def put(self, key: str, question: str, answer: AgentAnswer, requests_sent: int) -> bool:
        """Store a complete answer; return whether it was written."""
        if not is_complete(answer, self.max_llm_calls):
            return False
        result = answer.last_result
        payload = {
            "version": CACHE_FORMAT_VERSION,
            "question": question,
            "text": answer.text,
            "sql_executed": list(answer.sql_executed),
            "model_used": answer.model_used,
            "llm_calls": answer.llm_calls,
            "requests_sent": requests_sent,
            "columns": list(result.columns) if result is not None else [],
            "rows": [list(row) for row in result.rows[: self.max_rows]] if result else [],
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        path = self._path(key)
        # Unique per write: two concurrent writers of the same key must not share a temp file.
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # default=str: BLOB or date values must not make the whole entry unwritable.
            text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, path)
        except OSError as error:
            logger.warning("Could not write the cache entry %s: %s", path.name, error)
            _discard(temporary)
            return False
        return True

    def clear(self) -> int:
        """Delete every stored answer; return how many were removed."""
        folder = self.directory / ANSWERS_DIR
        if not folder.is_dir():
            return 0
        entries = list(folder.glob("*.json"))
        for entry in entries:
            entry.unlink()
        return len(entries)

    def _path(self, key: str) -> Path:
        return self.directory / ANSWERS_DIR / f"{key}.json"


def _discard(temporary: Path) -> None:
    """Best-effort removal of a half-written temp file; never raises."""
    # missing_ok only covers FileNotFoundError (what Windows reports when the parent is a
    # file); Linux reports NotADirectoryError, and a read-only folder gives PermissionError.
    try:
        temporary.unlink(missing_ok=True)
    except OSError as error:
        logger.debug("Could not remove the temporary cache file %s: %s", temporary.name, error)


def _parse(data: Any) -> CachedAnswer | None:
    if not isinstance(data, dict) or data.get("version") != CACHE_FORMAT_VERSION:
        return None
    try:
        created_at = datetime.fromisoformat(_field(data, "created_at", str))
        rows = _field(data, "rows", list)
        if not all(isinstance(row, list) for row in rows):
            return None
        model_used = data.get("model_used")
        return CachedAnswer(
            question=_field(data, "question", str),
            text=_field(data, "text", str),
            sql_executed=_strings(_field(data, "sql_executed", list)),
            model_used=model_used if isinstance(model_used, str) else None,
            llm_calls=_field(data, "llm_calls", int),
            requests_sent=_field(data, "requests_sent", int),
            columns=_strings(_field(data, "columns", list)),
            rows=rows,
            created_at=created_at,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _field(data: dict[str, Any], name: str, kind: type) -> Any:
    value = data[name]
    if not isinstance(value, kind) or isinstance(value, bool):
        raise TypeError(name)
    return value


def _strings(values: list[Any]) -> list[str]:
    if not all(isinstance(value, str) for value in values):
        raise TypeError("expected a list of strings")
    return list(values)
