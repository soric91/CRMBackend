"""gateway applied config version

Records which configuration the device reported having applied, and when.
Compared against the version the CRM would serve now, it says whether an edit
is still undelivered — which matters because acknowledging a configuration
turns the download switch off, so later changes wait for somebody to turn it
back on.

Revision ID: 7a18ddebba93
Revises: 27123a577d37
Create Date: 2026-08-04 12:30:09.003341

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a18ddebba93"
down_revision: str | Sequence[str] | None = "27123a577d37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the applied-version columns. Both null until a device reports in."""
    op.add_column(
        "gateways",
        sa.Column("config_version_aplicada", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "gateways",
        sa.Column("config_aplicada_en", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Forget what each device reported."""
    op.drop_column("gateways", "config_aplicada_en")
    op.drop_column("gateways", "config_version_aplicada")
