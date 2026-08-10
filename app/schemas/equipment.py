"""Request and response models for Modbus equipment."""

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import EquipmentType, ModbusTransport, SerialParity
from app.domain.modbus import TransportFieldError, normalize_transport_fields

# Modbus RTU reserves 0 for broadcast and stops at 247.
MIN_MODBUS_ID = 1
MAX_MODBUS_ID = 247

MIN_TCP_PORT = 1
MAX_TCP_PORT = 65535


class EquipmentCreate(BaseModel):
    """Payload for registering a device.

    Which connection fields apply depends on ``transporte``: serial ones for
    RTU, network ones for TCP. Sending the wrong set is rejected rather than
    ignored.
    """

    tipo: EquipmentType
    modbus_id: int = Field(ge=MIN_MODBUS_ID, le=MAX_MODBUS_ID)
    # Solo códigos de lectura: el gateway lee medidores, no los opera.
    modbus_function: Literal[1, 2, 3, 4] = 3
    transporte: ModbusTransport = ModbusTransport.RTU
    # Titles the device's section in the firmware config and names its map
    # file, so it has to be unique within the gateway.
    nombre_dispositivo: str = Field(min_length=1, max_length=80)
    # The firmware's own vocabulary, e.g. "CT_Meter". Free text on purpose:
    # `tipo` classifies the device for the panel, this one the gateway reads.
    device_type: str = Field(min_length=1, max_length=60)
    marca: str | None = Field(default=None, max_length=100)
    modelo: str | None = Field(default=None, max_length=100)
    modbusconnect: bool = True
    modbusread: bool = True
    blockreading: bool = True

    # --- Modbus RTU ---
    puerto: str | None = Field(default=None, max_length=60)
    baudrate: int | None = Field(default=None, gt=0)
    paridad: SerialParity | None = None
    bits: int | None = Field(default=None, ge=7, le=8)
    stop_bits: int | None = Field(default=None, ge=1, le=2)

    # --- Modbus TCP ---
    host: str | None = Field(default=None, max_length=255)
    puerto_tcp: int | None = Field(default=None, ge=MIN_TCP_PORT, le=MAX_TCP_PORT)

    @model_validator(mode="after")
    def _apply_transport_rules(self) -> Self:
        """Fill in the defaults of the chosen transport and reject the other's."""
        try:
            normalized = normalize_transport_fields(
                self.transporte,
                {
                    **self.model_dump(),
                    # RTU gets its defaults here rather than per field, so a
                    # TCP payload is not silently handed a serial port.
                    "puerto": self.puerto,
                },
            )
        except TransportFieldError as exc:
            raise ValueError(str(exc)) from exc

        for field in (
            "puerto",
            "baudrate",
            "paridad",
            "bits",
            "stop_bits",
            "host",
            "puerto_tcp",
        ):
            object.__setattr__(self, field, normalized[field])
        return self


class EquipmentUpdate(BaseModel):
    """Partial update.

    Coherence between ``transporte`` and the connection fields is checked by
    the service against the row's resulting state: a PATCH that only flips the
    transport still has to leave a usable row, and the stored values are not
    visible from here.
    """

    tipo: EquipmentType | None = None
    modbus_id: int | None = Field(default=None, ge=MIN_MODBUS_ID, le=MAX_MODBUS_ID)
    modbus_function: Literal[1, 2, 3, 4] | None = None
    transporte: ModbusTransport | None = None
    nombre_dispositivo: str | None = Field(default=None, min_length=1, max_length=80)
    device_type: str | None = Field(default=None, min_length=1, max_length=60)
    marca: str | None = Field(default=None, max_length=100)
    modelo: str | None = Field(default=None, max_length=100)
    modbusconnect: bool | None = None
    modbusread: bool | None = None
    blockreading: bool | None = None

    puerto: str | None = Field(default=None, max_length=60)
    baudrate: int | None = Field(default=None, gt=0)
    paridad: SerialParity | None = None
    bits: int | None = Field(default=None, ge=7, le=8)
    stop_bits: int | None = Field(default=None, ge=1, le=2)

    host: str | None = Field(default=None, max_length=255)
    puerto_tcp: int | None = Field(default=None, ge=MIN_TCP_PORT, le=MAX_TCP_PORT)


class EquipmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    gateway_id: uuid.UUID
    marca: str | None
    modelo: str | None
    tipo: EquipmentType
    modbus_id: int
    modbus_function: int
    transporte: ModbusTransport
    nombre_dispositivo: str
    device_type: str
    modbusconnect: bool
    modbusread: bool
    blockreading: bool
    puerto: str | None
    baudrate: int | None
    paridad: SerialParity | None
    bits: int | None
    stop_bits: int | None
    host: str | None
    puerto_tcp: int | None
    created_at: datetime
    updated_at: datetime
