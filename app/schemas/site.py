"""Request and response models for sites."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SiteCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=150)
    direccion: str | None = Field(default=None, max_length=300)
    # IANA name; the firmware stamps its readings with it.
    timezone: str = Field(default="America/Bogota", max_length=64)
    ciudad: str | None = Field(default=None, max_length=120)
    responsable_nombre: str | None = Field(default=None, max_length=150)


class SiteUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=150)
    direccion: str | None = Field(default=None, max_length=300)
    timezone: str | None = Field(default=None, max_length=64)
    ciudad: str | None = Field(default=None, max_length=120)
    responsable_nombre: str | None = Field(default=None, max_length=150)


class SiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_id: uuid.UUID
    nombre: str
    direccion: str | None
    timezone: str
    ciudad: str | None
    responsable_nombre: str | None
    created_at: datetime
    updated_at: datetime
