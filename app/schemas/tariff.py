"""Request and response models for monthly energy prices."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _first_of_month(value: date) -> date:
    """Snap a date to the first day of its month.

    `mes` means a month; the day is an artefact of representing it as a date.
    Normalising here rather than rejecting means no consumer — the panel,
    ApiEMS, a script — has to know the convention to use the API.
    """
    return value.replace(day=1)


class TariffCreate(BaseModel):
    """Payload for registering the prices of one month."""

    mes: date
    valor_importado: Decimal = Field(ge=0)
    valor_excedente: Decimal = Field(default=Decimal(0), ge=0)

    @field_validator("mes")
    @classmethod
    def _normalize_month(cls, value: date) -> date:
        return _first_of_month(value)


class TariffUpdate(BaseModel):
    """Partial update of the prices.

    `mes` is deliberately absent. Moving a tariff to another period rewrites
    costs that were already computed, and a cost from last year has to stay
    reproducible. A row filed under the wrong month is deleted and reloaded.
    """

    valor_importado: Decimal | None = Field(default=None, ge=0)
    valor_excedente: Decimal | None = Field(default=None, ge=0)


class TariffRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mes: date
    # Rendered in Spanish, e.g. "enero 2026". The panel can build its own label
    # from `mes`; this is here so a server-to-server consumer does not have to.
    periodo: str
    valor_importado: Decimal
    valor_excedente: Decimal
    created_at: datetime
    updated_at: datetime
