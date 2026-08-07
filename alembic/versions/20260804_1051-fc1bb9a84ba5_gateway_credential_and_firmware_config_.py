"""gateway credential and firmware config fields

Everything the gateway needs to authenticate and to be handed a configuration:

* `gateways` gains the credential it authenticates with (hash only), the switch
  that enables the configuration download, and the settings the firmware's
  `[DEFAULT]` and `[MAINMODBUS]` sections read.
* The polling cadence moves from `equipment` to `gateways`. The firmware walks
  every device of a gateway in one loop, so a value per device promised a
  granularity the hardware never had. Backfilled with the MIN of the gateway's
  devices, for the same reason the previous move used MIN: nobody loses
  resolution.
* `equipment` gains the name that titles its section in the config file, the
  firmware's own type vocabulary, and the three switches it reads verbatim.
* `float64` leaves `ModbusDataType`: the firmware's DATATYPE has no character
  for it, so it was an option that could never be read.

Revision ID: fc1bb9a84ba5
Revises: e607f6d5d8ad
Create Date: 2026-08-04 10:51:48.866311

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fc1bb9a84ba5"
down_revision: str | Sequence[str] | None = "e607f6d5d8ad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_INTERVAL = 60
DATA_TYPES_WITH_A_FIRMWARE_CHARACTER = "'int16', 'uint16', 'int32', 'uint32', 'float32'"
ALL_DATA_TYPES = f"{DATA_TYPES_WITH_A_FIRMWARE_CHARACTER}, 'float64'"


def upgrade() -> None:
    """Add the credential, the firmware settings, and move the cadence up."""
    # --- credential and the download switch ---------------------------------
    op.add_column(
        "gateways", sa.Column("credential_hash", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "gateways",
        sa.Column("credential_emitida_en", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "gateways",
        sa.Column(
            "config_habilitada",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # --- what the firmware config file needs, per gateway -------------------
    op.add_column(
        "gateways",
        sa.Column(
            "log_level", sa.String(length=8), nullable=False, server_default="INFO"
        ),
    )
    op.create_check_constraint(
        "gateway_log_level",
        "gateways",
        "log_level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')",
    )
    op.add_column(
        "gateways",
        sa.Column("intervalo_lectura_segundos", sa.Integer(), nullable=True),
    )
    # The cadence the devices already asked for, so nothing starts being read
    # more slowly than it was.
    op.execute(
        f"""
        UPDATE gateways SET intervalo_lectura_segundos = COALESCE(
            (SELECT MIN(e.frecuencia_lectura_segundos)
             FROM equipment e WHERE e.gateway_id = gateways.id),
            {DEFAULT_INTERVAL}
        )
        """
    )
    op.alter_column(
        "gateways",
        "intervalo_lectura_segundos",
        nullable=False,
        server_default=str(DEFAULT_INTERVAL),
    )
    op.add_column(
        "gateways",
        sa.Column("hora_inicio", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "gateways",
        sa.Column("hora_fin", sa.Integer(), nullable=False, server_default="23"),
    )
    for name, condition in (
        ("intervalo_lectura_positive", "intervalo_lectura_segundos > 0"),
        ("hora_inicio_range", "hora_inicio BETWEEN 0 AND 23"),
        ("hora_fin_range", "hora_fin BETWEEN 0 AND 23"),
        ("horas_ordered", "hora_fin >= hora_inicio"),
    ):
        op.create_check_constraint(name, "gateways", condition)

    op.drop_constraint("frecuencia_lectura_positive", "equipment", type_="check")
    op.drop_column("equipment", "frecuencia_lectura_segundos")

    # --- equipment: the firmware's own fields -------------------------------
    op.add_column(
        "equipment",
        sa.Column("nombre_dispositivo", sa.String(length=80), nullable=True),
    )
    # The firmware names the section and the map file after the model, as in
    # `Modbus_DTSU666`. Falling back to the unit id keeps it unique when the
    # model is unknown.
    op.execute(
        """
        UPDATE equipment
        SET nombre_dispositivo = CASE
            WHEN modelo IS NOT NULL AND modelo <> '' THEN 'Modbus_' || modelo
            ELSE 'Modbus_' || CAST(modbus_id AS TEXT)
        END
        """
    )
    op.alter_column("equipment", "nombre_dispositivo", nullable=False)
    op.create_unique_constraint(
        "uq_equipment_gateway_id_nombre",
        "equipment",
        ["gateway_id", "nombre_dispositivo"],
    )

    op.add_column(
        "equipment", sa.Column("device_type", sa.String(length=60), nullable=True)
    )
    # Every device registered so far is a current-transformer meter, which is
    # the vocabulary the running firmware already uses.
    op.execute("UPDATE equipment SET device_type = 'CT_Meter'")
    op.alter_column("equipment", "device_type", nullable=False)

    for switch in ("modbusconnect", "modbusread", "blockreading"):
        op.add_column(
            "equipment",
            sa.Column(
                switch, sa.Boolean(), nullable=False, server_default=sa.text("true")
            ),
        )

    # --- a data type the firmware cannot express ----------------------------
    op.drop_constraint("modbus_data_type", "variables", type_="check")
    op.create_check_constraint(
        "modbus_data_type",
        "variables",
        f"tipo_dato IN ({DATA_TYPES_WITH_A_FIRMWARE_CHARACTER})",
    )


def downgrade() -> None:
    """Put the cadence back on the devices and drop the firmware fields."""
    op.drop_constraint("modbus_data_type", "variables", type_="check")
    op.create_check_constraint(
        "modbus_data_type", "variables", f"tipo_dato IN ({ALL_DATA_TYPES})"
    )

    for switch in ("blockreading", "modbusread", "modbusconnect"):
        op.drop_column("equipment", switch)
    op.drop_column("equipment", "device_type")
    op.drop_constraint("uq_equipment_gateway_id_nombre", "equipment", type_="unique")
    op.drop_column("equipment", "nombre_dispositivo")

    op.add_column(
        "equipment",
        sa.Column("frecuencia_lectura_segundos", sa.Integer(), nullable=True),
    )
    op.execute(
        f"""
        UPDATE equipment SET frecuencia_lectura_segundos = COALESCE(
            (SELECT g.intervalo_lectura_segundos
             FROM gateways g WHERE g.id = equipment.gateway_id),
            {DEFAULT_INTERVAL}
        )
        """
    )
    op.alter_column(
        "equipment",
        "frecuencia_lectura_segundos",
        nullable=False,
        server_default=str(DEFAULT_INTERVAL),
    )
    op.create_check_constraint(
        "frecuencia_lectura_positive", "equipment", "frecuencia_lectura_segundos > 0"
    )

    for name in (
        "horas_ordered",
        "hora_fin_range",
        "hora_inicio_range",
        "intervalo_lectura_positive",
        "gateway_log_level",
    ):
        op.drop_constraint(name, "gateways", type_="check")
    for column in (
        "hora_fin",
        "hora_inicio",
        "intervalo_lectura_segundos",
        "log_level",
        "config_habilitada",
        "credential_emitida_en",
        "credential_hash",
    ):
        op.drop_column("gateways", column)
