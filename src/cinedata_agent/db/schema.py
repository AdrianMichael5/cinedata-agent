"""Tables and schemas the agent may query, per backend (names compared case-insensitively)."""

# Gold layer of data/cinerocket.db. alembic_version is migration metadata, not data.
SQLITE_ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "dim_movies",
        "fact_movies_performance",
        "dim_genres",
        "dim_people",
        "dim_companies",
        "dim_reviews",
        "movie_reviews",
        "bridge_movie_genre",
        "bridge_movie_person",
        "bridge_movie_company",
    }
)

SQLITE_ALLOWED_SCHEMAS: frozenset[str] = frozenset({"main"})
