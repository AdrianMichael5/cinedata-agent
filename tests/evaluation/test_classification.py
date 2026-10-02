import json
from pathlib import Path

import pytest

from cinedata_agent.evaluation.classification import (
    REFERENCE_CLASSES,
    VALUE_CHECKED_QUESTIONS,
    Role,
)
from cinedata_agent.evaluation.gabarito import EXPECTED_VARIANTS, load_gabarito

REAL_GABARITO = Path(__file__).resolve().parents[2] / "eval" / "gabarito.json"
RAW = json.loads(REAL_GABARITO.read_text(encoding="utf-8"))

VALID = Role.VALID_ALTERNATIVE
TRAP = Role.TRAP


def non_expected_titles(item: dict) -> set[str]:
    """Every reference of a question except the expected one."""
    titles = {item["principal"]["titulo"], *(variant["titulo"] for variant in item["variantes"])}
    expected = EXPECTED_VARIANTS.get(item["id"], item["principal"]["titulo"])
    return titles - {expected}


@pytest.mark.parametrize("item", RAW, ids=[item["id"] for item in RAW])
def test_every_non_expected_reference_is_classified(item):
    assert non_expected_titles(item) == set(REFERENCE_CLASSES.get(item["id"], {}))


def test_the_map_has_no_question_missing_from_the_gabarito():
    assert set(REFERENCE_CLASSES) <= {item["id"] for item in RAW}


def test_approved_classification():
    assert REFERENCE_CLASSES == {
        "Q01": {"Mesmo ranking em US$ (mostra a diferença)": TRAP},
        "Q02": {"Literal: só receita informada": VALID},
        "Q03": {"Margem sobre a receita": VALID},
        "Q04": {"Ingênua (o que um LLM tende a gerar)": TRAP},
        "Q05": {"Ambas as notas com votos": VALID},
        "Q07": {
            "Contando por id (ingênua)": TRAP,
            "Janela até a última data de lançamento da base, por obra": VALID,
        },
        "Q09": {"Por id (ingênua)": TRAP},
        "Q11": {"Ingênua: SUM(lucro_brl) sem filtro": TRAP},
        "Q12": {
            "Média simples da margem por filme": TRAP,
            "ROI médio (lucro / orçamento)": TRAP,
        },
        "Q13": {"Por id (ingênua)": TRAP},
        "Q14": {"Todos os filmes avaliados": VALID},
    }


def test_seven_questions_have_a_trap():
    with_trap = [qid for qid, classes in REFERENCE_CLASSES.items() if TRAP in classes.values()]

    assert len(with_trap) == 7


def test_value_check_column_exists_in_expected_and_trap_of_q11():
    q11 = next(question for question in load_gabarito(REAL_GABARITO) if question.id == "Q11")
    column = VALUE_CHECKED_QUESTIONS["Q11"]

    assert VALUE_CHECKED_QUESTIONS == {"Q11": "lucro_total_brl"}
    for reference in q11.references:
        assert reference.stored is not None
        assert column in reference.stored.columns
