"""Pick the database backend from settings."""

from cinedata_agent.config import Settings
from cinedata_agent.db.base import Database
from cinedata_agent.db.sqlite import SQLiteDatabase


def get_database(settings: Settings) -> Database:
    """Build the configured backend; the Databricks placeholder is imported only when chosen."""
    if settings.db_backend == "databricks":
        from cinedata_agent.db.databricks import create_database

        return create_database(settings)

    return SQLiteDatabase(
        settings.db_path,
        timeout_seconds=settings.query_timeout_seconds,
        max_rows=settings.max_rows,
    )
