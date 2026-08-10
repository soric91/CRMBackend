"""Equipment: a Modbus device reachable from a gateway."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.domain.enums import EquipmentType, ModbusTransport, SerialParity
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import enum_column

if TYPE_CHECKING:
    from app.models.gateway import Gateway
    from app.models.variable import Variable

# A device is identified on its bus by a different tuple depending on how it is
# reached, so uniqueness needs one partial index per transport. A single
# constraint over a nullable `puerto` would not protect TCP rows at all: most
# engines treat NULLs as distinct, so any number of them would collide silently.
_RTU_IS_UNIQUE_BY_PORT = Index(
    "uq_equipment_rtu",
    "gateway_id",
    "puerto",
    "modbus_id",
    unique=True,
    postgresql_where=text("transporte = 'rtu'"),
    sqlite_where=text("transporte = 'rtu'"),
)
# The device name titles a section of the firmware config file, so two devices
# of one gateway cannot share it.
_NAME_IS_UNIQUE_PER_GATEWAY = UniqueConstraint(
    "gateway_id", "nombre_dispositivo", name="uq_equipment_gateway_id_nombre"
)
_TCP_IS_UNIQUE_BY_HOST = Index(
    "uq_equipment_tcp",
    "gateway_id",
    "host",
    "puerto_tcp",
    "modbus_id",
    unique=True,
    postgresql_where=text("transporte = 'tcp'"),
    sqlite_where=text("transporte = 'tcp'"),
)


class Equipment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A meter, analyser, inverter or sensor polled over Modbus."""

    __tablename__ = "equipment"
    __table_args__ = (
        _RTU_IS_UNIQUE_BY_PORT,
        _TCP_IS_UNIQUE_BY_HOST,
        _NAME_IS_UNIQUE_PER_GATEWAY,
        CheckConstraint("modbus_id BETWEEN 1 AND 247", name="modbus_id_range"),
        # Solo los cuatro códigos de lectura. Escribir (5, 6, 15, 16) no tiene
        # sentido acá: el gateway lee medidores, no los opera.
        CheckConstraint(
            "modbus_function IN (1, 2, 3, 4)", name="modbus_function_is_a_read"
        ),
        # The serial parameters are absent on a TCP device, so every check has
        # to tolerate NULL or it would reject the whole transport.
        CheckConstraint("baudrate IS NULL OR baudrate > 0", name="baudrate_positive"),
        CheckConstraint("bits IS NULL OR bits IN (7, 8)", name="bits_valid"),
        CheckConstraint(
            "stop_bits IS NULL OR stop_bits IN (1, 2)", name="stop_bits_valid"
        ),
        CheckConstraint(
            "puerto_tcp IS NULL OR puerto_tcp BETWEEN 1 AND 65535",
            name="puerto_tcp_range",
        ),
        # Coherence lives in the database as well as in the schemas: a row that
        # claims one transport while carrying the other's fields is unreadable
        # by the firmware, whichever endpoint produced it.
        CheckConstraint(
            "(transporte = 'rtu' AND puerto IS NOT NULL AND baudrate IS NOT NULL"
            " AND host IS NULL AND puerto_tcp IS NULL)"
            " OR (transporte = 'tcp' AND host IS NOT NULL AND puerto IS NULL"
            " AND baudrate IS NULL)",
            name="transport_fields_coherent",
        ),
    )

    gateway_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gateways.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Titles the device's section in the firmware config and names its map
    # file, so it has to be unique within the gateway.
    nombre_dispositivo: Mapped[str] = mapped_column(String(80), nullable=False)
    # The firmware's own vocabulary, e.g. "CT_Meter". Deliberately free text
    # and separate from `tipo`: that one classifies the device for the panel,
    # this one is a value the gateway reads literally.
    device_type: Mapped[str] = mapped_column(String(60), nullable=False)
    marca: Mapped[str | None] = mapped_column(String(100))
    modelo: Mapped[str | None] = mapped_column(String(100))
    tipo: Mapped[EquipmentType] = mapped_column(
        enum_column(EquipmentType, "equipment_type"), nullable=False
    )

    # Unit id on the bus. Kept for both transports: over TCP it addresses the
    # slave behind a Modbus gateway, and it is not always 1.
    modbus_id: Mapped[int] = mapped_column(Integer, nullable=False)
    transporte: Mapped[ModbusTransport] = mapped_column(
        enum_column(ModbusTransport, "modbus_transport"),
        nullable=False,
        default=ModbusTransport.RTU,
        server_default=text("'rtu'"),
    )

    # --- Modbus RTU: set only when transporte == rtu ---
    puerto: Mapped[str | None] = mapped_column(String(60))
    baudrate: Mapped[int | None] = mapped_column(Integer)
    paridad: Mapped[SerialParity | None] = mapped_column(
        enum_column(SerialParity, "serial_parity")
    )
    bits: Mapped[int | None] = mapped_column(Integer)
    stop_bits: Mapped[int | None] = mapped_column(Integer)

    # --- Modbus TCP: set only when transporte == tcp ---
    host: Mapped[str | None] = mapped_column(String(255))
    puerto_tcp: Mapped[int | None] = mapped_column(Integer)

    # --- firmware switches, copied verbatim into its config file ---
    # El código de función Modbus con el que el firmware lee este equipo. Va
    # por equipo y no por variable: el firmware arma **una** petición de bloque
    # para todo el dispositivo, así que el código es del dispositivo.
    #
    # 3 —holding registers— es lo que usa casi todo medidor comercial, y por eso
    # es el valor por defecto: dejarlo nulo obligaría al firmware a adivinar.
    modbus_function: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    modbusconnect: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    modbusread: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    blockreading: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    gateway: Mapped["Gateway"] = relationship(back_populates="equipment", lazy="raise")
    variables: Mapped[list["Variable"]] = relationship(
        back_populates="equipment",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="Variable.nombre",
    )

    @property
    def endpoint(self) -> str:
        """Where this device is reached, for logs and conflict messages."""
        if self.transporte is ModbusTransport.TCP:
            return f"{self.host}:{self.puerto_tcp}"
        return str(self.puerto)

    def __repr__(self) -> str:
        return f"<Equipment {self.marca} {self.modelo} id={self.modbus_id}>"
