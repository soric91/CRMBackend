"""Request and response models for gateways."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import GatewayLogLevel, GatewayStatus


class GatewayCreate(BaseModel):
    numero_serie: str = Field(min_length=1, max_length=80)
    firmware_version: str | None = Field(default=None, max_length=40)
    # Known at install time when the device has a fixed address. Left empty,
    # the gateway reports it itself on first connection.
    ip_actual: str | None = Field(default=None, max_length=45)
    # --- what the firmware's config file reads ---
    log_level: GatewayLogLevel = GatewayLogLevel.INFO
    # The polling cadence of the whole bus: the firmware walks its devices in
    # one loop, so this belongs to the gateway rather than to each device.
    intervalo_lectura_segundos: int = Field(default=60, gt=0)
    hora_inicio: int = Field(default=0, ge=0, le=23)
    hora_fin: int = Field(default=23, ge=0, le=23)
    # `estado` is deliberately absent: the device reports it, not the operator.


class GatewayUpdate(BaseModel):
    numero_serie: str | None = Field(default=None, min_length=1, max_length=80)
    firmware_version: str | None = Field(default=None, max_length=40)
    # `estado` is absent on purpose: it is derived from `ultima_conexion`, so
    # there is nothing for an operator to set.
    ip_actual: str | None = Field(default=None, max_length=45)
    # Whether the firmware may download its configuration at all.
    config_habilitada: bool | None = None
    log_level: GatewayLogLevel | None = None
    intervalo_lectura_segundos: int | None = Field(default=None, gt=0)
    hora_inicio: int | None = Field(default=None, ge=0, le=23)
    hora_fin: int | None = Field(default=None, ge=0, le=23)


class GatewayRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    site_id: uuid.UUID
    numero_serie: str
    uuid: uuid.UUID
    firmware_version: str | None
    ultima_conexion: datetime | None
    ip_actual: str | None
    estado: GatewayStatus
    config_habilitada: bool
    # What the device reported having applied. The current version and the
    # drift live in /gateways/{id}/config-status, which has to assemble the
    # document to know them.
    config_version_aplicada: str | None
    config_aplicada_en: datetime | None
    log_level: GatewayLogLevel
    intervalo_lectura_segundos: int
    hora_inicio: int
    hora_fin: int
    created_at: datetime
    updated_at: datetime
