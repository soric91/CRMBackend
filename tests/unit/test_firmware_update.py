"""Las reglas de una actualización remota, probadas sin equipos ni servidor.

Lo que se prueba acá es lo que rompe gateways en sedes lejanas: comparar mal
dos versiones deja un equipo actualizándose para siempre, calcular mal la
ventana lo reinicia a media mañana, y aceptar una transición vieja deja la
pantalla mintiendo sobre lo que está pasando en la planta.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.enums import FirmwareUpdateState as Estado
from app.domain.firmware_update import (
    MAX_INTENTOS,
    SHA256,
    TRANSICIONES,
    VENTANA_MINUTOS_MAXIMA,
    VersionInvalidaError,
    como_utc,
    comparar_versiones,
    dentro_de_ventana,
    es_descenso,
    misma_version,
    normalizar_version,
    paquete_url,
    parse_bandera,
    parse_hora,
    parse_ventana_minutos,
    parse_version,
    proxima_ventana,
    puede_reintentar,
    transicion_valida,
    validar_transicion,
    zona,
)

BOGOTA = "America/Bogota"
MADRID = "Europe/Madrid"


def utc(*partes: int) -> datetime:
    return datetime(*partes, tzinfo=UTC)  # pyright: ignore[reportArgumentType]


class TestLeerUnaVersion:
    @pytest.mark.parametrize(
        ("texto", "esperado"),
        [
            ("1.2.0", (1, 2, 0)),
            ("v1.2.0", (1, 2, 0)),
            ("  v1.2.0  ", (1, 2, 0)),
            ("0.0.1", (0, 0, 1)),
            ("10.20.30", (10, 20, 30)),
            # Ceros a la izquierda: feo, pero nombra la misma versión.
            ("v01.02.03", (1, 2, 3)),
        ],
    )
    def test_acepta_las_formas_en_las_que_se_escribe(
        self, texto: str, esperado: tuple[int, int, int]
    ) -> None:
        assert parse_version(texto) == esperado

    @pytest.mark.parametrize(
        "texto",
        [
            "",
            "   ",
            "1.2",
            "1.2.3.4",
            "v1.2.3-rc1",
            "latest",
            "V1.2.3",
            "1.2.x",
            "-1.2.3",
            "1.2.3-",
        ],
    )
    def test_rechaza_lo_que_no_se_puede_comparar(self, texto: str) -> None:
        """Una versión que no se puede comparar no se puede desplegar: el
        equipo no sabría si ya la tiene."""
        with pytest.raises(VersionInvalidaError):
            parse_version(texto)

    def test_normalizar_saca_la_v(self) -> None:
        assert normalizar_version("v1.2.0") == "1.2.0"
        assert normalizar_version("1.2.0") == "1.2.0"

    def test_el_orden_es_numerico_y_no_alfabetico(self) -> None:
        """`1.10.0` es posterior a `1.9.0`. Como texto sería al revés, y esa
        confusión mandaría a la flota hacia atrás."""
        assert comparar_versiones("1.10.0", "1.9.0") == 1
        assert comparar_versiones("1.9.0", "1.10.0") == -1
        assert comparar_versiones("v1.2.0", "1.2.0") == 0


class TestSaberSiYaEstaAlDia:
    def test_la_v_no_hace_diferente_a_la_misma_version(self) -> None:
        """El tag es `v1.2.0` y el equipo reporta `1.2.0`. Compararlas como
        texto haría que se actualizara a lo que ya tiene, cada noche."""
        assert misma_version("v1.2.0", "1.2.0")

    def test_versiones_distintas_no_son_la_misma(self) -> None:
        assert not misma_version("1.2.0", "1.2.1")

    def test_sin_version_reportada_no_se_afirma_que_esta_al_dia(self) -> None:
        assert not misma_version(None, "1.2.0")
        assert not misma_version("1.2.0", None)
        assert not misma_version(None, None)

    def test_una_version_ilegible_cuenta_como_desconocida(self) -> None:
        """Un equipo viejo puede reportar cualquier cosa. Que no se entienda
        significa 'no sé', y 'no sé' no es 'está al día'."""
        assert not misma_version("firmware-viejo", "1.2.0")


class TestVolverAtras:
    def test_una_version_anterior_es_un_descenso(self) -> None:
        assert es_descenso("1.3.0", "1.2.0")

    def test_la_misma_o_una_posterior_no_lo_es(self) -> None:
        assert not es_descenso("1.2.0", "1.2.0")
        assert not es_descenso("1.2.0", "1.3.0")

    def test_sin_saber_de_donde_viene_no_se_advierte_nada(self) -> None:
        assert not es_descenso(None, "1.2.0")
        assert not es_descenso("no-se-entiende", "1.2.0")


class TestLeerLaConfiguracionDeLaVentana:
    @pytest.mark.parametrize(
        ("texto", "hora", "minuto"),
        [("03:00", 3, 0), ("00:00", 0, 0), ("23:59", 23, 59), (" 03:30 ", 3, 30)],
    )
    def test_acepta_una_hora_del_dia(self, texto: str, hora: int, minuto: int) -> None:
        leida = parse_hora(texto)
        assert (leida.hour, leida.minute) == (hora, minuto)

    @pytest.mark.parametrize(
        "texto", ["", "3:00", "24:00", "03:60", "03:00:00", "0300", "tres", "-1:00"]
    )
    def test_rechaza_lo_que_no_es_una_hora(self, texto: str) -> None:
        """Se rechaza al guardar, en el panel, y no de madrugada en el equipo."""
        with pytest.raises(ValueError):
            parse_hora(texto)

    @pytest.mark.parametrize(
        ("texto", "esperado"), [("120", 120), (" 60 ", 60), ("1", 1)]
    )
    def test_acepta_una_duracion(self, texto: str, esperado: int) -> None:
        assert parse_ventana_minutos(texto) == esperado

    def test_acepta_el_tope_exacto(self) -> None:
        tope = VENTANA_MINUTOS_MAXIMA
        assert parse_ventana_minutos(str(tope)) == tope

    @pytest.mark.parametrize(
        "texto",
        ["0", "-5", "abc", "", "12.5", str(VENTANA_MINUTOS_MAXIMA + 1)],
    )
    def test_rechaza_una_duracion_que_no_es_una_ventana(self, texto: str) -> None:
        with pytest.raises(ValueError):
            parse_ventana_minutos(texto)

    def test_una_zona_desconocida_no_pasa_por_utc_en_silencio(self) -> None:
        """Caer en UTC sería aplicar la actualización a una hora que nadie
        eligió, en una sede que no se sabe dónde está."""
        with pytest.raises(ValueError, match="Zona horaria desconocida"):
            zona("Marte/Olympus")

    def test_una_zona_conocida_se_resuelve(self) -> None:
        assert zona(BOGOTA) == ZoneInfo(BOGOTA)


class TestCuandoSePuedeAplicar:
    HORA = parse_hora("03:00")

    def test_antes_de_la_ventana_espera_a_la_de_hoy(self) -> None:
        # 2026-08-21 01:00 en Bogotá (UTC-5) es 06:00 UTC.
        ahora = utc(2026, 8, 21, 6, 0)
        assert proxima_ventana(ahora, self.HORA, 120, BOGOTA) == utc(2026, 8, 21, 8, 0)

    def test_dentro_de_la_ventana_se_aplica_ya(self) -> None:
        """Quien apretó el botón a las 03:30 con la ventana abierta no quiso
        esperar hasta mañana."""
        ahora = utc(2026, 8, 21, 8, 30)  # 03:30 en Bogotá
        assert proxima_ventana(ahora, self.HORA, 120, BOGOTA) == ahora

    def test_justo_al_empezar_ya_esta_dentro(self) -> None:
        ahora = utc(2026, 8, 21, 8, 0)
        assert proxima_ventana(ahora, self.HORA, 120, BOGOTA) == ahora

    def test_justo_al_terminar_la_ventana_ya_paso(self) -> None:
        """El final es exclusivo: a las 05:00 en punto la ventana de dos horas
        está cerrada, y el equipo espera a la de mañana."""
        ahora = utc(2026, 8, 21, 10, 0)  # 05:00 en Bogotá
        assert proxima_ventana(ahora, self.HORA, 120, BOGOTA) == utc(2026, 8, 22, 8, 0)

    def test_despues_de_la_ventana_espera_a_la_de_manana(self) -> None:
        ahora = utc(2026, 8, 21, 20, 0)  # 15:00 en Bogotá
        assert proxima_ventana(ahora, self.HORA, 120, BOGOTA) == utc(2026, 8, 22, 8, 0)

    def test_una_ventana_que_cruza_la_medianoche_sigue_abierta(self) -> None:
        """23:30 + 2h: a las 00:30 el equipo sigue dentro de la de ayer, y
        tratarla como cerrada lo haría esperar un día entero."""
        hora = parse_hora("23:30")
        ahora = utc(2026, 8, 21, 5, 30)  # 00:30 del 21 en Bogotá
        assert proxima_ventana(ahora, hora, 120, BOGOTA) == ahora

    def test_el_resultado_siempre_es_utc(self) -> None:
        resultado = proxima_ventana(utc(2026, 8, 21, 6, 0), self.HORA, 120, BOGOTA)
        assert resultado.tzinfo is not None
        assert resultado.utcoffset() == timedelta(0)

    def test_la_hora_es_la_de_la_sede_y_no_la_del_servidor(self) -> None:
        """Las 03:00 de Madrid y las de Bogotá son instantes distintos. Una
        sola hora global reiniciaría media flota a media tarde."""
        ahora = utc(2026, 8, 21, 0, 0)
        en_madrid = proxima_ventana(ahora, self.HORA, 120, MADRID)
        en_bogota = proxima_ventana(ahora, self.HORA, 120, BOGOTA)
        assert en_madrid == utc(2026, 8, 21, 1, 0)  # verano: UTC+2
        assert en_bogota == utc(2026, 8, 21, 8, 0)  # todo el año: UTC-5
        assert en_madrid < en_bogota


class TestCambiosDeHora:
    """El día que el reloj salta es el día en que un cálculo ingenuo se
    saltea una noche entera de actualizaciones, o la corre dos veces."""

    def test_una_hora_local_que_no_existe_se_corre_hacia_adelante(self) -> None:
        # En Madrid, el 29/03/2026 el reloj salta de 02:00 a 03:00: las 02:30
        # no existen. La ventana tiene que empezar apenas se pueda.
        hora = parse_hora("02:30")
        ahora = utc(2026, 3, 28, 23, 0)  # 00:00 del 29 en Madrid
        resultado = proxima_ventana(ahora, hora, 120, MADRID)
        assert resultado == utc(2026, 3, 29, 1, 30)
        # Y ese instante, visto en la sede, ya está del otro lado del salto.
        assert resultado.astimezone(ZoneInfo(MADRID)).hour == 3

    def test_una_hora_local_repetida_toma_la_primera_vez(self) -> None:
        # El 25/10/2026 el reloj vuelve de 03:00 a 02:00: las 02:30 pasan dos
        # veces. Se toma la primera, que es la que llega antes.
        hora = parse_hora("02:30")
        ahora = utc(2026, 10, 24, 22, 0)  # 00:00 del 25 en Madrid
        assert proxima_ventana(ahora, hora, 120, MADRID) == utc(2026, 10, 25, 0, 30)


class TestSeguirDentroDeLaVentana:
    DESDE = utc(2026, 8, 21, 8, 0)

    def test_antes_de_empezar_no(self) -> None:
        assert not dentro_de_ventana(self.DESDE - timedelta(seconds=1), self.DESDE, 120)

    def test_justo_al_empezar_si(self) -> None:
        assert dentro_de_ventana(self.DESDE, self.DESDE, 120)

    def test_en_el_medio_si(self) -> None:
        assert dentro_de_ventana(self.DESDE + timedelta(minutes=90), self.DESDE, 120)

    def test_al_cumplirse_los_minutos_ya_no(self) -> None:
        justo_al_final = self.DESDE + timedelta(minutes=120)
        assert not dentro_de_ventana(justo_al_final, self.DESDE, 120)

    def test_un_equipo_que_despierta_a_media_manana_no_actualiza(self) -> None:
        """Aplicar fuera de la ventana reinicia una planta en producción."""
        assert not dentro_de_ventana(self.DESDE + timedelta(hours=6), self.DESDE, 120)


class TestTransicionesDeEstado:
    def test_todos_los_estados_tienen_una_regla(self) -> None:
        """Un estado sin fila en la tabla haría explotar el acuse del equipo
        con un KeyError en vez de rechazarlo."""
        assert set(TRANSICIONES) == set(Estado)

    @pytest.mark.parametrize(
        ("actual", "nuevo"),
        [
            (Estado.SIN_PENDIENTE, Estado.PROGRAMADA),
            (Estado.PROGRAMADA, Estado.DESCARGANDO),
            (Estado.PROGRAMADA, Estado.SIN_PENDIENTE),
            (Estado.DESCARGANDO, Estado.APLICANDO),
            (Estado.DESCARGANDO, Estado.FALLIDA),
            (Estado.APLICANDO, Estado.APLICADA),
            (Estado.APLICANDO, Estado.FALLIDA),
            (Estado.APLICADA, Estado.PROGRAMADA),
            (Estado.FALLIDA, Estado.PROGRAMADA),
            (Estado.FALLIDA, Estado.SIN_PENDIENTE),
        ],
    )
    def test_el_recorrido_normal_y_sus_salidas(
        self, actual: Estado, nuevo: Estado
    ) -> None:
        assert transicion_valida(actual, nuevo)

    @pytest.mark.parametrize(
        ("actual", "nuevo"),
        [
            # Saltearse el trabajo: nadie aplica sin haber descargado.
            (Estado.SIN_PENDIENTE, Estado.APLICADA),
            (Estado.SIN_PENDIENTE, Estado.DESCARGANDO),
            (Estado.PROGRAMADA, Estado.APLICADA),
            # Un acuse viejo que llega tarde y quiere volver atrás.
            (Estado.APLICANDO, Estado.DESCARGANDO),
            (Estado.APLICADA, Estado.DESCARGANDO),
            (Estado.APLICADA, Estado.FALLIDA),
            # Cancelar mientras el equipo ya se está reiniciando: la pantalla
            # diría algo que no va a pasar.
            (Estado.APLICANDO, Estado.SIN_PENDIENTE),
            (Estado.APLICANDO, Estado.PROGRAMADA),
        ],
    )
    def test_lo_que_no_puede_pasar(self, actual: Estado, nuevo: Estado) -> None:
        assert not transicion_valida(actual, nuevo)

    @pytest.mark.parametrize("estado", list(Estado))
    def test_repetir_el_mismo_estado_siempre_vale(self, estado: Estado) -> None:
        """El acuse se manda por una red que se corta y el equipo reintenta.
        Tratar el segundo acuse como error llenaría de fallos falsos una flota
        que está funcionando."""
        assert transicion_valida(estado, estado)

    def test_validar_explota_con_los_dos_estados_en_el_mensaje(self) -> None:
        with pytest.raises(ValueError, match=r"aplicando.*programada"):
            validar_transicion(Estado.APLICANDO, Estado.PROGRAMADA)

    def test_validar_calla_cuando_la_transicion_vale(self) -> None:
        validar_transicion(Estado.PROGRAMADA, Estado.DESCARGANDO)


class TestReintentos:
    @pytest.mark.parametrize("intentos", [0, 1, MAX_INTENTOS - 1])
    def test_queda_margen(self, intentos: int) -> None:
        assert puede_reintentar(intentos)

    @pytest.mark.parametrize("intentos", [MAX_INTENTOS, MAX_INTENTOS + 5])
    def test_se_deja_de_insistir(self, intentos: int) -> None:
        """Un equipo que falla tres noches seguidas tiene un problema que otro
        reintento no arregla, y seguir intentando lo reinicia cada noche."""
        assert not puede_reintentar(intentos)


class TestChecksum:
    def test_acepta_un_sha256_en_minusculas(self) -> None:
        assert SHA256.match("a" * 64)

    @pytest.mark.parametrize(
        "valor", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, f"{'a' * 63} "]
    )
    def test_rechaza_lo_que_no_es_un_sha256(self, valor: str) -> None:
        """Un checksum mal pegado se descubriría después de bajar el paquete
        entero, por 4G, en una sede remota."""
        assert not SHA256.match(valor)


class TestLaBanderaDeEncendido:
    @pytest.mark.parametrize(
        "texto", ["true", "TRUE", " True ", "1", "si", "sí", "yes", "on"]
    )
    def test_lo_que_enciende(self, texto: str) -> None:
        assert parse_bandera(texto)

    @pytest.mark.parametrize("texto", ["false", "0", "", "   ", "tal vez", "no", "off"])
    def test_todo_lo_demas_deja_apagado(self, texto: str) -> None:
        """La variable que actualiza a toda la flota no puede encenderse por
        un valor que nadie entiende."""
        assert not parse_bandera(texto)


class TestLaDireccionDelPaquete:
    def test_se_arma_igual_que_en_el_enrolamiento(self) -> None:
        """Los dos bajan el mismo archivo: si el nombre cambia, tiene que
        cambiar en un solo lugar."""
        assert (
            paquete_url("https://ems.example/rel", "v1.2.0")
            == "https://ems.example/rel/gatewayEMS-v1.2.0.tar.gz"
        )

    def test_una_barra_de_mas_no_parte_la_direccion(self) -> None:
        assert paquete_url("https://ems.example/rel/", "v1.2.0").count("//") == 1


class TestInstantesSinZona:
    def test_una_fila_sin_zona_se_lee_como_utc(self) -> None:
        """SQLite no guarda la zona. Compararla con un instante con zona
        explota, y explotaría en la consulta del equipo, de madrugada."""
        sin_zona = datetime(2026, 8, 21, 8, 0)

        assert como_utc(sin_zona) == utc(2026, 8, 21, 8, 0)

    def test_uno_que_ya_tiene_zona_no_se_toca(self) -> None:
        con_zona = utc(2026, 8, 21, 8, 0)

        assert como_utc(con_zona) is con_zona

    def test_la_ventana_tolera_una_fila_sin_zona(self) -> None:
        desde = datetime(2026, 8, 21, 8, 0)

        assert dentro_de_ventana(utc(2026, 8, 21, 9, 0), desde, 120)
