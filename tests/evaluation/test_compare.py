import pytest

from cinedata_agent.evaluation.compare import (
    ResultTable,
    compare_results,
    normalize_key,
    results_agree,
    top1_value_matches,
)

EXPECTED = ResultTable(
    columns=("titulo", "ano_lancamento", "receita_brl"),
    rows=(
        ("Avatar: The Way Of Water", 2022, 12_390_136_500.54),
        ("Avengers: Endgame", 2019, 11_094_720_000.0),
        ("Spider-man: No Way Home", 2021, 10_977_782_882.74),
        ("Avengers: Infinity War", 2018, 7_190_430_847.63),
        ("Top Gun: Maverick", 2022, 7_160_804_869.01),
        ("Barbie", 2023, 6_800_000_000.0),
    ),
)


def table(columns, rows) -> ResultTable:
    return ResultTable(columns=tuple(columns), rows=tuple(tuple(row) for row in rows))


class TestNormalizeKey:
    @pytest.mark.parametrize(
        ("raw", "normalized"),
        [
            ("  Coração   Valente ", "coracao valente"),
            ("AVATAR", "avatar"),
            ("Pokémon", "pokemon"),
            (2016, "2016"),
            (2016.0, "2016"),
            (None, ""),
        ],
    )
    def test_normalizes_case_accents_spaces_and_whole_numbers(self, raw, normalized):
        assert normalize_key(raw) == normalized


class TestCompareResults:
    def test_identical_result_is_approved(self):
        comparison = compare_results(EXPECTED, EXPECTED)

        assert comparison.top1_ok
        assert comparison.recall == 1.0
        assert comparison.k == 5
        assert comparison.approved
        assert comparison.key_columns == ("titulo",)
        assert comparison.matched_columns == ("titulo",)

    def test_wrong_top1_is_rejected(self):
        rows = list(EXPECTED.rows)
        rows[0], rows[1] = rows[1], rows[0]

        comparison = compare_results(EXPECTED, table(EXPECTED.columns, rows))

        assert not comparison.top1_ok
        assert comparison.recall == 1.0
        assert not comparison.approved

    def test_column_with_another_name_and_position_is_found(self):
        actual = table(
            ("receita_total_brl", "nome_do_filme"),
            [(row[2], row[0].upper()) for row in EXPECTED.rows],
        )

        comparison = compare_results(EXPECTED, actual)

        assert comparison.matched_columns == ("nome_do_filme",)
        assert comparison.approved

    def test_order_after_top1_does_not_matter_for_recall(self):
        rows = [EXPECTED.rows[0], *reversed(EXPECTED.rows[1:5])]

        comparison = compare_results(EXPECTED, table(EXPECTED.columns, rows))

        assert comparison.top1_ok
        assert comparison.recall == 1.0
        assert comparison.approved

    @pytest.mark.parametrize(
        ("hits", "recall", "approved"),
        [(5, 1.0, True), (4, 0.8, True), (3, 0.6, False)],
    )
    def test_recall_threshold(self, hits, recall, approved):
        rows = [list(row) for row in EXPECTED.rows[:5]]
        for index in range(hits, 5):
            rows[index][0] = f"Outro filme {index}"

        comparison = compare_results(EXPECTED, table(EXPECTED.columns, rows))

        assert comparison.recall == pytest.approx(recall)
        assert comparison.approved is approved

    def test_repeated_keys_count_once_in_the_recall(self):
        # A ranking by id repeats a title when the same film has several ids (Q13's trap).
        expected = table(
            ("titulo", "avaliacoes"),
            [("Die Hart 2", 13), ("Die Hart 2", 12), ("Die Hart 2", 11), ("Rec", 10), ("Up", 9)],
        )

        comparison = compare_results(expected, expected)

        assert comparison.recall == 1.0
        assert comparison.approved

    def test_short_expected_uses_its_length_as_k(self):
        expected = table(("nome_genero", "filmes"), [("Drama", 10), ("Comedy", 8)])

        comparison = compare_results(expected, expected)

        assert comparison.k == 2
        assert comparison.approved

    def test_numeric_key_when_there_is_no_text_column(self):
        expected = table(("ano_lancamento", "nota"), [(2016, 6.34), (2015, 6.30), (2017, 6.2)])
        actual = table(("nota_media", "ano"), [(6.34, 2016.0), (6.30, 2015), (6.2, 2017)])

        comparison = compare_results(expected, actual)

        assert comparison.key_columns == ("ano_lancamento",)
        assert comparison.matched_columns == ("ano",)
        assert comparison.approved

    def test_pair_key_matches_two_columns(self):
        expected = table(
            ("ator", "diretor", "filmes_juntos"),
            [("Joe Anoa'i", "Kevin Dunn", 37), ("A", "B", 20), ("C", "D", 10)],
        )
        actual = table(
            ("diretor", "ator", "total"),
            [("Kevin Dunn", "Joe Anoa'i", 37), ("B", "A", 20), ("D", "C", 10)],
        )

        comparison = compare_results(expected, actual)

        assert comparison.key_columns == ("ator", "diretor")
        assert comparison.matched_columns == ("ator", "diretor")
        assert comparison.approved

    def test_pair_with_the_wrong_partner_fails_top1(self):
        expected = table(("ator", "diretor"), [("A", "B"), ("C", "D")])
        actual = table(("ator", "diretor"), [("A", "D"), ("C", "B")])

        assert not compare_results(expected, actual).top1_ok

    def test_date_column_is_not_part_of_the_key(self):
        expected = table(
            ("titulo", "data_lancamento", "avaliacoes"), [("Die Hart 2", "2024-05-30", 155)]
        )
        actual = table(("titulo", "avaliacoes"), [("Die Hart 2", 155)])

        comparison = compare_results(expected, actual)

        assert comparison.key_columns == ("titulo",)
        assert comparison.approved

    @pytest.mark.parametrize(
        ("factor", "value_ok"),
        [(1.0, True), (1.005, True), (0.991, True), (1.05, False)],
    )
    def test_top1_value_tolerance_is_one_percent(self, factor, value_ok):
        rows = [(row[0], row[1], row[2] * factor) for row in EXPECTED.rows]

        comparison = compare_results(EXPECTED, table(EXPECTED.columns, rows))

        assert comparison.value_ok is value_ok
        assert comparison.approved  # values are informative, approval is top1 + recall

    def test_values_matched_by_value_when_names_differ(self):
        actual = table(("filme", "total_reais"), [(row[0], row[2]) for row in EXPECTED.rows])

        assert compare_results(EXPECTED, actual).value_ok is True

    def test_value_check_is_none_without_numeric_columns(self):
        actual = table(("filme",), [(row[0],) for row in EXPECTED.rows])

        assert compare_results(EXPECTED, actual).value_ok is None

    def test_missing_result_is_rejected(self):
        comparison = compare_results(EXPECTED, None)

        assert not comparison.approved
        assert comparison.recall == 0.0
        assert comparison.matched_columns == ()

    def test_empty_result_is_rejected(self):
        comparison = compare_results(EXPECTED, table(EXPECTED.columns, []))

        assert not comparison.top1_ok
        assert not comparison.approved

    def test_result_without_any_matching_column_is_rejected(self):
        actual = table(("x",), [("nada",), ("disso",)])

        comparison = compare_results(EXPECTED, actual)

        assert comparison.matched_columns == ()
        assert not comparison.approved


class TestTop1ValueMatches:
    def test_scaled_value_matches(self):
        actual = table(("titulo", "receita_bi"), [("Avatar", 12.39)])

        assert top1_value_matches(EXPECTED, "receita_brl", actual)

    @pytest.mark.parametrize(
        ("column", "actual"),
        [
            ("receita_brl", None),
            ("receita_brl", table(("titulo", "x"), [])),
            ("nao_existe", table(("titulo", "x"), [("Avatar", 1.0)])),
            ("titulo", table(("titulo", "x"), [("Avatar", 1.0)])),
        ],
        ids=["no_result", "empty_result", "unknown_column", "text_column"],
    )
    def test_nothing_to_compare_is_not_a_match(self, column, actual):
        assert not top1_value_matches(EXPECTED, column, actual)


class TestResultsAgree:
    def test_same_rows_with_float_noise_agree(self):
        computed = table(EXPECTED.columns, [(r[0], r[1], r[2] + 1e-4) for r in EXPECTED.rows])

        assert results_agree(EXPECTED, computed)

    @pytest.mark.parametrize(
        "change",
        [
            lambda rows: rows[:-1],
            lambda rows: [("Outro", *rows[0][1:]), *rows[1:]],
            lambda rows: [(rows[0][0], rows[0][1], rows[0][2] * 1.01), *rows[1:]],
        ],
        ids=["row_count", "text", "number"],
    )
    def test_differences_are_detected(self, change):
        computed = table(EXPECTED.columns, change(list(EXPECTED.rows)))

        assert not results_agree(EXPECTED, computed)

    def test_rows_of_different_widths_disagree(self):
        computed = table(EXPECTED.columns, [row[:2] for row in EXPECTED.rows])

        assert not results_agree(EXPECTED, computed)

    def test_column_names_must_match(self):
        renamed = table(("nome", "ano", "receita"), EXPECTED.rows)

        assert not results_agree(EXPECTED, renamed)
