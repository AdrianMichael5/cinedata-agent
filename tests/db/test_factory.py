import sys

import pytest

from cinedata_agent.config import Settings
from cinedata_agent.db.factory import get_database
from cinedata_agent.db.sqlite import SQLiteDatabase

DATABRICKS_MODULE = "cinedata_agent.db.databricks"


def make_settings(monkeypatch, **env: str) -> Settings:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_sqlite_backend_uses_settings(monkeypatch, sample_db):
    settings = make_settings(monkeypatch, DB_PATH=str(sample_db), MAX_ROWS="2")

    database = get_database(settings)

    assert isinstance(database, SQLiteDatabase)
    assert database.run_query("SELECT id FROM dim_movies").truncated is True


def test_sqlite_backend_does_not_import_databricks(monkeypatch, sample_db):
    monkeypatch.delitem(sys.modules, DATABRICKS_MODULE, raising=False)
    settings = make_settings(monkeypatch, DB_PATH=str(sample_db))

    get_database(settings)

    assert DATABRICKS_MODULE not in sys.modules


def test_databricks_backend_is_not_implemented(monkeypatch):
    settings = make_settings(monkeypatch, DB_BACKEND="databricks")

    with pytest.raises(NotImplementedError, match="ainda não foi implementado") as error:
        get_database(settings)

    assert "extra" not in str(error.value)
