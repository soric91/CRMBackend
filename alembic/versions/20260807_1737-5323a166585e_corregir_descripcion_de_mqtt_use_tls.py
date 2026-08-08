"""corregir la descripción de MQTT_USE_TLS

La semilla original copió un comentario que estaba suelto en el `.env` de
producción —"Exige MQTT_PORT=8883 y que MQTT_HOST sea el nombre del
certificado"— tomándolo por la descripción de la variable. Era una nota al
margen de quien escribió el archivo, no una explicación de qué hace.

Hace falta una migración y no basta con arreglar la semilla: la migración que
sembró la tabla ya corrió, y Alembic no la vuelve a aplicar. Cambiar la
semilla solo sirve para una base nueva.

Solo toca la fila si todavía tiene el texto viejo. Si alguien ya la editó a
mano, esta migración no le pisa el cambio.

Revision ID: 5323a166585e
Revises: a027b0a32398
Create Date: 2026-08-07 17:37:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5323a166585e"
down_revision: str | Sequence[str] | None = "a027b0a32398"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIEJA = "Exige MQTT_PORT=8883 y que MQTT_HOST sea el nombre del certificado"
NUEVA = "Cifra la conexión con el broker."


def _reemplazar(desde: str, hacia: str) -> None:
    tabla = sa.table(
        "platform_settings",
        sa.column("clave", sa.String),
        sa.column("descripcion", sa.String),
    )
    op.execute(
        tabla.update()
        .where(tabla.c.clave == "MQTT_USE_TLS")
        .where(tabla.c.descripcion == desde)
        .values(descripcion=hacia)
    )


def upgrade() -> None:
    _reemplazar(VIEJA, NUEVA)


def downgrade() -> None:
    _reemplazar(NUEVA, VIEJA)
