"""scope v1: consumption-page flag on clients, drop the gateway access token

Two changes that belong to the same scope decision:

* `clients.puede_ver_consumo` gates whether a client's users may open their
  energy consumption page. It defaults to false so an unfinished client cannot
  see half-populated readings; existing rows get false too.
* `gateways.token_acceso_hash` goes away. It existed only so the firmware could
  authenticate when downloading its configuration, and v1 serves no firmware
  configuration. Reintroducing it later is one column, not a redesign.

Revision ID: 82d676485a08
Revises: 84a71224e411
Create Date: 2026-08-03 13:07:02.559671

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "82d676485a08"
down_revision: str | Sequence[str] | None = "84a71224e411"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the consumption flag and remove the gateway credential."""
    op.add_column(
        "clients",
        sa.Column(
            "puede_ver_consumo",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.drop_column("gateways", "token_acceso_hash")


def downgrade() -> None:
    """Restore the gateway credential column and drop the consumption flag.

    The column comes back empty: the hashes it held cannot be recovered, and
    every gateway would need a freshly issued token anyway.
    """
    op.add_column(
        "gateways", sa.Column("token_acceso_hash", sa.String(length=255), nullable=True)
    )
    op.drop_column("clients", "puede_ver_consumo")
