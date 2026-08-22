"""actualización remota del firmware: catálogo de versiones y ventana horaria

Hoy actualizar un gateway es viajar a la sede. Esto pone las dos mitades que
faltan para no hacerlo:

* **`firmware_releases`** — qué versiones existen, con su checksum. Publicar
  no es desplegar: se sube una versión, se prueba en un equipo, y recién
  después se le pide a la flota. Una versión mala se **retira**, no se borra,
  porque los equipos que la instalaron siguen apuntando a esta fila y esa es
  la única explicación de por qué una sede quedó como quedó.
* **Seis columnas en `gateways`** — a qué versión va cada equipo, desde qué
  momento puede aplicarla, y qué contestó. El estado lo reporta el equipo y no
  lo deduce el servidor: en el medio hay un reinicio, y el CRM no puede
  observar ese tramo.

La clave foránea es `RESTRICT` a propósito: mientras un equipo esté en camino
a una versión, esa versión no se puede borrar de abajo.

Las cinco filas nuevas de `platform_settings` viajan al `.env` de cada
gateway, que es como el firmware se entera de la ventana. `FIRMWARE_UPDATE_ACTIVO`
queda en **false**: la flota no empieza a actualizarse sola por haber corrido
una migración. Se enciende desde el panel, cuando alguien lo decide.

La hora es local a la sede —`sites.timezone` ya la tiene— así que no hay una
variable de zona horaria: las 03:00 son las 03:00 de cada planta, y una sola
hora global reiniciaría media flota a media tarde.

Revision ID: d3b7a91e5c24
Revises: a1c4f2b70d31
Create Date: 2026-08-21 10:15:00.000000

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3b7a91e5c24"
down_revision: str | Sequence[str] | None = "a1c4f2b70d31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ESTADOS = ("sin_pendiente", "programada", "descargando", "aplicando", "aplicada", "fallida")
CANALES = ("estable", "beta")

# clave, valor, descripción
NUEVAS: tuple[tuple[str, str, str], ...] = (
    (
        "FIRMWARE_UPDATE_ACTIVO",
        "false",
        "Si los equipos aceptan actualizarse solos. Apagado, el botón del "
        "panel no llega a ningún lado: es el freno de mano de la flota.",
    ),
    (
        "FIRMWARE_UPDATE_HORA",
        "03:00",
        "Hora local de la sede a partir de la cual se aplica una "
        "actualización programada, en formato HH:MM.",
    ),
    (
        "FIRMWARE_UPDATE_VENTANA_MINUTOS",
        "120",
        "Cuánto dura esa ventana. Pasada, el equipo no actualiza: espera a la "
        "siguiente en vez de reiniciar una planta en producción.",
    ),
    (
        "FIRMWARE_CHECK_SECONDS",
        "1800",
        "Cada cuánto el equipo pregunta si tiene una actualización pedida.",
    ),
    (
        "FIRMWARE_ROLLBACK_AUTO",
        "true",
        "Si el equipo vuelve solo a la versión anterior cuando el software "
        "nuevo no arranca.",
    ),
)


def upgrade() -> None:
    op.create_table(
        "firmware_releases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column(
            "canal", sa.String(length=20), nullable=False, server_default="beta"
        ),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("tamano_bytes", sa.Integer(), nullable=True),
        sa.Column("notas", sa.Text(), nullable=False, server_default=""),
        sa.Column("retirado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publicado_por", sa.Uuid(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_firmware_releases")),
        sa.UniqueConstraint("version", name=op.f("uq_firmware_releases_version")),
        sa.CheckConstraint(
            "length(sha256) = 64", name=op.f("ck_firmware_releases_sha256_length")
        ),
        sa.CheckConstraint(
            "tamano_bytes IS NULL OR tamano_bytes > 0",
            name=op.f("ck_firmware_releases_tamano_positive"),
        ),
        sa.CheckConstraint(
            f"canal IN ({', '.join(repr(canal) for canal in CANALES)})",
            name=op.f("ck_firmware_releases_firmware_channel"),
        ),
    )

    # --- a qué versión va cada equipo ---------------------------------------
    op.add_column(
        "gateways", sa.Column("firmware_objetivo_id", sa.Uuid(), nullable=True)
    )
    op.create_index(
        op.f("ix_gateways_firmware_objetivo_id"),
        "gateways",
        ["firmware_objetivo_id"],
    )
    op.create_foreign_key(
        op.f("fk_gateways_firmware_objetivo_id_firmware_releases"),
        "gateways",
        "firmware_releases",
        ["firmware_objetivo_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "gateways",
        sa.Column(
            "firmware_estado",
            sa.String(length=20),
            nullable=False,
            server_default="sin_pendiente",
        ),
    )
    op.create_check_constraint(
        "firmware_update_state",
        "gateways",
        f"firmware_estado IN ({', '.join(repr(estado) for estado in ESTADOS)})",
    )
    op.create_check_constraint(
        "firmware_target_present",
        "gateways",
        "firmware_estado = 'sin_pendiente' OR firmware_objetivo_id IS NOT NULL",
    )
    op.add_column(
        "gateways",
        sa.Column("firmware_aplicar_desde", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "gateways",
        sa.Column("firmware_version_anterior", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "gateways", sa.Column("firmware_error", sa.String(length=300), nullable=True)
    )
    op.add_column(
        "gateways",
        sa.Column(
            "firmware_intentos", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.create_check_constraint(
        "firmware_intentos_no_negative", "gateways", "firmware_intentos >= 0"
    )
    op.add_column(
        "gateways",
        sa.Column("firmware_reportado_en", sa.DateTime(timezone=True), nullable=True),
    )

    # --- lo que el equipo necesita saber para respetar la ventana -----------
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
    op.execute(
        "DELETE FROM platform_settings WHERE clave IN ("
        + ", ".join(repr(clave) for clave, _, _ in NUEVAS)
        + ")"
    )

    for nombre in (
        "firmware_intentos_no_negative",
        "firmware_target_present",
        "firmware_update_state",
    ):
        op.drop_constraint(op.f(f"ck_gateways_{nombre}"), "gateways", type_="check")
    op.drop_constraint(
        op.f("fk_gateways_firmware_objetivo_id_firmware_releases"),
        "gateways",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_gateways_firmware_objetivo_id"), table_name="gateways")

    for columna in (
        "firmware_reportado_en",
        "firmware_intentos",
        "firmware_error",
        "firmware_version_anterior",
        "firmware_aplicar_desde",
        "firmware_estado",
        "firmware_objetivo_id",
    ):
        op.drop_column("gateways", columna)

    op.drop_table("firmware_releases")
