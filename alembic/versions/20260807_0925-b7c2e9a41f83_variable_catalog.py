"""drop unidad: la unidad se deduce de qué se mide

`nombre` pasó a salir de un catálogo cerrado (app/domain/measurements.py), y
cada entrada trae su unidad. Guardarla además en la fila era una segunda
verdad que nada mantenía al día — el mismo problema que ya corregimos con el
`estado` del gateway.

También es lo que producía `kw` contra `kW`: una unidad tecleada a mano
admite tantas grafías como personas la escriban. Al derivarla, el caso deja
de existir.

Las filas anteriores al catálogo conservan su `nombre` viejo; para ellas
`magnitud`, `fase` y `unidad` son `None` hasta que se renombren. Ese
renombrado es tarea de quien administra los datos, no de esta migración: son
decisiones sobre qué mide cada registro, y adivinarlas en silencio es
exactamente cómo se llega a datos sucios que nadie audita.

Revision ID: b7c2e9a41f83
Revises: a3f81c40de27
Create Date: 2026-08-07 09:25:11.402318

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c2e9a41f83"
down_revision: str | Sequence[str] | None = "a3f81c40de27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Soltar la columna. Su valor pasa a derivarse del nombre."""
    op.drop_column("variables", "unidad")


def downgrade() -> None:
    """Restaurar la columna, vacía.

    Los valores anteriores no se recuperan, y tampoco hacen falta: eran
    copias de lo que el catálogo ya sabe.
    """
    op.add_column("variables", sa.Column("unidad", sa.String(length=20), nullable=True))
