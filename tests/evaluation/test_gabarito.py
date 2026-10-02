import json
from pathlib import Path

import pytest

from cinedata_agent.evaluation.classification import REFERENCE_CLASSES, VALUE_CHECKED_QUESTIONS
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
THIRD = ResultTable(("titulo", "valor"), (("M", 6), ("N", 5), ("O", 4)))

PRODUCERS = ("Marvel Studios", "Walt Disney", "Pixar", "Lucasfilm", "Universal")
EXPECTED_Q11 = ResultTable(
    ("nome_produtora", "lucro_total_brl", "lucro_total_usd", "filmes"),
    tuple(
        (name, 61_553_661_048.84 - index * 1e9, 14_897_936_776.0 - index * 1e8, 17 + index)
        for index, name in enumerate(PRODUCERS)
    ),
)
TRAP_Q11 = ResultTable(
    ("nome_produtora", "lucro_total_brl"),
    tuple((name, 63_936_626_417.88 - index * 1e9) for index, name in enumerate(PRODUCERS)),
)


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


def agent(*rows: tuple, columns: tuple[str, ...] = ("produtora", "lucro")) -> ResultTable:
    return ResultTable(columns, rows)


class TestLoadRealGabarito:
    def test_has_the_fourteen_questions_in_order(self):
        questions = load_gabarito(REAL_GABARITO)

        assert [question.id for question in questions] == [f"Q{n:02d}" for n in range(1, 15)]

    @pytest.mark.parametrize("question_id", ["Q03", "Q05", "Q14"])
    def test_filtered_variant_is_expected_and_principal_is_a_valid_alternative(self, question_id):
        question = next(q for q in load_gabarito(REAL_GABARITO) if q.id == question_id)

        expected, *others = question.references
        assert expected.role is Role.EXPECTED
        assert expected.label == EXPECTED_VARIANTS[question_id]
        assert expected.source == "variante"
        [principal] = others
        assert principal.source == "principal"
        assert principal.role is Role.VALID_ALTERNATIVE

    def test_other_questions_expect_the_principal(self):
        q01 = load_gabarito(REAL_GABARITO)[0]

        expected, trap = q01.references
        assert expected.label == "Ranking em R$"
        assert expected.source == "principal"
        assert trap.label == "Mesmo ranking em US$ (mostra a diferença)"
        assert trap.role is Role.TRAP

    def test_roles_follow_the_classification_map(self):
        for question in load_gabarito(REAL_GABARITO):
            expected, *others = question.references
            assert expected.role is Role.EXPECTED
            for reference in others:
                assert reference.role is REFERENCE_CLASSES[question.id][reference.label]

    def test_stored_results_are_loaded(self):
        q01 = load_gabarito(REAL_GABARITO)[0]

        stored = q01.references[0].stored
        assert stored is not None
        assert stored.columns[0] == "titulo"
        assert stored.rows[0][0] == "Avatar: The Way Of Water"


class TestLoadSyntheticGabarito:
    def test_reference_without_stored_result(self, tmp_path):
        path = write_gabarito(tmp_path / "g.json", [entry("Q01", [], stored=False)])

        [question] = load_gabarito(path, classes={})

        assert question.references[0].stored is None

    def test_unclassified_variant_is_an_error(self, tmp_path):
        path = write_gabarito(tmp_path / "g.json", [entry("Q01", ["Variante nova"])])

        with pytest.raises(ValueError, match="Q01.*Variante nova"):
            load_gabarito(path, classes={})

    def test_missing_expected_variant_is_an_error(self, tmp_path):
        path = write_gabarito(tmp_path / "g.json", [entry("Q03", ["Outra variante"])])

        with pytest.raises(ValueError, match="Q03"):
            load_gabarito(path, classes={"Q03": {"Outra variante": Role.TRAP}})

    def test_malformed_file_is_an_error(self, tmp_path):
        path = tmp_path / "g.json"
        path.write_text(json.dumps({"perguntas": []}), encoding="utf-8")

        with pytest.raises(ValueError, match="lista"):
            load_gabarito(path)

    def test_valid_alternatives_come_before_traps(self, tmp_path):
        path = write_gabarito(tmp_path / "g.json", [entry("Q01", ["Armadilha", "Válida"])])
        classes = {"Q01": {"Armadilha": Role.TRAP, "Válida": Role.VALID_ALTERNATIVE}}

        [question] = load_gabarito(path, classes=classes)

        assert [ref.label for ref in question.references] == ["Principal", "Válida", "Armadilha"]


class TestJudge:
    def references(self, tmp_path):
        path = write_gabarito(tmp_path / "g.json", [entry("Q01", ["Válida", "Armadilha"])])
        classes = {"Q01": {"Válida": Role.VALID_ALTERNATIVE, "Armadilha": Role.TRAP}}
        return load_gabarito(path, classes=classes)[0].references

    def test_matching_the_expected_reference(self, tmp_path):
        expected, valid, trap = self.references(tmp_path)

        verdict = judge([(expected, TOP), (valid, OTHER), (trap, THIRD)], TOP)

        assert verdict.approved
        assert verdict.matched is expected

    def test_matching_a_valid_alternative_is_approved(self, tmp_path):
        expected, valid, trap = self.references(tmp_path)

        verdict = judge([(expected, TOP), (valid, OTHER), (trap, THIRD)], OTHER)

        assert verdict.approved
        assert verdict.matched is valid

    def test_matching_a_trap_is_rejected(self, tmp_path):
        expected, valid, trap = self.references(tmp_path)

        verdict = judge([(expected, TOP), (valid, OTHER), (trap, THIRD)], THIRD)

        assert not verdict.approved
        assert verdict.matched is trap
        assert verdict.fell_into_trap

    def test_expected_wins_when_a_trap_has_the_same_keys(self, tmp_path):
        expected, valid, trap = self.references(tmp_path)

        verdict = judge([(expected, TOP), (valid, OTHER), (trap, TOP)], TOP)

        assert verdict.approved
        assert verdict.matched is expected

    def test_matching_nothing_reports_the_expected_comparison(self, tmp_path):
        expected, valid, trap = self.references(tmp_path)

        verdict = judge([(expected, TOP), (valid, OTHER), (trap, THIRD)], agent(("Nada", 1)))

        assert not verdict.approved
        assert verdict.matched is None
        assert not verdict.fell_into_trap
        assert verdict.comparison.key_columns == ("titulo",)


class TestMandatoryValueCheck:
    def references(self, tmp_path):
        path = write_gabarito(
            tmp_path / "g.json", [entry("Q11", ["Ingênua: SUM(lucro_brl) sem filtro"])]
        )
        classes = {"Q11": {"Ingênua: SUM(lucro_brl) sem filtro": Role.TRAP}}
        expected, trap = load_gabarito(path, classes=classes)[0].references
        return [(expected, EXPECTED_Q11), (trap, TRAP_Q11)]

    def judge_q11(self, tmp_path, actual: ResultTable):
        return judge(self.references(tmp_path), actual, value_column="lucro_total_brl")

    def test_exact_value_is_approved(self, tmp_path):
        actual = agent(*((row[0], row[1]) for row in EXPECTED_Q11.rows))

        verdict = self.judge_q11(tmp_path, actual)

        assert verdict.approved
        assert verdict.matched.role is Role.EXPECTED
        assert verdict.value_check is True

    def test_value_in_billions_is_approved(self, tmp_path):
        actual = agent(*((row[0], round(row[1] / 1e9, 2)) for row in EXPECTED_Q11.rows))

        assert self.judge_q11(tmp_path, actual).approved

    @pytest.mark.parametrize("scale", [1e3, 1e6])
    def test_value_in_thousands_or_millions_is_approved(self, tmp_path, scale):
        actual = agent(*((row[0], row[1] / scale) for row in EXPECTED_Q11.rows))

        assert self.judge_q11(tmp_path, actual).approved

    def test_value_within_one_percent_in_another_column_is_approved(self, tmp_path):
        actual = agent(
            *((row[0], row[3], row[1] * 0.995) for row in EXPECTED_Q11.rows),
            columns=("produtora", "filmes", "total_reais"),
        )

        assert self.judge_q11(tmp_path, actual).approved

    def test_trap_value_is_rejected_as_the_trap(self, tmp_path):
        actual = agent(*((row[0], row[1]) for row in TRAP_Q11.rows))

        verdict = self.judge_q11(tmp_path, actual)

        assert not verdict.approved
        assert verdict.fell_into_trap
        assert verdict.matched.label == "Ingênua: SUM(lucro_brl) sem filtro"
        assert verdict.value_check is False

    def test_value_matching_nothing_is_rejected_without_a_match(self, tmp_path):
        actual = agent(*((row[0], 50_000_000_000.0 - i) for i, row in enumerate(TRAP_Q11.rows)))

        verdict = self.judge_q11(tmp_path, actual)

        assert not verdict.approved
        assert verdict.matched is None
        assert not verdict.fell_into_trap

    def test_no_numeric_column_fails_the_value_check(self, tmp_path):
        actual = agent(*((row[0],) for row in EXPECTED_Q11.rows), columns=("produtora",))

        assert not self.judge_q11(tmp_path, actual).approved

    def test_questions_without_the_check_ignore_values(self, tmp_path):
        actual = agent(*((row[0], 1.0) for row in EXPECTED_Q11.rows))

        verdict = judge(self.references(tmp_path), actual)

        assert verdict.approved
        assert verdict.value_check is None


class TestRealTrapsAreDetected:
    def stored_references(self, question):
        return [(reference, reference.stored) for reference in question.references]

    @pytest.mark.parametrize(
        ("question_id", "trap_label"),
        [
            (question_id, label)
            for question_id, classes in REFERENCE_CLASSES.items()
            for label, role in classes.items()
            if role is Role.TRAP
        ],
    )
    def test_answering_with_the_trap_result_falls_into_the_trap(self, question_id, trap_label):
        question = next(q for q in load_gabarito(REAL_GABARITO) if q.id == question_id)
        trap = next(ref for ref in question.references if ref.label == trap_label)

        verdict = judge(
            self.stored_references(question),
            trap.stored,
            value_column=VALUE_CHECKED_QUESTIONS.get(question_id),
        )

        assert not verdict.approved
        assert verdict.fell_into_trap
        assert verdict.matched is not None and verdict.matched.role is Role.TRAP

    @pytest.mark.parametrize("question_id", [f"Q{n:02d}" for n in range(1, 15)])
    def test_answering_with_the_expected_result_is_approved(self, question_id):
        question = next(q for q in load_gabarito(REAL_GABARITO) if q.id == question_id)

        verdict = judge(
            self.stored_references(question),
            question.references[0].stored,
            value_column=VALUE_CHECKED_QUESTIONS.get(question_id),
        )

        assert verdict.approved
        assert verdict.matched is question.references[0]
