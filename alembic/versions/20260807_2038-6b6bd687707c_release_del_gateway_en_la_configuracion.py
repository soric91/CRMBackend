"""qué software instala un gateway nuevo

Cuatro filas más en `platform_settings`: de dónde baja el instalador, de dónde
baja el paquete, qué versión, y con qué checksum verificarla.

Van a la base y no al código porque **qué versión reciben los equipos nuevos es
una decisión, no una consecuencia de haber publicado**. Se publica `v0.0.2`, se
prueba en un equipo, y recién entonces se edita `GATEWAY_RELEASE_VERSION` desde
el panel. Volver atrás es editarla de vuelta, sin republicar nada.

El checksum vive acá y no solo junto al `.tar.gz` a propósito: son dos
servidores distintos, así que comprometer el de releases no alcanza para que un
equipo instale un paquete alterado — compara contra un valor que ese servidor
no controla.

La versión y el checksum quedan **vacíos**. `v0.0.1` fue un tag de prueba para
verificar la publicación, no un release. Cargar uno de prueba acá haría que el
primer equipo enrolado instalara eso.

Revision ID: 6b6bd687707c
Revises: 2e3d2f04aa86
Create Date: 2026-08-07 20:38:00.000000

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6b6bd687707c"
down_revision: str | Sequence[str] | None = "2e3d2f04aa86"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BASE = "https://ems.129-146-123-58.sslip.io"

# clave, valor, descripción
NUEVAS: tuple[tuple[str, str, str], ...] = (
    (
        "GATEWAY_INSTALLER_URL",
        f"{BASE}/install.sh",
        "De dónde se baja el instalador. El panel arma con esto el comando "
        "que se le pasa al técnico.",
    ),
    (
        "GATEWAY_RELEASE_BASE_URL",
        f"{BASE}/rel",
        "Dónde viven los paquetes. El nombre del archivo se arma con la versión.",
    ),
    (
        "GATEWAY_RELEASE_VERSION",
        "",
        "Qué versión instala un equipo nuevo. Se cambia después de probarla, "
        "no al publicarla.",
    ),
    (
        "GATEWAY_RELEASE_SHA256",
        "",
        "Checksum de esa versión. El equipo lo verifica antes de descomprimir.",
    ),
)


def upgrade() -> None:
    tabla = sa.table(
        "platform_settings",
        sa.column("id", sa.Uuid),
        sa.column("clave", sa.String),
        sa.column("valor", sa.Text),
        sa.column("es_secreto", sa.Boolean),
        sa.column("origen", sa.String),
        sa.column("descripcion", sa.String),
    )
    op.bulk_insert(
        tabla,
        [
            {
                "id": uuid.uuid4(),
                "clave": clave,
                "valor": valor,
                "es_secreto": False,
                "origen": "plataforma",
                "descripcion": descripcion,
            }
            for clave, valor, descripcion in NUEVAS
        ],
    )


def downgrade() -> None:
    tabla = sa.table("platform_settings", sa.column("clave", sa.String))
    op.execute(tabla.delete().where(tabla.c.clave.in_([c for c, _, _ in NUEVAS])))
