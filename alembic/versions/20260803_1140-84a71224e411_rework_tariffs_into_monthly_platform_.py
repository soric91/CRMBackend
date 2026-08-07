"""rework tariffs into monthly platform-wide prices

Tariffs stop being per-client date ranges and become one row per month, valid
for the whole platform: `mes`, `valor_importado`, `valor_excedente`.

The table is recreated rather than altered column by column. Autogenerate
proposed a sequence of add/drop column operations, which had two defects:
adding a NOT NULL column with no default breaks on a non-empty table, and
Alembic does not detect CHECK constraints at all, so the new ones would never
have been created. The old shape never held data.

Revision ID: 84a71224e411
Revises: ed0fe9fa4ba2
Create Date: 2026-08-03 11:40:52.527049

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "84a71224e411"
down_revision: str | Sequence[str] | None = "ed0fe9fa4ba2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace the per-client tariff periods with monthly platform prices."""
    op.drop_table("tariffs")
    op.create_table(
        "tariffs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mes", sa.Date(), nullable=False),
        sa.Column("valor_importado", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("valor_excedente", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "EXTRACT(day FROM mes) = 1", name=op.f("ck_tariffs_mes_is_first_day")
        ),
        sa.CheckConstraint(
            "valor_importado >= 0", name=op.f("ck_tariffs_valor_importado_non_negative")
        ),
        sa.CheckConstraint(
            "valor_excedente >= 0", name=op.f("ck_tariffs_valor_excedente_non_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tariffs")),
        sa.UniqueConstraint("mes", name=op.f("uq_tariffs_mes")),
    )


def downgrade() -> None:
    """Restore the per-client tariff periods."""
    op.drop_table("tariffs")
    op.create_table(
        "tariffs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("cu_cop_kwh", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column(
            "cargo_fijo_mensual", sa.Numeric(precision=14, scale=2), nullable=False
        ),
        sa.Column(
            "tasa_credito_excedente", sa.Numeric(precision=14, scale=4), nullable=True
        ),
        sa.Column("vigente_desde", sa.Date(), nullable=False),
        sa.Column("vigente_hasta", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cargo_fijo_mensual >= 0", name=op.f("ck_tariffs_cargo_fijo_non_negative")
        ),
        sa.CheckConstraint("cu_cop_kwh >= 0", name=op.f("ck_tariffs_cu_non_negative")),
        sa.CheckConstraint(
            "vigente_hasta IS NULL OR vigente_hasta > vigente_desde",
            name=op.f("ck_tariffs_vigencia_order"),
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name=op.f("fk_tariffs_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tariffs")),
    )
    op.create_index(op.f("ix_tariffs_client_id"), "tariffs", ["client_id"])
    op.create_index(op.f("ix_tariffs_vigente_desde"), "tariffs", ["vigente_desde"])
