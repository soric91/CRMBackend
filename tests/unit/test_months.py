"""Spanish month names used to display tariff periods."""

from datetime import date

import pytest

from app.domain.months import (
    MONTH_NAMES_ES,
    first_day,
    month_label,
    month_name,
    parse_month_name,
)


class TestNames:
    def test_there_are_twelve_of_them(self) -> None:
        assert len(MONTH_NAMES_ES) == 12

    @pytest.mark.parametrize(
        ("month", "expected"),
        [(1, "enero"), (5, "mayo"), (6, "junio"), (12, "diciembre")],
    )
    def test_a_date_renders_its_month_name(self, month: int, expected: str) -> None:
        assert month_name(date(2026, month, 1)) == expected

    def test_the_label_carries_the_year(self) -> None:
        assert month_label(date(2026, 6, 1)) == "junio 2026"

    def test_the_same_month_of_two_years_reads_differently(self) -> None:
        assert month_label(date(2026, 1, 1)) != month_label(date(2027, 1, 1))


class TestParsing:
    @pytest.mark.parametrize(
        ("name", "expected"), [("enero", 1), ("mayo", 5), ("junio", 6)]
    )
    def test_a_month_name_maps_back_to_its_number(
        self, name: str, expected: int
    ) -> None:
        assert parse_month_name(name) == expected

    @pytest.mark.parametrize("name", ["ENERO", "  Junio  ", "MaYo"])
    def test_case_and_blanks_are_ignored(self, name: str) -> None:
        assert parse_month_name(name) in range(1, 13)

    @pytest.mark.parametrize("name", ["january", "enerio", "", "13"])
    def test_an_unknown_name_is_rejected(self, name: str) -> None:
        with pytest.raises(ValueError, match="not a Spanish month name"):
            parse_month_name(name)

    def test_every_name_round_trips(self) -> None:
        for number, name in enumerate(MONTH_NAMES_ES, start=1):
            assert parse_month_name(name) == number


class TestFirstDay:
    def test_a_month_name_becomes_the_first_of_that_month(self) -> None:
        assert first_day(2026, "junio") == date(2026, 6, 1)

    def test_a_month_number_works_too(self) -> None:
        assert first_day(2026, 6) == date(2026, 6, 1)

    def test_an_unknown_month_name_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a Spanish month name"):
            first_day(2026, "smarch")
