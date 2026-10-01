import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from cinedata_agent.db import schema as schema_module
from cinedata_agent.db.errors import UnsafeQueryError
from cinedata_agent.db.schema import INVALID_NAMES, MACROS, expand_macros
from cinedata_agent.db.validator import validate_select

GABARITO_SCRIPT = Path(__file__).resolve().parents[2] / "eval" / "gabarito.py"


@pytest.fixture
def gabarito_module(monkeypatch):
    """Import eval/gabarito.py from its path without running it as a script."""
    spec = importlib.util.spec_from_file_location("gabarito_reference", GABARITO_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Its dataclasses use postponed annotations, which need the module registered first.
    monkeypatch.setitem(sys.modules, "gabarito_reference", module)
    spec.loader.exec_module(module)
    return module


class TestInvalidNames:
    def test_matches_the_gabarito_list(self, gabarito_module):
        literal = re.search(r"json_each\('(\[.*?\])'\)", gabarito_module.NOMES_INVALIDOS, re.S)
        assert literal is not None
        reference = set(json.loads(literal.group(1)))

        assert set(INVALID_NAMES) == reference, {
            "only_in_gabarito": sorted(reference - set(INVALID_NAMES)),
            "only_here": sorted(set(INVALID_NAMES) - reference),
        }

    def test_has_no_duplicates(self):
        assert len(INVALID_NAMES) == len(set(INVALID_NAMES))


class TestExpandMacros:
    def test_expands_invalid_names_marker(self):
        expanded = expand_macros(
            "SELECT nome_pessoa FROM dim_people WHERE nome_pessoa NOT IN {{NOMES_INVALIDOS}}"
        )

        assert "{{" not in expanded
        assert (
            "SELECT nome_genero FROM dim_genres UNION ALL SELECT value FROM json_each('["
            in expanded
        )
        assert '"United States Of America"' in expanded

    def test_tolerates_spaces_inside_the_marker(self):
        assert "json_each" in expand_macros("SELECT 1 WHERE 'x' NOT IN {{ NOMES_INVALIDOS }}")

    def test_sql_without_marker_is_unchanged(self):
        sql = "SELECT titulo FROM dim_movies WHERE titulo = '{não é marcador}'"

        assert expand_macros(sql) == sql

    def test_unknown_marker_lists_the_valid_ones(self):
        with pytest.raises(UnsafeQueryError) as error:
            expand_macros("SELECT * FROM dim_people WHERE nome_pessoa NOT IN {{NOMES_PROIBIDOS}}")

        message = str(error.value)
        assert "{{NOMES_PROIBIDOS}}" in message
        assert "{{NOMES_INVALIDOS}}" in message

    def test_expanded_sql_passes_the_validator(self):
        sql = (
            "SELECT c.nome_produtora FROM dim_companies c "
            "WHERE c.nome_produtora NOT IN {{NOMES_INVALIDOS}}"
        )

        assert validate_select(expand_macros(sql))

    def test_names_with_apostrophes_keep_the_sql_valid(self, monkeypatch):
        monkeypatch.setattr(schema_module, "INVALID_NAMES", ("O'Brien", "English"))

        expanded = expand_macros("SELECT 1 AS x WHERE 'a' NOT IN {{NOMES_INVALIDOS}}")

        assert "O''Brien" in expanded
        assert validate_select(expanded)

    def test_known_markers(self):
        assert set(MACROS) == {"NOMES_INVALIDOS"}
