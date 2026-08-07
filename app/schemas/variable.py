"""Request and response models for equipment variables."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import ModbusDataType, ModbusRegisterType, RegisterNotation
from app.domain.firmware import as_firmware_address, parse_address


def _resolve_address(values: Any) -> Any:
    """Turn what the operator typed into the numeric address.

    A string is read in the base ``notacion_registro`` names, so `2006` with
    hex selected becomes 8198 — the register the datasheet actually points at.
    An integer is taken as the numeric address it already is.
    """
    if not isinstance(values, dict):
        return values
    raw = values.get("registro_modbus")
    if not isinstance(raw, str):
        return values

    notation = values.get("notacion_registro", RegisterNotation.DECIMAL)
    try:
        notation = RegisterNotation(notation)
    except ValueError as exc:
        raise ValueError(f"'{notation}' is not a register notation") from exc

    return {**values, "registro_modbus": parse_address(raw, notation)}


class VariableCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    # Accepts an integer, or a string read in `notacion_registro`'s base.
    registro_modbus: int = Field(ge=0)
    notacion_registro: RegisterNotation = RegisterNotation.DECIMAL
    tipo_registro: ModbusRegisterType = ModbusRegisterType.HOLDING
    tipo_dato: ModbusDataType = ModbusDataType.UINT16
    escala: Decimal = Decimal(1)
    unidad: str | None = Field(default=None, max_length=20)

    _parse_address = model_validator(mode="before")(_resolve_address)

    @field_validator("escala")
    @classmethod
    def _reject_zero_scale(cls, value: Decimal) -> Decimal:
        """A zero multiplier would turn every reading into zero."""
        if value == 0:
            raise ValueError("escala cannot be zero")
        return value


class VariableUpdate(BaseModel):
    """Partial update.

    A string address is read in the notation sent alongside it, so changing
    the base and the number together stays unambiguous.
    """

    nombre: str | None = Field(default=None, min_length=1, max_length=80)
    registro_modbus: int | None = Field(default=None, ge=0)
    notacion_registro: RegisterNotation | None = None
    tipo_registro: ModbusRegisterType | None = None
    tipo_dato: ModbusDataType | None = None
    escala: Decimal | None = None
    unidad: str | None = Field(default=None, max_length=20)

    _parse_address = model_validator(mode="before")(_resolve_address)

    @field_validator("escala")
    @classmethod
    def _reject_zero_scale(cls, value: Decimal | None) -> Decimal | None:
        if value == 0:
            raise ValueError("escala cannot be zero")
        return value


class VariableRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    equipment_id: uuid.UUID
    nombre: str
    registro_modbus: int
    notacion_registro: RegisterNotation
    # The address written in its own base, ready to display without converting.
    registro_display: str = ""
    tipo_registro: ModbusRegisterType
    tipo_dato: ModbusDataType
    escala: Decimal
    unidad: str | None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _render_address(self) -> Self:
        object.__setattr__(
            self,
            "registro_display",
            as_firmware_address(self.registro_modbus, self.notacion_registro),
        )
        return self
