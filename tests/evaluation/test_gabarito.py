import json
from pathlib import Path

import pytest

from cinedata_agent.evaluation.compare import ResultTable
from cinedata_agent.evaluation.gabarito import (
    EXPECTED_VARIANTS,
    Role,
    judge,
    load_gabarito,
)

REAL_GABARITO = Path(__file__).resolve().parents[2] / "eval" / "gabarito.json"

TOP = ResultTable(("titulo", "valor"), (("A", 3), ("B", 2), ("C", 1)))
OTHER = ResultTable(("titulo", "valor"), (("X", 9), ("Y", 8), ("Z", 7)))


def write_gabarito(path: Path, entries: list[dict]) -> Path:
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return path


def entry(question_id: str, variants: list[str], stored: bool = True) -> dict:
    result = {"colunas": ["x"], "linhas": [[1]]}
    principal = {"titulo": "Principal", "sql": "SELECT 1 AS x"}
    if stored:
        principal["resultado"] = result
    return {
        "id": question_id,
        "categoria": "Teste",
        "pergunta": f"Pergunta {question_id}?",
        "decisoes": [],
        "principal": principal,
        "variantes": [{"titulo": title, "sql": "SELECT 2 AS x"} for title in variants],
    }


class TestLoadRealGabarito:
    def test_has_the_fourteen_questions_in_order(self):
        questions = load_gabarito(REAL_GABARITO)

        assert [question.id for question in questions] == [f"Q{n:02d}" for n in range(1, 15)]

    @pytest.mark.parametrize("question_id", ["Q03", "Q05", "Q14"])
    def test_filtered_variant_is_expected_and_principal_is_an_alternative(self, question_id):
        question = next(q for q in load_gabarito(REAL_GABARITO) if q.id == question_id)

        expected, *alternatives = question.references
        assert expected.role is Role.EXPECTED
        assert expected.label == EXPECTED_VARIANTS[question_id]
        assert expected.source == "variante"
        assert any(ref.source == "principal" for ref in alternatives)
        assert all(ref.role is Role.ALTERNATIVE for ref in alternatives)

    def test_other_questions_expect_the_principal(self):
        q01 = load_gabarito(REAL_GABARITO)[0]

        expected, *alternatives = q01.references
        assert expected.label == "Ranking em R$"
        assert expected.source == "principal"
        assert [ref.label for ref in alternatives] == ["Mesmo ranking em US$ (mostra a diferença)"]

    def test_stored_results_are_loaded(self):
        q01 = load_gabarito(REAL_GABARITO)[0]

        stored = q01.references[0].stored
        assert stored is not None
        assert stored.columns[0] == "titulo"
        assert stored.rows[0][0] == "Avatar: The Way Of Water"


class TestLoadSyntheticGabarito:
    def test_reference_without_stored_result(self, tmp_path):
        path = write_gabarito(tmp_path / "g.json", [entry("Q01", [], stored=False)])

        [question] = load_gabarito(path)

        assert question.references[0].stored is None

    def test_missing_expected_variant_is_an_error(self, tmp_path):
        path = write_gabarito(tmp_path / "g.json", [entry("Q03", ["Outra variante"])])

        with pytest.raises(ValueError, match="Q03"):
            load_gabarito(path)

    def test_malformed_file_is_an_error(self, tmp_path):
        path = tmp_path / "g.json"
        path.write_text(json.dumps({"perguntas": []}), encoding="utf-8")

        with pytest.raises(ValueError, match="lista"):
            load_gabarito(path)


class TestJudge:
    def references(self, tmp_path):
        path = write_gabarito(tmp_path / "g.json", [entry("Q01", ["Variante"])])
        return load_gabarito(path)[0].references

    def test_matching_the_expected_reference(self, tmp_path):
        expected, alternative = self.references(tmp_path)

        verdict = judge([(expected, TOP), (alternative, OTHER)], TOP)

        assert verdict.approved
        assert verdict.matched is expected
        assert verdict.matched.role is Role.EXPECTED

    def test_matching_only_an_alternative(self, tmp_path):
        expected, alternative = self.references(tmp_path)

        verdict = judge([(expected, TOP), (alternative, OTHER)], OTHER)

        assert verdict.approved
        assert verdict.matched is alternative
        assert verdict.matched.label == "Variante"

    def test_matching_nothing_reports_the_expected_comparison(self, tmp_path):
        expected, alternative = self.references(tmp_path)
        actual = ResultTable(("titulo",), (("Nada",),))

        verdict = judge([(expected, TOP), (alternative, OTHER)], actual)

        assert not verdict.approved
        assert verdict.matched is None
        assert verdict.comparison.key_columns == ("titulo",)
