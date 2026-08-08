"""tokens de enrolamiento

El permiso de un solo uso con el que un gateway recibe su configuración. Es un
puntero, no un contenedor: el token que se entrega no lleva adentro a qué
equipo pertenece, así que uno filtrado no dice ni de qué instalación es.

`token_hash` es sha256 y no bcrypt, al revés que las contraseñas. El canje
llega con el token y hay que **encontrar** su fila, y bcrypt no se puede
indexar. Tampoco hace falta: bcrypt existe para frenar un diccionario contra
un secreto que alguien eligió, y esto son 32 bytes al azar.

`emitido_por` no tiene clave foránea a propósito. Es auditoría, y tiene que
sobrevivir a que la cuenta se borre — con `CASCADE` la evidencia desaparecería
junto con el usuario, y con `RESTRICT` no se podría dar de baja a nadie que
alguna vez haya emitido un token.

Revision ID: 18d113135b56
Revises: 6b6bd687707c
Create Date: 2026-08-07 20:51:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "18d113135b56"
down_revision: str | Sequence[str] | None = "6b6bd687707c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "enrollment_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("gateway_id", sa.Uuid(), nullable=False),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("usado_desde", sa.String(length=45), nullable=True),
        sa.Column("emitido_por", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["gateway_id"], ["gateways.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_enrollment_tokens_token_hash",
        "enrollment_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_enrollment_tokens_gateway_id", "enrollment_tokens", ["gateway_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_enrollment_tokens_gateway_id", table_name="enrollment_tokens")
    op.drop_index("ix_enrollment_tokens_token_hash", table_name="enrollment_tokens")
    op.drop_table("enrollment_tokens")
