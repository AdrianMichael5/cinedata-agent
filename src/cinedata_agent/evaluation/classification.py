"""Which non-expected references count as a valid reading and which are documented traps.

VALID_ALTERNATIVE: another legitimate reading of the question; matching it approves.
TRAP: the mistake the gabarito documents; matching it fails the question, named in the report.
Every reference of eval/gabarito.json other than the expected one must appear here: a new
variant without a class fails the tests and load_gabarito.
"""

from enum import StrEnum


class Role(StrEnum):
    EXPECTED = "esperada"
    VALID_ALTERNATIVE = "alternativa"
    TRAP = "armadilha"


VALID = Role.VALID_ALTERNATIVE
TRAP = Role.TRAP

REFERENCE_CLASSES: dict[str, dict[str, Role]] = {
    "Q01": {"Mesmo ranking em US$ (mostra a diferença)": TRAP},
    "Q02": {"Literal: só receita informada": VALID},
    # Q03, Q05 and Q14 expect the variant with a minimum filter; the main answer stays valid.
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

# Questions whose trap lists the same entities in the same order as the expected answer, so
# only the value tells them apart. Maps the question to the reference's main numeric column.
VALUE_CHECKED_QUESTIONS: dict[str, str] = {
    # Q11: summing every lucro_brl (no revenue/budget filter) ranks the same 10 producers;
    # the top-1 profit differs by ~3.9% (R$ 63.94 bi instead of R$ 61.55 bi).
    "Q11": "lucro_total_brl",
}
