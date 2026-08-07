"""How deep a fleet document goes, as a rule rather than as four booleans."""

import pytest

from app.domain.fleet import LEVEL_ORDER, FleetLevel, reaches


class TestReaching:
    @pytest.mark.parametrize("level", list(FleetLevel))
    def test_every_level_reaches_itself(self, level: FleetLevel) -> None:
        assert reaches(level, level)

    def test_the_deepest_one_reaches_everything(self) -> None:
        for wanted in FleetLevel:
            assert reaches(FleetLevel.VARIABLES, wanted)

    def test_the_shallowest_one_reaches_only_itself(self) -> None:
        assert reaches(FleetLevel.SITIOS, FleetLevel.SITIOS)
        for wanted in (FleetLevel.GATEWAYS, FleetLevel.EQUIPOS, FleetLevel.VARIABLES):
            assert not reaches(FleetLevel.SITIOS, wanted)

    def test_it_is_transitive_down_the_chain(self) -> None:
        """Asking for devices implies the gateways and sites above them.

        There is no gateway without a site, so a level can never skip one.
        """
        assert reaches(FleetLevel.EQUIPOS, FleetLevel.GATEWAYS)
        assert reaches(FleetLevel.EQUIPOS, FleetLevel.SITIOS)
        assert not reaches(FleetLevel.EQUIPOS, FleetLevel.VARIABLES)


class TestTheOrder:
    def test_it_lists_every_level_exactly_once(self) -> None:
        """A level missing from the order would raise on comparison."""
        assert set(LEVEL_ORDER) == set(FleetLevel)
        assert len(LEVEL_ORDER) == len(FleetLevel)

    def test_it_follows_the_hierarchy(self) -> None:
        assert LEVEL_ORDER == (
            FleetLevel.SITIOS,
            FleetLevel.GATEWAYS,
            FleetLevel.EQUIPOS,
            FleetLevel.VARIABLES,
        )

    def test_the_values_are_what_the_query_string_carries(self) -> None:
        assert [level.value for level in LEVEL_ORDER] == [
            "sitios",
            "gateways",
            "equipos",
            "variables",
        ]
