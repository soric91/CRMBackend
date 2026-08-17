"""sede con generación propia

La analítica necesita saber si la sede tiene fotovoltaica inyectando: el
medidor de frontera es el mismo, pero con generación solo se ve el BALANCE
NETO y varios indicadores (carga base, curva de carga, detección de eventos)
solo valen en horas sin sol. Sin generación, todo lo que pasa por el medidor
es consumo y esos mismos cálculos valen las 24 h.

`tiene_generacion` queda NULLABLE y sin default a propósito: NULL significa
"nadie lo dijo, detéctalo a partir de la energía exportada". Ponerle `False`
a toda la tabla habría apagado exportación y balance neto en la sede que hoy
sí tiene solar.

Revision ID: a1c4f2b70d31
Revises: 72e2818d9d9d
Create Date: 2026-08-17 16:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c4f2b70d31"
down_revision: str | Sequence[str] | None = "72e2818d9d9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Agregar `tiene_generacion` (NULL = detectar) y `capacidad_kwp`."""
    op.add_column("sites", sa.Column("tiene_generacion", sa.Boolean(), nullable=True))
    op.add_column(
        "sites", sa.Column("capacidad_kwp", sa.Numeric(precision=8, scale=2), nullable=True)
    )
    op.create_check_constraint(
        "capacidad_kwp_positive", "sites", "capacidad_kwp IS NULL OR capacidad_kwp > 0"
    )


def downgrade() -> None:
    """Quitar ambas columnas. Se pierde lo que alguien haya marcado a mano;
    el modo vuelve a detectarse solo, que es el comportamiento anterior."""
    op.drop_constraint(op.f("ck_sites_capacidad_kwp_positive"), "sites", type_="check")
    op.drop_column("sites", "capacidad_kwp")
    op.drop_column("sites", "tiene_generacion")
