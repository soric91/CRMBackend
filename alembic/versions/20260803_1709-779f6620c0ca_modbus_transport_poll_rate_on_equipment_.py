"""modbus transport, poll rate on equipment, register type on variable

Four contract changes requested by the frontend, in one revision because they
touch overlapping columns of the same two tables:

1. `gateways.ip_actual` becomes writable at creation — schema only, no DDL.
2. `frecuencia_lectura_segundos` moves from `variables` to `equipment`. The
   firmware polls a slave with a single cadence, so one value per device is
   what the hardware can actually honour. Backfilled with the MIN of the
   device's variables: with MAX or AVG a variable that asked for 10 s would
   silently start being read less often.
3. `tipo_registro` moves from `equipment` to `variables`, and
   `direccion_inicial` is dropped. One analyser exposes measurements as
   holding or input registers and relay states as coils, so the address space
   belongs to the reading, not the device.
4. Equipment gains `transporte` (rtu | tcp) plus the network fields, and the
   serial fields become nullable.

`direccion_inicial` is dropped without folding it into `registro_modbus`. In
the live data both columns held the same value (2000), so treating it as an
offset would have doubled every address to 4000 — an address nobody
configured. It was the base of the block, recorded twice.

Revision ID: 779f6620c0ca
Revises: 82d676485a08
Create Date: 2026-08-03 17:09:37.343169

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "779f6620c0ca"
down_revision: str | Sequence[str] | None = "82d676485a08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constraint names are passed bare: the metadata naming convention for checks
# is "ck_%(table_name)s_%(constraint_name)s", so handing it the full name would
# produce "ck_equipment_ck_equipment_...".
DEFAULT_POLL_SECONDS = 60
DEFAULT_REGISTER_TYPE = "holding"


def upgrade() -> None:
    """Move the two columns and add the TCP transport."""
    # --- 2. poll rate: variables -> equipment --------------------------------
    op.add_column(
        "equipment",
        sa.Column("frecuencia_lectura_segundos", sa.Integer(), nullable=True),
    )
    op.execute(
        f"""
        UPDATE equipment SET frecuencia_lectura_segundos = COALESCE(
            (SELECT MIN(v.frecuencia_lectura_segundos)
             FROM variables v WHERE v.equipment_id = equipment.id),
            {DEFAULT_POLL_SECONDS}
        )
        """
    )
    op.alter_column(
        "equipment",
        "frecuencia_lectura_segundos",
        nullable=False,
        server_default=str(DEFAULT_POLL_SECONDS),
    )
    op.create_check_constraint(
        "frecuencia_lectura_positive",
        "equipment",
        "frecuencia_lectura_segundos > 0",
    )
    op.drop_constraint("frecuencia_lectura_positive", "variables", type_="check")
    op.drop_column("variables", "frecuencia_lectura_segundos")

    # --- 3. register type: equipment -> variables ----------------------------
    op.add_column(
        "variables", sa.Column("tipo_registro", sa.String(length=8), nullable=True)
    )
    op.execute(
        """
        UPDATE variables SET tipo_registro = (
            SELECT e.tipo_registro FROM equipment e WHERE e.id = variables.equipment_id
        )
        """
    )
    op.execute(
        f"UPDATE variables SET tipo_registro = '{DEFAULT_REGISTER_TYPE}'"
        " WHERE tipo_registro IS NULL"
    )
    op.alter_column(
        "variables",
        "tipo_registro",
        nullable=False,
        server_default=DEFAULT_REGISTER_TYPE,
    )
    op.create_check_constraint(
        "modbus_register_type",
        "variables",
        "tipo_registro IN ('holding', 'input', 'coil', 'discrete')",
    )

    op.drop_constraint("modbus_register_type", "equipment", type_="check")
    op.drop_column("equipment", "tipo_registro")
    op.drop_constraint("direccion_inicial_non_negative", "equipment", type_="check")
    op.drop_column("equipment", "direccion_inicial")

    # --- 4. RTU or TCP -------------------------------------------------------
    # Everything registered so far is serial, so 'rtu' is a safe backfill.
    op.add_column(
        "equipment",
        sa.Column(
            "transporte", sa.String(length=3), nullable=False, server_default="rtu"
        ),
    )
    op.create_check_constraint(
        "modbus_transport", "equipment", "transporte IN ('rtu', 'tcp')"
    )
    op.add_column("equipment", sa.Column("host", sa.String(length=255), nullable=True))
    op.add_column("equipment", sa.Column("puerto_tcp", sa.Integer(), nullable=True))

    # The serial parameters no longer apply to every row.
    for column in ("puerto", "baudrate", "paridad", "bits", "stop_bits"):
        op.alter_column("equipment", column, nullable=True)

    # The old checks assumed the columns were always present.
    for name, condition in (
        ("baudrate_positive", "baudrate IS NULL OR baudrate > 0"),
        ("bits_valid", "bits IS NULL OR bits IN (7, 8)"),
        ("stop_bits_valid", "stop_bits IS NULL OR stop_bits IN (1, 2)"),
    ):
        op.drop_constraint(name, "equipment", type_="check")
        op.create_check_constraint(name, "equipment", condition)

    op.create_check_constraint(
        "puerto_tcp_range",
        "equipment",
        "puerto_tcp IS NULL OR puerto_tcp BETWEEN 1 AND 65535",
    )
    op.create_check_constraint(
        "transport_fields_coherent",
        "equipment",
        "(transporte = 'rtu' AND puerto IS NOT NULL AND baudrate IS NOT NULL"
        " AND host IS NULL AND puerto_tcp IS NULL)"
        " OR (transporte = 'tcp' AND host IS NOT NULL AND puerto IS NULL"
        " AND baudrate IS NULL)",
    )

    # A single constraint over a nullable `puerto` would not protect TCP rows:
    # most engines treat NULLs as distinct, so they would never collide.
    op.drop_constraint("uq_equipment_gateway_puerto_id", "equipment", type_="unique")
    op.create_index(
        "uq_equipment_rtu",
        "equipment",
        ["gateway_id", "puerto", "modbus_id"],
        unique=True,
        postgresql_where=sa.text("transporte = 'rtu'"),
        sqlite_where=sa.text("transporte = 'rtu'"),
    )
    op.create_index(
        "uq_equipment_tcp",
        "equipment",
        ["gateway_id", "host", "puerto_tcp", "modbus_id"],
        unique=True,
        postgresql_where=sa.text("transporte = 'tcp'"),
        sqlite_where=sa.text("transporte = 'tcp'"),
    )


def downgrade() -> None:
    """Put both columns back where they were and drop the TCP transport.

    TCP devices cannot survive a downgrade: the old shape has nowhere to record
    a host. They are deleted rather than silently turned into serial devices
    pointing at a port that does not exist.
    """
    op.execute("DELETE FROM equipment WHERE transporte = 'tcp'")

    op.drop_index("uq_equipment_tcp", table_name="equipment")
    op.drop_index("uq_equipment_rtu", table_name="equipment")
    op.create_unique_constraint(
        "uq_equipment_gateway_puerto_id",
        "equipment",
        ["gateway_id", "puerto", "modbus_id"],
    )

    op.drop_constraint("transport_fields_coherent", "equipment", type_="check")
    op.drop_constraint("puerto_tcp_range", "equipment", type_="check")
    for name, condition in (
        ("baudrate_positive", "baudrate > 0"),
        ("bits_valid", "bits IN (7, 8)"),
        ("stop_bits_valid", "stop_bits IN (1, 2)"),
    ):
        op.drop_constraint(name, "equipment", type_="check")
        op.create_check_constraint(name, "equipment", condition)

    for column in ("puerto", "baudrate", "paridad", "bits", "stop_bits"):
        op.alter_column("equipment", column, nullable=False)

    op.drop_column("equipment", "puerto_tcp")
    op.drop_column("equipment", "host")
    op.drop_constraint("modbus_transport", "equipment", type_="check")
    op.drop_column("equipment", "transporte")

    # --- register type back on equipment ------------------------------------
    op.add_column(
        "equipment",
        sa.Column(
            "direccion_inicial", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.create_check_constraint(
        "direccion_inicial_non_negative",
        "equipment",
        "direccion_inicial >= 0",
    )
    op.add_column(
        "equipment",
        sa.Column(
            "tipo_registro",
            sa.String(length=8),
            nullable=False,
            server_default=DEFAULT_REGISTER_TYPE,
        ),
    )
    # One value per device again: the old shape could not express more than one,
    # so the device keeps whichever its variables agree on first.
    op.execute(
        """
        UPDATE equipment SET tipo_registro = COALESCE(
            (SELECT MIN(v.tipo_registro)
             FROM variables v WHERE v.equipment_id = equipment.id),
            'holding'
        )
        """
    )
    op.create_check_constraint(
        "modbus_register_type",
        "equipment",
        "tipo_registro IN ('holding', 'input', 'coil', 'discrete')",
    )
    op.drop_constraint("modbus_register_type", "variables", type_="check")
    op.drop_column("variables", "tipo_registro")

    # --- poll rate back on variables ----------------------------------------
    op.add_column(
        "variables",
        sa.Column("frecuencia_lectura_segundos", sa.Integer(), nullable=True),
    )
    op.execute(
        f"""
        UPDATE variables SET frecuencia_lectura_segundos = COALESCE(
            (SELECT e.frecuencia_lectura_segundos
             FROM equipment e WHERE e.id = variables.equipment_id),
            {DEFAULT_POLL_SECONDS}
        )
        """
    )
    op.alter_column(
        "variables",
        "frecuencia_lectura_segundos",
        nullable=False,
        server_default=str(DEFAULT_POLL_SECONDS),
    )
    op.create_check_constraint(
        "frecuencia_lectura_positive",
        "variables",
        "frecuencia_lectura_segundos > 0",
    )
    op.drop_constraint("frecuencia_lectura_positive", "equipment", type_="check")
    op.drop_column("equipment", "frecuencia_lectura_segundos")
