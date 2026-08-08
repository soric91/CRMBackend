"""configuración compartida de la flota de gateways

Los valores del `.env` que son iguales en todos los equipos pasan a vivir en
una tabla. Hoy se escriben a mano en cada instalación: cambiar el host del
broker significa visitar cada sede, y un valor tecleado mal no falla al
arrancar — falla más tarde y en silencio.

Se siembran las claves conocidas con sus valores no secretos, tomados del
`.env` que hay hoy en producción. **Los valores secretos quedan vacíos a
propósito**: la contraseña de MQTT y el token de InfluxDB del servidor se
cargan desde el panel. Escribirlos en una migración los dejaría en el
historial de git para siempre, que es exactamente lo contrario de lo que esta
tabla intenta hacer.

Un secreto vacío se ve en el panel como "sin valor", no como "tapado", así que
la carga pendiente queda visible en vez de parecer hecha.

Revision ID: a027b0a32398
Revises: 0a5e1dd
Create Date: 2026-08-07 17:08:00.000000

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a027b0a32398"
down_revision: str | Sequence[str] | None = "c4d18f6b2e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# clave, valor, es_secreto, descripción
#
# El valor de un secreto va vacío. No es un olvido: ver más arriba.
SEMILLA: tuple[tuple[str, str, bool, str], ...] = (
    # --- InfluxDB local del equipo ---
    ("INFLUXDB_ADMIN_USER", "admin", False, "Usuario administrador del InfluxDB local"),
    ("INFLUXDB_ORG", "gateway_ems", False, "Organización del InfluxDB local"),
    ("INFLUXDB_BUCKET", "modbus_data", False, "Bucket local de lecturas Modbus"),
    ("INFLUXDB_RETENTION", "30d", False, "Cuánto guarda el equipo antes de descartar"),
    # --- MQTT ---
    ("MQTT_USER", "gatewayems", False, "Usuario del broker"),
    ("MQTT_PASSWORD", "", True, "Contraseña del broker — cargar desde el panel"),
    ("MQTT_HOST", "mqtt.129-146-123-58.sslip.io", False, "Host del broker"),
    ("MQTT_PORT", "8883", False, "Puerto del broker. 8883 exige TLS"),
    ("MQTT_TOPIC_TLM", "gatewayems/modbus", False, "Tópico de telemetría"),
    ("MQTT_TOPIC_CRM", "crm/gateways", False, "Tópico de eventos hacia el CRM"),
    ("MQTT_QOS", "1", False, "Calidad de servicio de las publicaciones"),
    ("MQTT_ACTIVE", "true", False, "Si el equipo publica por MQTT"),
    ("MQTT_USE_TLS", "true", False, "Cifra la conexión con el broker."),
    # --- CRM ---
    ("CRM_API_URL", "", False, "URL de esta API. Usar https en producción"),
    ("CRM_HEARTBEAT_SECONDS", "60", False, "Cada cuánto el equipo reporta que vive"),
    ("CRM_HTTP_TIMEOUT", "30", False, "Timeout de las peticiones al CRM, en segundos"),
    # --- InfluxDB del servidor ---
    ("INFLUXDB_SERVER_ACTIVE", "true", False, "Si el equipo escribe en la base central"),
    ("INFLUXDB_SERVER_URL", "", False, "URL del InfluxDB central"),
    (
        "INFLUXDB_SERVER_TOKEN",
        "",
        True,
        "Token de escritura del InfluxDB central — cargar desde el panel",
    ),
    ("INFLUXDB_SERVER_ORG", "gateway_ems_server", False, "Organización del central"),
    ("INFLUXDB_SERVER_BUCKET", "telemetry_server", False, "Bucket del central"),
    (
        "INFLUXDB_SERVER_INTERVAL_MINUTES",
        "15",
        False,
        "Cada cuánto el equipo vuelca al central",
    ),
)


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clave", sa.String(length=100), nullable=False),
        sa.Column("valor", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "es_secreto", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "descripcion", sa.String(length=300), nullable=False, server_default=""
        ),
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
    )
    op.create_index(
        "ix_platform_settings_clave", "platform_settings", ["clave"], unique=True
    )

    tabla = sa.table(
        "platform_settings",
        sa.column("id", sa.Uuid),
        sa.column("clave", sa.String),
        sa.column("valor", sa.Text),
        sa.column("es_secreto", sa.Boolean),
        sa.column("descripcion", sa.String),
    )
    op.bulk_insert(
        tabla,
        [
            {
                "id": uuid.uuid4(),
                "clave": clave,
                "valor": valor,
                "es_secreto": es_secreto,
                "descripcion": descripcion,
            }
            for clave, valor, es_secreto, descripcion in SEMILLA
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_platform_settings_clave", table_name="platform_settings")
    op.drop_table("platform_settings")
