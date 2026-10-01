"""Tables, schemas and SQL macros the agent may use, per backend.

Table and schema names are compared case-insensitively.
"""

import json
import re
from collections.abc import Callable

from cinedata_agent.db.errors import UnsafeQueryError

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

# Single source of truth: languages and countries that leaked into dim_people and
# dim_companies (column shift in the source data). Genre names leak too; those come from
# dim_genres inside the macro. Same set as NOMES_INVALIDOS in eval/gabarito.py.
INVALID_NAMES: tuple[str, ...] = (
    "English", "French", "Spanish", "German", "Japanese", "Italian", "Korean", "Hindi",
    "Portuguese", "Chinese", "Mandarin", "Cantonese", "Russian", "Arabic", "Turkish", "Tamil",
    "Telugu", "Malayalam", "Thai", "Swedish", "Danish", "Norwegian", "Finnish", "Dutch",
    "Polish", "Greek", "Hebrew", "Persian", "Indonesian", "Tagalog", "Czech", "Hungarian",
    "Romanian", "Ukrainian", "Bengali", "Kannada", "Marathi", "United States Of America",
    "United Kingdom", "France", "Germany", "Japan", "India", "Canada", "Brazil", "Spain",
    "Italy", "South Korea", "China", "Mexico", "Australia", "Russia", "Turkey", "Argentina",
    "Philippines", "Hong Kong", "Taiwan", "Sweden", "Denmark", "Norway", "Finland",
    "Netherlands", "Belgium", "Poland", "Ireland",
)  # fmt: skip

_MACRO = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")


def _invalid_names_subquery() -> str:
    # Doubled single quotes keep a name like O'Brien inside the SQL string literal.
    names = json.dumps(list(INVALID_NAMES), ensure_ascii=False).replace("'", "''")
    return f"(SELECT nome_genero FROM dim_genres UNION ALL SELECT value FROM json_each('{names}'))"


# Marker name -> SQL fragment, built at expansion time.
MACROS: dict[str, Callable[[], str]] = {"NOMES_INVALIDOS": _invalid_names_subquery}


def expand_macros(sql: str) -> str:
    """Replace {{MARKER}} with its SQL fragment; unknown markers raise UnsafeQueryError."""
    unknown = sorted({name for name in _MACRO.findall(sql) if name not in MACROS})
    if unknown:
        valid = ", ".join(f"{{{{{name}}}}}" for name in sorted(MACROS))
        found = ", ".join(f"{{{{{name}}}}}" for name in unknown)
        raise UnsafeQueryError(f"Marcador desconhecido: {found}. Marcadores válidos: {valid}.")
    return _MACRO.sub(lambda match: MACROS[match.group(1)](), sql)
