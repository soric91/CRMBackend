"""Request and response models for equipment variables."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import ModbusDataType, ModbusRegisterType, RegisterNotation
from app.domain.firmware import as_firmware_address, parse_address
from app.domain.measurements import POR_NOMBRE, Fase, Magnitud


def _solo_del_catalogo(value: str) -> str:
    """Rechaza cualquier nombre que no esté en el catálogo.

    El mensaje nombra el problema y dónde ver las opciones, en vez de un
    "valor inválido" que obliga a adivinar.
    """
    if value not in POR_NOMBRE:
        raise ValueError(
            f"'{value}' no es una medición conocida. Las opciones están en "
            "GET /api/v1/variable-catalog"
        )
    return value


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
    # Del catálogo, no texto libre. Antes cada persona escribía el suyo
    # —`Voltaje A`, `VOLTAGE_A`, `V1`— y el panel quedaba sin datos sin que
    # nada fallara. Elegir de una lista hace imposible ese error.
    nombre: str
    # Accepts an integer, or a string read in `notacion_registro`'s base.
    registro_modbus: int = Field(ge=0)
    notacion_registro: RegisterNotation = RegisterNotation.DECIMAL
    tipo_registro: ModbusRegisterType = ModbusRegisterType.HOLDING
    tipo_dato: ModbusDataType = ModbusDataType.UINT16
    escala: Decimal = Decimal(1)
    # `unidad` no se pide: se deduce de qué se está midiendo.

    _parse_address = model_validator(mode="before")(_resolve_address)

    _check_nombre = field_validator("nombre")(_solo_del_catalogo)

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

    nombre: str | None = None
    registro_modbus: int | None = Field(default=None, ge=0)
    notacion_registro: RegisterNotation | None = None
    tipo_registro: ModbusRegisterType | None = None
    tipo_dato: ModbusDataType | None = None
    escala: Decimal | None = None

    _parse_address = model_validator(mode="before")(_resolve_address)

    @field_validator("nombre")
    @classmethod
    def _check_nombre(cls, value: str | None) -> str | None:
        return None if value is None else _solo_del_catalogo(value)

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
    # Derivadas del nombre vía el catálogo: no se guardan ni se piden.
    etiqueta: str | None
    unidad: str | None
    magnitud: Magnitud | None
    fase: Fase | None
    # Un contador solo admite difference()/last(); nunca promedios.
    acumulativa: bool
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


class MedicionRead(BaseModel):
    """Una entrada del catálogo, como la ve el panel."""

    model_config = ConfigDict(from_attributes=True)

    # Lo que se guarda como `nombre` de la variable, y lo que el gateway
    # publica por MQTT.
    nombre: str
    # Lo único pensado para leerse: "Tensión fase C".
    etiqueta: str
    # Con qué otras agrupar la tarjeta en el panel.
    magnitud: Magnitud
    fase: Fase
    unidad: str
    acumulativa: bool
