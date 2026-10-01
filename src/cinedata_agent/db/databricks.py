"""Optional Databricks backend (extra `databricks`). Not implemented yet."""

from cinedata_agent.config import Settings
from cinedata_agent.db.base import Database


def create_database(settings: Settings) -> Database:
    raise NotImplementedError(
        "O backend Databricks é um extra opcional e ainda não foi implementado. "
        "Use DB_BACKEND=sqlite."
    )
