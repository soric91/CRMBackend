"""The whole tree under a client, as one nested document.

Walking the hierarchy through the per-parent listings costs one request per
node, which is fine for a form and wasteful for anything that has to know the
shape of a client's installation up front. These models carry the same rows,
nested, so one request answers it.

Deliberately absent from :class:`GatewayFleet`: the credential fields and the
configuration switch. This document describes what exists, not what the
firmware is allowed to download — that lives in `/gateways/{id}/config-status`,
and secrets are never part of a listing.

A nested collection is ``None`` when the requested :class:`FleetLevel` stopped
above it, and ``[]`` when the level was reached and nothing is there. The
distinction matters to a consumer deciding whether to ask again more deeply.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.domain.enums import (
    ClientStatus,
    EquipmentType,
    GatewayStatus,
    ModbusDataType,
    ModbusRegisterType,
    ModbusTransport,
    RegisterNotation,
)


class VariableFleet(BaseModel):
    """One register read off a device."""

    id: uuid.UUID
    nombre: str
    registro_modbus: int
    notacion_registro: RegisterNotation
    # The address written in the base it was entered in, ready to compare
    # against a datasheet without converting.
    registro_display: str
    tipo_registro: ModbusRegisterType
    tipo_dato: ModbusDataType
    escala: Decimal
    unidad: str | None


class EquipmentFleet(BaseModel):
    """A Modbus device, identified as the firmware names it."""

    id: uuid.UUID
    nombre_dispositivo: str
    device_type: str
    tipo: EquipmentType
    marca: str | None
    modelo: str | None
    modbus_id: int
    transporte: ModbusTransport
    variables: list[VariableFleet] | None = None


class GatewayFleet(BaseModel):
    """A gateway, with the uuid its MQTT topics and its own API calls use."""

    id: uuid.UUID
    uuid: uuid.UUID
    numero_serie: str
    firmware_version: str | None
    estado: GatewayStatus
    ultima_conexion: datetime | None
    ip_actual: str | None
    intervalo_lectura_segundos: int
    hora_inicio: int
    hora_fin: int
    equipment: list[EquipmentFleet] | None = None


class SiteFleet(BaseModel):
    """A physical installation."""

    id: uuid.UUID
    nombre: str
    direccion: str | None
    timezone: str
    latitud: Decimal | None
    longitud: Decimal | None
    gateways: list[GatewayFleet] | None = None


class ClientFleet(BaseModel):
    """A client and everything installed under it."""

    id: uuid.UUID
    nombre_empresa: str
    estado: ClientStatus
    # Whether this client is allowed to look at its own consumption at all.
    puede_ver_consumo: bool
    sites: list[SiteFleet] | None = None
