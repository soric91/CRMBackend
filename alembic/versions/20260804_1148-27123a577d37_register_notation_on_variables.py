"""register notation on variables

Records the base a register address was read in, so the firmware is handed the
address in the same shape as the datasheet it came from. Until now every
address went out as hex, which turned a decimal 2000 into `0x07D0` — a
different register than the operator meant.

Existing rows default to `decimal`, which is the literal truth of what is
stored. Any address that was actually read in hex has to be re-entered from
the panel, choosing that notation.

Revision ID: 27123a577d37
Revises: fc1bb9a84ba5
Create Date: 2026-08-04 11:48:26.841291

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "27123a577d37"
down_revision: str | Sequence[str] | None = "fc1bb9a84ba5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the notation, defaulting to what the stored number literally is."""
    op.add_column(
        "variables",
        sa.Column(
            "notacion_registro",
            sa.Enum(
                "decimal",
                "hex",
                name="register_notation",
                native_enum=False,
                create_constraint=True,
            ),
            server_default=sa.text("'decimal'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Drop the notation. Every address goes back to being written as hex."""
    op.drop_column("variables", "notacion_registro")
