"""service accounts for machine-to-machine consumers

`ApiEMS` reads tariffs and the fleet from this API. Until now the only way in
was a person's login, so another system held a human password: a credential
that opens the panel, carries a role able to write, and belongs to somebody
who will eventually leave the company.

This table gives a machine its own identity. The credential is split — a
public `credencial_id` that travels in the request and a hashed secret that
never leaves the database — so the token endpoint can look the row up by index
instead of comparing a hash against every row. What it may read is an explicit
list, and there is no value in that list that grants writing.

Revision ID: a3f81c40de27
Revises: 35772f9d1938
Create Date: 2026-08-06 09:12:44.118207

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f81c40de27"
down_revision: str | Sequence[str] | None = "35772f9d1938"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the table. Nothing existing is touched."""
    op.create_table(
        "service_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("descripcion", sa.String(length=300), nullable=True),
        sa.Column("credencial_id", sa.String(length=60), nullable=False),
        sa.Column("secret_hash", sa.String(length=255), nullable=False),
        sa.Column("secret_emitido_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("permisos", sa.JSON(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=True),
        sa.Column("activo", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_uso_en", sa.DateTime(timezone=True), nullable=True),
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
        # A credential pinned to one client dies with it: it existed to serve
        # that company and has nothing left to read once the company is gone.
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name=op.f("fk_service_accounts_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_accounts")),
        sa.UniqueConstraint("nombre", name=op.f("uq_service_accounts_nombre")),
        sa.UniqueConstraint(
            "credencial_id", name=op.f("uq_service_accounts_credencial_id")
        ),
    )
    # The lookup key of the token endpoint, which is the only route that reads
    # this table often.
    op.create_index(
        op.f("ix_service_accounts_credencial_id"),
        "service_accounts",
        ["credencial_id"],
    )
    op.create_index(
        op.f("ix_service_accounts_client_id"), "service_accounts", ["client_id"]
    )


def downgrade() -> None:
    """Drop the table. Every issued credential stops working."""
    op.drop_index(op.f("ix_service_accounts_client_id"), table_name="service_accounts")
    op.drop_index(
        op.f("ix_service_accounts_credencial_id"), table_name="service_accounts"
    )
    op.drop_table("service_accounts")
