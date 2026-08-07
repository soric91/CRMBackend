"""renombrar las variables cargadas antes del catálogo

Las cuatro variables del medidor de prueba se cargaron cuando `nombre` era
texto libre. Quedaron con nombres que el catálogo no conoce, así que su
magnitud, fase y unidad salían en `null`: el sistema guardaba la lectura pero
no sabía qué era.

Esto NO es una conversión automática de nombres arbitrarios. Es una lista de
cuatro decisiones que ya se tomaron:

    Voltaje A            -> PhV_phsA   tensión fase A
    Corriente A          -> A_phsA     corriente fase A
    Potencia Activa Ints -> TotW       potencia activa total
    PF                   -> TotPF      factor de potencia total

Las dos primeras son traducción directa. Las dos últimas fueron una decisión:
en un medidor monofásico la potencia de la fase A y la total dan el mismo
número, así que `TotW` y `W_phsA` son ambos plausibles. Se eligió el total
porque describe al equipo completo, y esa distinción recién importa el día
que se conecte un trifásico — momento en el que un `W_phsA` mal puesto se
sumaría a los totales sin que nada avise.

**Después de aplicar esto hay que reconfigurar el gateway** para que publique
`PhV_phsA` en vez de `Voltaje_A`. El CRM sirve la configuración del firmware
a partir de estos nombres: si solo se mueve un lado, el panel busca un nombre
y el equipo manda otro, y vuelven los cero puntos.

El histórico ya escrito en InfluxDB conserva los nombres viejos. No se
renombra desde acá: son otra base, y con pocos días de datos de prueba lo más
simple es que la serie arranque de nuevo con el nombre correcto.

Revision ID: c4d18f6b2e57
Revises: b7c2e9a41f83
Create Date: 2026-08-07 10:12:33.771904

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d18f6b2e57"
down_revision: str | Sequence[str] | None = "b7c2e9a41f83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# viejo -> nuevo. Explícito y enumerado a propósito: cada línea es una
# traducción revisable, no una regla que se aplique a lo que venga.
#
# Cubre el juego completo que un medidor trifásico suele exponer, no solo las
# cuatro del equipo de prueba. Un nombre que no esté cargado simplemente no
# coincide con ninguna fila y el UPDATE no toca nada — así la misma migración
# sirve para cualquier base sin tener que saber de antemano qué tiene cada una.
#
# Se aceptan las grafías que ya aparecieron en la práctica: con y sin espacio,
# con y sin acento, y las abreviaturas de uso común.
RENOMBRES: tuple[tuple[str, str], ...] = (
    # --- Tensión ---
    ("Voltaje A", "PhV_phsA"),
    ("Voltaje B", "PhV_phsB"),
    ("Voltaje C", "PhV_phsC"),
    ("Voltaje_A", "PhV_phsA"),
    ("Voltaje_B", "PhV_phsB"),
    ("Voltaje_C", "PhV_phsC"),
    ("Tension A", "PhV_phsA"),
    ("Tension B", "PhV_phsB"),
    ("Tension C", "PhV_phsC"),
    # --- Corriente ---
    ("Corriente A", "A_phsA"),
    ("Corriente B", "A_phsB"),
    ("Corriente C", "A_phsC"),
    ("Corriente_A", "A_phsA"),
    ("Corriente_B", "A_phsB"),
    ("Corriente_C", "A_phsC"),
    # --- Potencia activa ---
    # `Ints` era la instantánea del equipo monofásico: su total.
    ("Potencia Activa Ints", "TotW"),
    ("Potencia Activa Total", "TotW"),
    ("Potencia Activa A", "W_phsA"),
    ("Potencia Activa B", "W_phsB"),
    ("Potencia Activa C", "W_phsC"),
    ("Potencia_Activa_A", "W_phsA"),
    ("Potencia_Activa_B", "W_phsB"),
    ("Potencia_Activa_C", "W_phsC"),
    # --- Potencia reactiva ---
    ("Potencia Reactiva Total", "TotVAr"),
    ("Potencia Reactiva A", "VAr_phsA"),
    ("Potencia Reactiva B", "VAr_phsB"),
    ("Potencia Reactiva C", "VAr_phsC"),
    ("Reactiva Total", "TotVAr"),
    ("Reactiva A", "VAr_phsA"),
    ("Reactiva B", "VAr_phsB"),
    ("Reactiva C", "VAr_phsC"),
    # --- Potencia aparente ---
    ("Potencia Aparente Total", "TotVA"),
    # --- Factor de potencia ---
    ("PF", "TotPF"),
    ("FP", "TotPF"),
    ("Factor Potencia Total", "TotPF"),
    ("FP A", "PF_phsA"),
    ("FP B", "PF_phsB"),
    ("FP C", "PF_phsC"),
    ("Factor Potencia A", "PF_phsA"),
    ("Factor Potencia B", "PF_phsB"),
    ("Factor Potencia C", "PF_phsC"),
    # --- Frecuencia ---
    ("Frecuencia", "Hz"),
    # --- Energía: los contadores ---
    ("Energia Importada", "TotWh_import"),
    ("Energia Importada Total", "TotWh_import"),
    ("Energia Activa Importada", "TotWh_import"),
    ("Energia Exportada", "TotWh_export"),
    ("Energia Exportada Total", "TotWh_export"),
    ("Energia Activa Exportada", "TotWh_export"),
    ("Energia Reactiva Importada", "TotVArh_import"),
    ("Energia Reactiva Exportada", "TotVArh_export"),
)


def _renombrar(pares: tuple[tuple[str, str], ...]) -> None:
    """Aplicar los renombrados uno por uno.

    Nombre exacto, no patrón: una variable que no esté en la lista se queda
    como está. Si el destino ya existiera en ese mismo equipo, la restricción
    de unicidad (equipment_id, nombre) hace fallar la migración — que es lo
    correcto: significa que hay dos filas para la misma medición y eso lo
    tiene que resolver una persona, no un UPDATE.
    """
    variables = sa.table(
        "variables", sa.column("nombre", sa.String), sa.column("id", sa.Uuid)
    )
    for viejo, nuevo in pares:
        op.execute(
            variables.update().where(variables.c.nombre == viejo).values(nombre=nuevo)
        )


def upgrade() -> None:
    """Pasar los nombres viejos a los del catálogo."""
    _renombrar(RENOMBRES)


def downgrade() -> None:
    """No revierte los nombres, y no puede.

    El mapeo es de muchos a uno: `Voltaje A`, `Voltaje_A` y `Tension A` van
    todas a `PhV_phsA`. Invertirlo obligaría a elegir una de las tres para
    cada fila, y la elección sería inventada — no queda registro de cuál era.

    Escribir un downgrade que devuelva "alguno" de los nombres viejos sería
    peor que no tener ninguno: parecería reversible y dejaría los datos en un
    estado que nunca existió. Si hay que volver atrás, se restaura la copia
    de la base anterior a la migración.
    """
    raise NotImplementedError(
        "Renombrado no reversible: varias grafías viejas comparten un mismo "
        "nombre nuevo. Restaurar desde una copia de la base."
    )
