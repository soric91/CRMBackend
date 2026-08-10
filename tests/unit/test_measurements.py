"""El catálogo de mediciones.

Su valor es negativo: existe para que ciertas cosas sean imposibles. Que
`Voltaje A` y `VOLTAGE_A` convivan, que la unidad se escriba de dos formas,
que un contador admita promedios.
"""

import pytest

from app.domain.measurements import (
    CATALOGO,
    POR_NOMBRE,
    Fase,
    Magnitud,
    buscar,
    es_acumulativa,
)


class TestItIsAClosedList:
    def test_every_name_is_unique(self) -> None:
        """Dos entradas con el mismo nombre harían ambiguo el catálogo."""
        assert len({medicion.nombre for medicion in CATALOGO}) == len(CATALOGO)

    def test_the_index_covers_everything(self) -> None:
        assert len(POR_NOMBRE) == len(CATALOGO)

    def test_an_invented_name_is_not_in_it(self) -> None:
        """Los que produjeron el problema original."""
        for inventado in ("Voltaje A", "VOLTAGE_A", "V1", "Tension_L1", "voltaje_l1"):
            assert buscar(inventado) is None

    @pytest.mark.parametrize("medicion", CATALOGO, ids=lambda m: m.nombre)
    def test_every_entry_has_a_readable_label(self, medicion: object) -> None:
        """La etiqueta es lo único pensado para leerse; sin ella no hay panel."""
        assert getattr(medicion, "etiqueta")  # noqa: B009


class TestUnitsCannotDrift:
    """La unidad se deriva. Nadie la teclea, así que `kw` contra `kW` no existe."""

    @pytest.mark.parametrize(
        ("nombre", "unidad"),
        [
            ("PhV_phsA", "V"),
            ("PPV_phsAB", "V"),
            ("A_phsA", "A"),
            ("TotW", "kW"),
            ("TotVAr", "kvar"),
            ("TotVA", "kVA"),
            ("Hz", "Hz"),
            ("TotWh_import", "kWh"),
        ],
    )
    def test_the_unit_comes_from_what_is_measured(
        self, nombre: str, unidad: str
    ) -> None:
        medicion = buscar(nombre)
        assert medicion is not None
        assert medicion.unidad == unidad

    def test_the_power_factor_has_none(self) -> None:
        """Es adimensional. Inventarle una unidad sería peor que dejarla vacía."""
        medicion = buscar("TotPF")
        assert medicion is not None
        assert medicion.unidad == ""

    def test_the_same_magnitude_always_uses_the_same_unit(self) -> None:
        por_magnitud: dict[Magnitud, set[str]] = {}
        for medicion in CATALOGO:
            por_magnitud.setdefault(medicion.magnitud, set()).add(medicion.unidad)

        for magnitud, unidades in por_magnitud.items():
            assert len(unidades) == 1, f"{magnitud} usa {unidades}"


class TestCountersAreDerived:
    """Acumulativa sale de la magnitud, no de una lista aparte que se olvide."""

    @pytest.mark.parametrize(
        "nombre", ["TotWh_import", "TotWh_export", "TotVArh_import", "TotVArh_export"]
    )
    def test_energy_is_a_counter(self, nombre: str) -> None:
        medicion = buscar(nombre)
        assert medicion is not None
        assert medicion.acumulativa is True

    @pytest.mark.parametrize("nombre", ["PhV_phsA", "A_phsA", "TotW", "TotPF", "Hz"])
    def test_everything_else_is_instantaneous(self, nombre: str) -> None:
        """Una potencia tratada como contador daría consumos absurdos."""
        medicion = buscar(nombre)
        assert medicion is not None
        assert medicion.acumulativa is False

    def test_import_and_export_are_different_magnitudes(self) -> None:
        """Sumarlas daría un consumo neto que no es lo que se factura."""
        importada = buscar("TotWh_import")
        exportada = buscar("TotWh_export")
        assert importada is not None and exportada is not None
        assert importada.magnitud != exportada.magnitud

    def test_the_helper_agrees_with_the_entries(self) -> None:
        for medicion in CATALOGO:
            assert medicion.acumulativa == es_acumulativa(medicion.magnitud)


class TestThePhasesThatWereMissing:
    """El origen concreto: la fase C no existía y caía a un desplegable."""

    @pytest.mark.parametrize("fase", [Fase.A, Fase.B, Fase.C])
    def test_voltage_and_current_cover_all_three(self, fase: Fase) -> None:
        for prefijo in ("PhV", "A"):
            assert buscar(f"{prefijo}_phs{fase.value}") is not None

    def test_grouping_by_magnitude_is_possible(self) -> None:
        """Con esto el panel arma una tarjeta por magnitud, sin campos fijos."""
        tensiones = [m for m in CATALOGO if m.magnitud is Magnitud.TENSION]

        assert {m.fase for m in tensiones} == {Fase.A, Fase.B, Fase.C}


class TestDigitalIO:
    def test_relays_are_expressible(self) -> None:
        """`tipo_registro` admite `coil`; sin estas entradas, un relé no se
        podría cargar y el catálogo dejaría de ser usable."""
        assert buscar("Ind01") is not None
        assert buscar("Ind08") is not None

    def test_they_are_not_electrical_magnitudes(self) -> None:
        medicion = buscar("Ind01")
        assert medicion is not None
        assert medicion.magnitud is Magnitud.ESTADO_DIGITAL
        assert medicion.acumulativa is False


class TestFourQuadrantEnergy:
    """Los contadores de un medidor de cuatro cuadrantes.

    Un medidor así lleva la reactiva separada por combinación de signo entre
    potencia activa y reactiva. Sirve para facturar penalizaciones por factor
    de potencia, donde se mira un cuadrante concreto y no la suma.
    """

    def test_the_four_counters_are_in_the_catalogue(self) -> None:
        nombres = {m.nombre for m in CATALOGO}

        assert {"Q1Eq", "Q2Eq", "Q3Eq", "Q4Eq"} <= nombres

    def test_all_four_are_counters(self) -> None:
        """Son acumulados. Sin esta marca el panel promediaría un contador
        monótono y mostraría un consumo que no existe."""
        for nombre in ("Q1Eq", "Q2Eq", "Q3Eq", "Q4Eq"):
            medicion = next(m for m in CATALOGO if m.nombre == nombre)
            assert es_acumulativa(medicion.magnitud), nombre

    def test_they_are_split_by_which_way_the_reactive_flows(self) -> None:
        """Q1 y Q2 consumen reactiva; Q3 y Q4 la entregan.

        Es lo que decide de qué lado del balance cae cada contador, y
        confundirlo invertiría el signo de la penalización.
        """
        por_nombre = {m.nombre: m for m in CATALOGO}

        assert por_nombre["Q1Eq"].magnitud is Magnitud.ENERGIA_REACTIVA_IMPORTADA
        assert por_nombre["Q2Eq"].magnitud is Magnitud.ENERGIA_REACTIVA_IMPORTADA
        assert por_nombre["Q3Eq"].magnitud is Magnitud.ENERGIA_REACTIVA_EXPORTADA
        assert por_nombre["Q4Eq"].magnitud is Magnitud.ENERGIA_REACTIVA_EXPORTADA

    def test_the_quadrant_is_readable_in_the_label(self) -> None:
        """El nombre canónico no dice nada a quien lo lee: `Q3Eq` es un
        registro, no una explicación."""
        por_nombre = {m.nombre: m for m in CATALOGO}

        assert "Q1" in por_nombre["Q1Eq"].etiqueta
        assert "inductiva" in por_nombre["Q1Eq"].etiqueta
        assert "capacitiva" in por_nombre["Q4Eq"].etiqueta

    def test_they_are_measured_in_kvarh(self) -> None:
        for nombre in ("Q1Eq", "Q2Eq", "Q3Eq", "Q4Eq"):
            medicion = next(m for m in CATALOGO if m.nombre == nombre)
            assert medicion.unidad == "kvarh", nombre
