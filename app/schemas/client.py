"""Request and response models for clients."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.domain.enums import ClientStatus


class ClientCreate(BaseModel):
    """Payload for creating a client."""

    nombre_empresa: str = Field(min_length=1, max_length=200)
    contacto_nombre: str | None = Field(default=None, max_length=150)
    contacto_email: EmailStr | None = None
    contacto_telefono: str | None = Field(default=None, max_length=40)
    plan_contratado: str | None = Field(default=None, max_length=80)
    estado: ClientStatus = ClientStatus.PROSPECTO
    fecha_alta: date | None = None
    puede_ver_consumo: bool = False


class ClientUpdate(BaseModel):
    """Partial update. Only the fields present are written."""

    nombre_empresa: str | None = Field(default=None, min_length=1, max_length=200)
    contacto_nombre: str | None = Field(default=None, max_length=150)
    contacto_email: EmailStr | None = None
    contacto_telefono: str | None = Field(default=None, max_length=40)
    plan_contratado: str | None = Field(default=None, max_length=80)
    estado: ClientStatus | None = None
    puede_ver_consumo: bool | None = None


class ClientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nombre_empresa: str
    contacto_nombre: str | None
    contacto_email: str | None
    contacto_telefono: str | None
    plan_contratado: str | None
    estado: ClientStatus
    fecha_alta: date
    puede_ver_consumo: bool
    created_at: datetime
    updated_at: datetime
