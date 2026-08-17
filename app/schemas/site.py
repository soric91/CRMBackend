"""Request and response models for sites."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# Whether the site has its own generation injecting into the grid. Left unset
# it means "detect it": the analytics side infers the mode from whether the
# meter ever exported. Writing it forces the mode.
_TIENE_GENERACION = Field(
    default=None,
    description=(
        "Si la sede tiene generación propia (fotovoltaica) inyectando a la red. "
        "Sin valor, el modo se detecta a partir de la energía exportada."
    ),
)
_CAPACIDAD_KWP = Field(
    default=None, gt=0, description="Potencia instalada del arreglo (kWp)"
)


class SiteCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=150)
    direccion: str | None = Field(default=None, max_length=300)
    # IANA name; the firmware stamps its readings with it.
    timezone: str = Field(default="America/Bogota", max_length=64)
    ciudad: str | None = Field(default=None, max_length=120)
    responsable_nombre: str | None = Field(default=None, max_length=150)
    tiene_generacion: bool | None = _TIENE_GENERACION
    capacidad_kwp: Decimal | None = _CAPACIDAD_KWP


class SiteUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=150)
    direccion: str | None = Field(default=None, max_length=300)
    timezone: str | None = Field(default=None, max_length=64)
    ciudad: str | None = Field(default=None, max_length=120)
    responsable_nombre: str | None = Field(default=None, max_length=150)
    tiene_generacion: bool | None = _TIENE_GENERACION
    capacidad_kwp: Decimal | None = _CAPACIDAD_KWP


class SiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_id: uuid.UUID
    nombre: str
    direccion: str | None
    timezone: str
    ciudad: str | None
    responsable_nombre: str | None
    tiene_generacion: bool | None
    capacidad_kwp: Decimal | None
    created_at: datetime
    updated_at: datetime
