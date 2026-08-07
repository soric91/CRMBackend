"""derive gateway status from last contact

`estado` was a column an operator typed and nothing ever updated, so the panel
showed whatever was set at installation forever. Connectivity is observed, not
recorded: it now comes from `ultima_conexion`, which the gateway refreshes on
every heartbeat and every configuration fetch.

Dropping the column removes the second source of truth. Nothing is lost that
was true — the stored value had no relationship to whether the device was
actually reachable.

Revision ID: 35772f9d1938
Revises: 7a18ddebba93
Create Date: 2026-08-05 10:21:08.289210

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "35772f9d1938"
down_revision: str | Sequence[str] | None = "7a18ddebba93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the stored flag; reachability comes from `ultima_conexion` now."""
    op.drop_index(op.f("ix_gateways_estado"), table_name="gateways")
    op.drop_column("gateways", "estado")
    # The fleet view filters on this column now, so it gets an index.
    op.create_index(
        op.f("ix_gateways_ultima_conexion"), "gateways", ["ultima_conexion"]
    )


def downgrade() -> None:
    """Restore the column, defaulting everything to offline.

    The previous values cannot be recovered, and they were meaningless anyway.
    """
    op.drop_index(op.f("ix_gateways_ultima_conexion"), table_name="gateways")
    op.add_column(
        "gateways",
        sa.Column(
            "estado", sa.VARCHAR(length=7), nullable=False, server_default="offline"
        ),
    )
    op.create_check_constraint(
        "gateway_status", "gateways", "estado IN ('online', 'offline')"
    )
    op.create_index(op.f("ix_gateways_estado"), "gateways", ["estado"], unique=False)
