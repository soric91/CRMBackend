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


# viejo -> nuevo. Explícito y corto a propósito: cada línea es una decisión
# revisable, no una regla que se aplique a lo que venga.
RENOMBRES: tuple[tuple[str, str], ...] = (
    ("Voltaje A", "PhV_phsA"),
    ("Corriente A", "A_phsA"),
    ("Potencia Activa Ints", "TotW"),
    ("PF", "TotPF"),
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
    """Volver a los nombres anteriores.

    Deja las variables otra vez sin magnitud ni unidad, que es el estado del
    que se venía.
    """
    _renombrar(tuple((nuevo, viejo) for viejo, nuevo in RENOMBRES))
