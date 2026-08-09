"""eliminar la tabla de alertas que nunca se usó

`alerts_config` venía del esquema inicial y nunca se conectó a nada: no tiene
repositorio, ni servicio, ni endpoint, ni aparece en CRMweb, y en la base está
vacía. Las alertas que sí existen las calcula ApiEMS por bandas de percentiles
sobre las lecturas — nunca leyó esta tabla.

Se elimina porque una tabla vacía que parece configuración invita a cargarla, y
lo que se cargue ahí no va a tener ningún efecto.

El autogenerado además traía cambios de `platform_settings` y
`service_accounts` que no son de este cambio: son diferencias entre el modelo y
la base que vienen de antes. Se sacaron a mano. Mezclarlas acá habría alterado
tablas en uso dentro de una migración que dice hacer otra cosa.

Revision ID: 407c5fe31c7e
Revises: 5ab728d9a6fb
Create Date: 2026-08-09 11:35:24.885828

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "407c5fe31c7e"
down_revision: str | Sequence[str] | None = "5ab728d9a6fb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Eliminar `alerts_config`."""
    op.drop_index(op.f("ix_alerts_config_gateway_id"), table_name="alerts_config")
    op.drop_index(op.f("ix_alerts_config_tipo"), table_name="alerts_config")
    op.drop_table("alerts_config")


def downgrade() -> None:
    """Recrearla vacía, con la misma forma que tenía.

    No se restauran filas porque no había ninguna. Si alguna vez se hubieran
    cargado, este downgrade devolvería la estructura pero no el contenido — y
    conviene saberlo antes de correrlo, no después.
    """
    op.create_table(
        "alerts_config",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("gateway_id", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column("tipo", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column(
            "umbral",
            sa.NUMERIC(precision=14, scale=4),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "canal_notif", sa.VARCHAR(length=8), autoincrement=False, nullable=False
        ),
        sa.Column("activo", sa.BOOLEAN(), autoincrement=False, nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "canal_notif::text = ANY (ARRAY['email'::character varying, "
            "'telegram'::character varying, 'whatsapp'::character varying]::text[])",
            name=op.f("ck_alerts_config_notification_channel"),
        ),
        sa.CheckConstraint(
            "tipo::text = ANY (ARRAY['desconexion'::character varying, "
            "'voltaje_fuera_rango'::character varying, "
            "'factor_potencia_bajo'::character varying, "
            "'consumo_excesivo'::character varying]::text[])",
            name=op.f("ck_alerts_config_alert_type"),
        ),
        sa.ForeignKeyConstraint(
            ["gateway_id"],
            ["gateways.id"],
            name=op.f("fk_alerts_config_gateway_id_gateways"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts_config")),
    )
    op.create_index(op.f("ix_alerts_config_tipo"), "alerts_config", ["tipo"], unique=False)
    op.create_index(
        op.f("ix_alerts_config_gateway_id"), "alerts_config", ["gateway_id"], unique=False
    )
