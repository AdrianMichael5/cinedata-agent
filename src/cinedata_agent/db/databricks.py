"""Databricks backend placeholder: not implemented, so DB_BACKEND=databricks fails clearly."""

from cinedata_agent.config import Settings
from cinedata_agent.db.base import Database


def create_database(settings: Settings) -> Database:
    raise NotImplementedError(
        "O backend Databricks ainda não foi implementado. Use DB_BACKEND=sqlite."
    )
