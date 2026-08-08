"""MQTT_CLIENT_ID lo genera el equipo, no el CRM

La semilla que introdujo `origen` lo puso como `identidad`, o sea derivado del
uuid del gateway. Eso movía al CRM una decisión que hoy vive en el
dispositivo: `scripts/generate_device_env.py` genera ese valor localmente
junto con los dos secretos del InfluxDB local.

Derivarlo del uuid tenía una ventaja real —los logs del broker dirían qué
equipo es cada conexión, en vez de un identificador al azar— pero la unicidad
ya está resuelta del lado del equipo, y no vale mover una decisión de sitio
solo por eso.

La semilla quedó corregida, así que en una base nueva esta migración no
encuentra nada que cambiar. Está para las bases donde aquella ya corrió.

Revision ID: 5ab728d9a6fb
Revises: 18d113135b56
Create Date: 2026-08-08 10:34:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5ab728d9a6fb"
down_revision: str | Sequence[str] | None = "18d113135b56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _mover(desde: str, hacia: str) -> None:
    tabla = sa.table(
        "platform_settings",
        sa.column("clave", sa.String),
        sa.column("origen", sa.String),
    )
    # Solo si sigue como estaba: si alguien ya lo cambió a mano, no se le pisa.
    op.execute(
        tabla.update()
        .where(tabla.c.clave == "MQTT_CLIENT_ID")
        .where(tabla.c.origen == desde)
        .values(origen=hacia)
    )


def upgrade() -> None:
    _mover("identidad", "equipo")


def downgrade() -> None:
    _mover("equipo", "identidad")
