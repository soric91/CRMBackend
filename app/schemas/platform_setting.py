"""Request and response models for the shared gateway configuration."""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import SettingOrigin

# Un nombre de variable de entorno: mayúsculas, dígitos y guión bajo, sin
# empezar con dígito. Se valida porque el valor termina escrito en un archivo
# que un shell interpreta — `MQTT HOST` o `MQTT-HOST` no son asignables, y
# `PATH=…` en el lugar equivocado rompe el arranque del contenedor.
CLAVE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Nombres que no se pueden usar aunque tengan la forma correcta: cambiarlos
# desde el panel afectaría al proceso, no a la aplicación.
RESERVADAS = frozenset({"PATH", "HOME", "USER", "SHELL", "LD_PRELOAD", "PYTHONPATH"})


class PlatformSettingRead(BaseModel):
    """Un valor de configuración, con el secreto tapado."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    clave: str
    # `None` cuando es secreto: el listado nunca lo trae. Verlo es una acción
    # aparte, con su propia petición, para que quede registrada.
    valor: str | None
    es_secreto: bool
    # De dónde sale el valor. Solo `plataforma` se edita desde el panel; las
    # otras están para que su nombre viaje en la configuración del equipo.
    origen: SettingOrigin
    # True si hay algo guardado. Distingue "tapado" de "vacío" sin revelarlo —
    # y un secreto vacío es un gateway que no va a poder conectar.
    tiene_valor: bool
    descripcion: str
    updated_at: datetime


class PlatformSettingRevealed(BaseModel):
    """El valor en claro. Solo se devuelve pidiéndolo explícitamente."""

    clave: str
    valor: str


class PlatformSettingCreate(BaseModel):
    clave: str = Field(max_length=100)
    valor: str = Field(default="", max_length=4000)
    es_secreto: bool = False
    descripcion: str = Field(default="", max_length=300)

    @field_validator("clave")
    @classmethod
    def _forma_de_variable(cls, value: str) -> str:
        clave = value.strip()
        if not CLAVE.match(clave):
            raise ValueError(
                "El nombre debe ser MAYUSCULAS_CON_GUION_BAJO y empezar con letra"
            )
        if clave in RESERVADAS:
            raise ValueError(
                f"'{clave}' es una variable del sistema y no se puede usar"
            )
        return clave


class PlatformSettingUpdate(BaseModel):
    """Lo que se puede cambiar de una fila que ya existe.

    La clave no está: renombrarla es borrar una variable y crear otra, y como
    los gateways leen por nombre, hacerlo en un solo paso silencioso dejaría
    equipos buscando una variable que dejó de existir.
    """

    valor: str | None = Field(default=None, max_length=4000)
    es_secreto: bool | None = None
    descripcion: str | None = Field(default=None, max_length=300)
