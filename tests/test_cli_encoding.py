"""Regression: Unicode answers used to crash the CLI on a cp1252 stdout (Windows pipes)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

SCRIPT = """
import sys
sys.path.insert(0, {tests_dir!r})
from fakes import FakeLLM, text_message, tool_call_message
from cinedata_agent import cli

fake = FakeLLM([
    tool_call_message("SELECT COUNT(*) AS total FROM dim_movies"),
    text_message({answer!r}),
])
cli.LLMClient = lambda settings, **kwargs: fake
sys.argv = ["cinedata", "ask", "Top filmes", "--show-sql"]
cli.app()
"""


@pytest.mark.parametrize(
    "answer",
    ["Top 1 ‑ Avatar (U+2011)", "Avatar 🎬 lidera", "Coração – “aspas” … ✓"],
    ids=["non_breaking_hyphen", "emoji", "typography"],
)
def test_unicode_answer_survives_a_cp1252_stdout(tmp_path, sample_db, answer):
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONUTF8", "PYTHONIOENCODING", "PYTHONLEGACYWINDOWSSTDIO"}
    }
    env.update(
        PYTHONIOENCODING="cp1252",
        DB_PATH=str(sample_db),
        OPENROUTER_API_KEY="sk-or-v1-fake-key-for-encoding-tests",
    )
    script = SCRIPT.format(tests_dir=str(TESTS_DIR), answer=answer)

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=60,
        check=False,
    )

    stdout = completed.stdout.decode("utf-8")
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")[-2000:]
    assert answer in stdout
    assert "Chamadas ao LLM: 2 · Requisições ao OpenRouter: 2" in stdout
    assert "SELECT COUNT(*) AS total FROM dim_movies" in stdout
