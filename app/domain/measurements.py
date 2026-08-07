"""El catálogo de magnitudes eléctricas que un medidor puede reportar.

Lista cerrada, a propósito. Antes el nombre de una variable era texto libre, y
cada persona escribía el suyo: `Voltaje A`, `VOLTAGE_A`, `V1`, `Tension_L1`.
Todos correctos, ninguno comparable, y el panel quedaba sin datos sin que nada
fallara.

Acá el nombre se elige de una lista. Quien carga un equipo dice *qué* está
midiendo, no *cómo* llamarlo, y de esa elección salen el nombre, la magnitud,
la fase y la unidad. Una variable nueva se agrega a este archivo — un lugar,
revisable — y aparece en el panel sin tocar nada más.

Los nombres siguen **IEC 61850**, nodos lógicos `MMXU` (medición) y `MMTR`
(energía), que es lo que ya usan Schneider, Siemens, SEL y SATEC. No se
inventó vocabulario: `PhV.phsA` es tensión fase-neutro de la fase A en
cualquier equipo del mundo que hable el estándar.

El nombre canónico es **también** el que el gateway publica por MQTT y escribe
en InfluxDB. Esa es toda la simplificación: sin dos vocabularios, no hay
traducción que mantener ni que se pueda desincronizar.
"""

from dataclasses import dataclass
from enum import StrEnum


class Magnitud(StrEnum):
    """Qué se está midiendo. Decide cómo se agrega y cómo se grafica."""

    TENSION = "tension"
    TENSION_COMPUESTA = "tension_compuesta"
    CORRIENTE = "corriente"
    POTENCIA_ACTIVA = "potencia_activa"
    POTENCIA_REACTIVA = "potencia_reactiva"
    POTENCIA_APARENTE = "potencia_aparente"
    FACTOR_POTENCIA = "factor_potencia"
    FRECUENCIA = "frecuencia"
    # Contadores. Monótonos crecientes: solo admiten difference() y last().
    ENERGIA_IMPORTADA = "energia_importada"
    ENERGIA_EXPORTADA = "energia_exportada"
    ENERGIA_REACTIVA_IMPORTADA = "energia_reactiva_importada"
    ENERGIA_REACTIVA_EXPORTADA = "energia_reactiva_exportada"
    # No es una magnitud eléctrica: es el estado de un relé o una entrada
    # digital. Va acá porque `tipo_registro` admite `coil` y `discrete`, y
    # dejarlo afuera haría imposible cargar esas variables.
    ESTADO_DIGITAL = "estado_digital"


class Fase(StrEnum):
    """A qué fase corresponde la lectura."""

    A = "A"
    B = "B"
    C = "C"
    AB = "AB"
    BC = "BC"
    CA = "CA"
    NEUTRO = "N"
    TOTAL = "total"


# Las magnitudes que son contadores. Derivado, nunca guardado: un campo
# `acumulativa` en la base sería una segunda verdad que nada mantiene al día.
CUMULATIVE_MAGNITUDES: frozenset[Magnitud] = frozenset(
    {
        Magnitud.ENERGIA_IMPORTADA,
        Magnitud.ENERGIA_EXPORTADA,
        Magnitud.ENERGIA_REACTIVA_IMPORTADA,
        Magnitud.ENERGIA_REACTIVA_EXPORTADA,
    }
)


def es_acumulativa(magnitud: Magnitud) -> bool:
    """Si la magnitud es un contador que solo crece."""
    return magnitud in CUMULATIVE_MAGNITUDES


@dataclass(frozen=True)
class Medicion:
    """Una entrada del catálogo."""

    # El nombre que viaja por MQTT y se guarda en InfluxDB. IEC 61850.
    nombre: str
    # Cómo se muestra en el panel. Lo único pensado para leerse.
    etiqueta: str
    magnitud: Magnitud
    fase: Fase
    # La unidad no se pregunta: se deriva de qué se está midiendo. Así no
    # existe el caso de `kw` contra `kW` que ya tuvimos.
    unidad: str

    @property
    def acumulativa(self) -> bool:
        return es_acumulativa(self.magnitud)


def _por_fase(
    prefijo: str,
    etiqueta: str,
    magnitud: Magnitud,
    unidad: str,
    fases: tuple[Fase, ...] = (Fase.A, Fase.B, Fase.C),
) -> list[Medicion]:
    """Las variantes por fase de una misma magnitud."""
    return [
        Medicion(
            nombre=f"{prefijo}_phs{fase.value}",
            etiqueta=f"{etiqueta} fase {fase.value}",
            magnitud=magnitud,
            fase=fase,
            unidad=unidad,
        )
        for fase in fases
    ]


CATALOGO: tuple[Medicion, ...] = (
    # --- Tensión ---
    *_por_fase("PhV", "Tensión", Magnitud.TENSION, "V"),
    *_por_fase(
        "PPV",
        "Tensión entre fases",
        Magnitud.TENSION_COMPUESTA,
        "V",
        (Fase.AB, Fase.BC, Fase.CA),
    ),
    # --- Corriente ---
    *_por_fase("A", "Corriente", Magnitud.CORRIENTE, "A"),
    Medicion("A_neut", "Corriente de neutro", Magnitud.CORRIENTE, Fase.NEUTRO, "A"),
    # --- Potencia ---
    *_por_fase("W", "Potencia activa", Magnitud.POTENCIA_ACTIVA, "kW"),
    Medicion(
        "TotW", "Potencia activa total", Magnitud.POTENCIA_ACTIVA, Fase.TOTAL, "kW"
    ),
    *_por_fase("VAr", "Potencia reactiva", Magnitud.POTENCIA_REACTIVA, "kvar"),
    Medicion(
        "TotVAr",
        "Potencia reactiva total",
        Magnitud.POTENCIA_REACTIVA,
        Fase.TOTAL,
        "kvar",
    ),
    Medicion(
        "TotVA",
        "Potencia aparente total",
        Magnitud.POTENCIA_APARENTE,
        Fase.TOTAL,
        "kVA",
    ),
    # --- Factor de potencia: adimensional, por eso la unidad va vacía ---
    *_por_fase("PF", "Factor de potencia", Magnitud.FACTOR_POTENCIA, ""),
    Medicion(
        "TotPF", "Factor de potencia total", Magnitud.FACTOR_POTENCIA, Fase.TOTAL, ""
    ),
    # --- Entradas y salidas digitales ---
    # `GGIO.Ind` en IEC 61850: indicación genérica. Numeradas porque un relé
    # no tiene un significado universal — qué representa cada una lo sabe
    # quien cableó el tablero, y va en la descripción del equipo.
    *(
        Medicion(
            nombre=f"Ind{numero:02d}",
            etiqueta=f"Entrada digital {numero}",
            magnitud=Magnitud.ESTADO_DIGITAL,
            fase=Fase.TOTAL,
            unidad="",
        )
        for numero in range(1, 9)
    ),
    # --- Frecuencia ---
    Medicion("Hz", "Frecuencia", Magnitud.FRECUENCIA, Fase.TOTAL, "Hz"),
    # --- Energía: los contadores. Sin estos no hay costos ni reportes ---
    Medicion(
        "TotWh_import",
        "Energía activa importada",
        Magnitud.ENERGIA_IMPORTADA,
        Fase.TOTAL,
        "kWh",
    ),
    Medicion(
        "TotWh_export",
        "Energía activa exportada",
        Magnitud.ENERGIA_EXPORTADA,
        Fase.TOTAL,
        "kWh",
    ),
    Medicion(
        "TotVArh_import",
        "Energía reactiva importada",
        Magnitud.ENERGIA_REACTIVA_IMPORTADA,
        Fase.TOTAL,
        "kvarh",
    ),
    Medicion(
        "TotVArh_export",
        "Energía reactiva exportada",
        Magnitud.ENERGIA_REACTIVA_EXPORTADA,
        Fase.TOTAL,
        "kvarh",
    ),
)

POR_NOMBRE: dict[str, Medicion] = {medicion.nombre: medicion for medicion in CATALOGO}


def buscar(nombre: str) -> Medicion | None:
    """La entrada del catálogo con ese nombre, o None si no está."""
    return POR_NOMBRE.get(nombre)
