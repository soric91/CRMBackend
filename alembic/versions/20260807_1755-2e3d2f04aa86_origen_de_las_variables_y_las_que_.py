"""de dónde sale cada variable, y las seis que faltaban

La semilla original cargó solo las variables cuyo valor vive en el CRM. El
`.env` real tiene seis más, y no todas por la misma razón:

* `INFLUXDB_TOKEN` y `INFLUXDB_ADMIN_PASSWORD` las genera el equipo al azar
  cuando se instala. Guardarlas acá las traería a la red sin que nadie más las
  necesite.
* `GATEWAY_UUID` y `GATEWAY_CREDENTIAL` salen de la ficha del gateway, que ya
  está en la tabla `gateways`. Copiarlas sería un segundo lugar afirmando lo
  mismo, y el día que discrepen no habría forma de saber cuál manda.
* `MQTT_CLIENT_ID` lo genera el equipo, igual que hoy lo hace
  `scripts/generate_device_env.py`. El CRM podría derivarlo del uuid y así los
  logs del broker dirían qué equipo es cada conexión, pero eso movería una
  decisión que hoy vive en el dispositivo — y la unicidad ya está resuelta.
* `INFLUXDB_URL` es `http://localhost:8086` en todos los equipos, así que es
  una constante como cualquier otra y se carga con su valor.

Las cinco primeras entran igual, vacías y con un `origen` que dice quién las
llena. El motivo es que el equipo tiene que poder pedir su configuración
completa: si el CRM le manda solo las líneas que conoce, el firmware necesita
mantener una segunda lista de qué más hace falta — y esa lista se desactualiza
la primera vez que alguien agrega una variable acá.

Revision ID: 2e3d2f04aa86
Revises: 5323a166585e
Create Date: 2026-08-07 17:55:00.000000

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2e3d2f04aa86"
down_revision: str | Sequence[str] | None = "5323a166585e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# clave, es_secreto, origen, descripción. Todas entran con el valor vacío.
FALTANTES: tuple[tuple[str, bool, str, str], ...] = (
    (
        "INFLUXDB_TOKEN",
        True,
        "equipo",
        "Token del InfluxDB local. Lo genera el equipo al instalarse.",
    ),
    (
        "INFLUXDB_ADMIN_PASSWORD",
        True,
        "equipo",
        "Contraseña del InfluxDB local. La genera el equipo al instalarse.",
    ),
    (
        "GATEWAY_UUID",
        False,
        "identidad",
        "Identificador del gateway. Sale de su ficha en el CRM.",
    ),
    (
        "GATEWAY_CREDENTIAL",
        True,
        "identidad",
        "Credencial con la que el gateway se autentica. Se emite desde su ficha.",
    ),
    (
        "MQTT_CLIENT_ID",
        False,
        "equipo",
        "Identificador de la conexión al broker. Lo genera el equipo. Único "
        "por equipo: dos iguales se desconectan mutuamente en bucle.",
    ),
)

# Esta sí tiene valor y se edita como cualquier otra: es la misma en todos los
# equipos porque InfluxDB corre en el propio gateway.
CONSTANTE = (
    "INFLUXDB_URL",
    "http://localhost:8086",
    "URL del InfluxDB local, dentro del propio equipo.",
)


def upgrade() -> None:
    op.add_column(
        "platform_settings",
        sa.Column(
            "origen",
            sa.String(length=20),
            nullable=False,
            server_default="plataforma",
        ),
    )

    tabla = sa.table(
        "platform_settings",
        sa.column("id", sa.Uuid),
        sa.column("clave", sa.String),
        sa.column("valor", sa.Text),
        sa.column("es_secreto", sa.Boolean),
        sa.column("origen", sa.String),
        sa.column("descripcion", sa.String),
    )

    filas = [
        {
            "id": uuid.uuid4(),
            "clave": clave,
            "valor": "",
            "es_secreto": es_secreto,
            "origen": origen,
            "descripcion": descripcion,
        }
        for clave, es_secreto, origen, descripcion in FALTANTES
    ]
    filas.append(
        {
            "id": uuid.uuid4(),
            "clave": CONSTANTE[0],
            "valor": CONSTANTE[1],
            "es_secreto": False,
            "origen": "plataforma",
            "descripcion": CONSTANTE[2],
        }
    )
    op.bulk_insert(tabla, filas)


def downgrade() -> None:
    tabla = sa.table("platform_settings", sa.column("clave", sa.String))
    claves = [clave for clave, _, _, _ in FALTANTES] + [CONSTANTE[0]]
    op.execute(tabla.delete().where(tabla.c.clave.in_(claves)))
    op.drop_column("platform_settings", "origen")
