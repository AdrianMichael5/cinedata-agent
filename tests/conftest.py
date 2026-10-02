"""Shared fixtures that keep tests independent of the developer's environment and .env."""

import socket
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest

from cinedata_agent.config import get_settings

SETTINGS_ENV_VARS = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "LLM_MODELS",
    "DB_BACKEND",
    "DB_PATH",
    "QUERY_TIMEOUT_SECONDS",
    "MAX_ROWS",
    "MAX_LLM_CALLS_PER_QUESTION",
    "MAX_REQUESTS_PER_QUESTION",
    "CACHE_DIR",
    "REFERENCE_DATE",
    "LOG_LEVEL",
    "DATABRICKS_SERVER_HOSTNAME",
    "DATABRICKS_HTTP_PATH",
    "DATABRICKS_TOKEN",
)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

SAMPLE_MOVIES = [
    (1, "Avatar", 2022),
    (2, "Barbie", 2023),
    (3, "Coco", 2017),
    (4, "Duna", 2021),
    (5, "Elvis", 2022),
]


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Drop config env vars and run each test from an empty dir, so no real .env is read."""
    for name in SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if any test reaches a real HTTP transport (httpx.MockTransport still works)."""

    def refuse(self: Any, request: Any) -> Any:
        raise RuntimeError(f"Network access is disabled in tests: {request.method} {request.url}")

    # httpx for the account helpers; httpx2 is what the openai SDK sends through.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", refuse)

    # Socket-level guard as well: covers any client (async httpx, other SDKs), loopback excepted.
    original_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: Any) -> None:
        host = address[0] if isinstance(address, tuple) else address
        if host in LOOPBACK_HOSTS:
            return original_connect(self, address)
        raise RuntimeError(f"Network access is disabled in tests: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture
def sample_db(tmp_path: Path) -> Path:
    """Small SQLite file with three tables, built from scratch for each test."""
    path = tmp_path / "sample db #1" / "sample.db"
    path.parent.mkdir()
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE dim_movies (id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, ano INTEGER);
            CREATE TABLE dim_genres (id INTEGER PRIMARY KEY, nome_genero TEXT NOT NULL);
            CREATE TABLE bridge_movie_genre (movie_id INTEGER, genre_id INTEGER);
            INSERT INTO dim_genres VALUES (1, 'Drama'), (2, 'Animation');
            INSERT INTO bridge_movie_genre VALUES (1, 1), (3, 2), (4, 1);
            """
        )
        connection.executemany("INSERT INTO dim_movies VALUES (?, ?, ?)", SAMPLE_MOVIES)
    connection.close()
    return path
