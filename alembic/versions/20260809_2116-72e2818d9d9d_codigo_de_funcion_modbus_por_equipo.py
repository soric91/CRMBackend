"""codigo de funcion modbus por equipo

El firmware necesita saber con qué código de función leer cada dispositivo. Va
por equipo y no por variable: arma **una** petición de bloque para todo el
dispositivo, así que el código es del dispositivo.

Entra con `3` —holding registers— porque es lo que usa casi todo medidor
comercial y es lo que los equipos ya cargados están usando de hecho. Un valor
nulo obligaría al firmware a adivinar.

La restricción admite solo los cuatro códigos de lectura. Escribir (5, 6, 15,
16) no tiene sentido acá: el gateway lee medidores, no los opera.

Revision ID: 72e2818d9d9d
Revises: 92cffe50f77d
Create Date: 2026-08-09 21:16:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "72e2818d9d9d"
down_revision: str | Sequence[str] | None = "92cffe50f77d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Agregar `modbus_function`, con 3 para lo que ya está cargado."""
    op.add_column(
        "equipment",
        sa.Column(
            "modbus_function",
            sa.Integer(),
            nullable=False,
            server_default="3",
        ),
    )
    op.create_check_constraint(
        "modbus_function_is_a_read", "equipment", "modbus_function IN (1, 2, 3, 4)"
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_equipment_modbus_function_is_a_read"), "equipment", type_="check"
    )
    op.drop_column("equipment", "modbus_function")
