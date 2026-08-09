"""sede con ciudad en vez de coordenadas

Latitud y longitud pedían un dato que nadie tiene a mano al dar de alta una
sede. Quedaban vacías casi siempre, así que no servían ni para ubicarla ni
para dibujarla en un mapa. Con la ciudad y la dirección escritas al menos se
puede llegar.

**Se pierden las coordenadas cargadas.** El `downgrade` devuelve las columnas
pero no sus valores: no hay de dónde sacarlos. Antes de correr esto conviene
anotarlas si le sirven a alguien.

Revision ID: 92cffe50f77d
Revises: 407c5fe31c7e
Create Date: 2026-08-09 12:17:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "92cffe50f77d"
down_revision: str | Sequence[str] | None = "407c5fe31c7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Agregar `ciudad`, quitar las coordenadas y sus restricciones."""
    op.add_column("sites", sa.Column("ciudad", sa.String(length=120), nullable=True))
    # Las restricciones van antes que las columnas: dejar un CHECK apuntando a
    # una columna que ya no existe falla, y el orden inverso no avisa hasta que
    # Postgres intenta resolverlo.
    op.drop_constraint(op.f("ck_sites_latitud_range"), "sites", type_="check")
    op.drop_constraint(op.f("ck_sites_longitud_range"), "sites", type_="check")
    op.drop_column("sites", "latitud")
    op.drop_column("sites", "longitud")


def downgrade() -> None:
    """Devolver las columnas, vacías.

    Los valores no vuelven. Si alguna sede tenía coordenadas cargadas, se
    perdieron en el `upgrade` y hay que volver a escribirlas a mano.
    """
    op.add_column("sites", sa.Column("latitud", sa.Numeric(precision=8, scale=6), nullable=True))
    op.add_column(
        "sites", sa.Column("longitud", sa.Numeric(precision=9, scale=6), nullable=True)
    )
    op.create_check_constraint(
        "latitud_range", "sites", "latitud IS NULL OR (latitud >= -90 AND latitud <= 90)"
    )
    op.create_check_constraint(
        "longitud_range",
        "sites",
        "longitud IS NULL OR (longitud >= -180 AND longitud <= 180)",
    )
    op.drop_column("sites", "ciudad")
