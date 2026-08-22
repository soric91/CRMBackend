"""Lo que el firmware pide, y lo que el panel decide.

Del lado del equipo hay dos mensajes y nada más: "¿tengo algo que instalar?"
y "esto es lo que pasó". Del lado del panel, publicar una versión y pedirle a
un grupo de equipos que la instalen — dos actos separados a propósito, porque
publicar no es desplegar.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import FirmwareChannel, FirmwareUpdateState
from app.domain.firmware_update import SHA256, VersionInvalidaError, parse_version

# Los estados que un equipo puede reportar. `sin_pendiente` y `programada`
# quedan afuera: son decisiones del CRM, y un equipo que pudiera declararse
# "sin pendiente" estaría cancelando su propia actualización.
REPORTABLES = frozenset(
    {
        FirmwareUpdateState.DESCARGANDO,
        FirmwareUpdateState.APLICANDO,
        FirmwareUpdateState.APLICADA,
        FirmwareUpdateState.FALLIDA,
    }
)


class FirmwareUpdateOrder(BaseModel):
    """La orden de instalación que baja el equipo.

    Lleva el checksum y no una firma de la dirección: el equipo verifica el
    paquete contra un valor que guarda el CRM, así que comprometer el
    servidor de paquetes no alcanza para que instale algo alterado.
    """

    version: str
    url: str = Field(description="De dónde bajar el .tar.gz")
    sha256: str = Field(description="Se verifica antes de descomprimir nada")
    tamano_bytes: int | None = None
    # Desde cuándo puede aplicarla, en UTC. El equipo compara contra su reloj.
    aplicar_desde: datetime
    # Cuánto dura la ventana. Pasada, no actualiza: espera a la siguiente.
    ventana_minutos: int
    # Si tiene que volver solo a la versión anterior cuando el software nuevo
    # no arranca.
    rollback_auto: bool
    # Cuántos intentos le quedan antes de que el CRM deje de ofrecerla.
    intentos_restantes: int
    notas: str = ""


class FirmwareUpdateAck(BaseModel):
    """Lo que el equipo cuenta sobre la actualización que tenía pedida."""

    estado: FirmwareUpdateState
    # De qué versión habla. Un acuse que no nombra la versión no se puede
    # distinguir de uno viejo que llegó tarde por una red lenta.
    version: str = Field(min_length=1, max_length=40)
    # Por qué falló, en sus palabras. Solo tiene sentido con `fallida`.
    error: str | None = Field(default=None, max_length=300)

    @field_validator("estado")
    @classmethod
    def _solo_lo_que_el_equipo_puede_reportar(
        cls, value: FirmwareUpdateState
    ) -> FirmwareUpdateState:
        if value not in REPORTABLES:
            raise ValueError(
                f"'{value.value}' lo decide el CRM, no el equipo: "
                f"reportar {sorted(estado.value for estado in REPORTABLES)}"
            )
        return value


class FirmwareUpdateStatus(BaseModel):
    """En qué quedó la actualización de un equipo, después de un acuse."""

    gateway_uuid: uuid.UUID
    estado: FirmwareUpdateState
    version_objetivo: str | None
    version_actual: str | None
    aplicar_desde: datetime | None
    intentos: int
    intentos_restantes: int
    error: str | None
    reportado_en: datetime | None


# --- lo que decide el panel --------------------------------------------------


class FirmwareReleaseCreate(BaseModel):
    """Publicar una versión en el catálogo.

    No la despliega en ningún equipo: la deja disponible para que alguien
    después decida a quién pedírsela.
    """

    version: str = Field(max_length=40)
    canal: FirmwareChannel = FirmwareChannel.BETA
    sha256: str = Field(max_length=64)
    tamano_bytes: int | None = Field(default=None, gt=0)
    notas: str = Field(default="", max_length=2000)

    @field_validator("version")
    @classmethod
    def _comparable(cls, value: str) -> str:
        """Una versión que no se puede comparar no se puede desplegar: no
        habría forma de saber si un equipo ya la tiene."""
        texto = value.strip()
        try:
            parse_version(texto)
        except VersionInvalidaError as invalida:
            raise ValueError(str(invalida)) from invalida
        return texto

    @field_validator("sha256")
    @classmethod
    def _un_checksum_de_verdad(cls, value: str) -> str:
        texto = value.strip().lower()
        if not SHA256.match(texto):
            raise ValueError(
                "El checksum tiene que ser un sha256 en hexadecimal, "
                "64 caracteres"
            )
        return texto


class FirmwareReleaseRead(BaseModel):
    """Una versión del catálogo, como la ve el panel."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: str
    canal: FirmwareChannel
    sha256: str
    tamano_bytes: int | None
    notas: str
    retirado_en: datetime | None
    publicado_por: uuid.UUID | None
    created_at: datetime
    # Cuántos equipos la tienen pedida ahora mismo. Se mira antes de
    # retirarla: retirar una a la que van tres equipos los deja sin nada.
    gateways_apuntando: int = 0


class RolloutCreate(BaseModel):
    """A quién pedirle una versión, y desde cuándo.

    El destino es uno solo de los tres. Aceptar dos a la vez obligaría a
    inventar qué gana, y el error se vería recién cuando media flota se
    reiniciara.
    """

    release_id: uuid.UUID
    gateway_ids: list[uuid.UUID] | None = None
    site_id: uuid.UUID | None = None
    client_id: uuid.UUID | None = None
    # `False` aplica en la próxima ventana; `True`, apenas el equipo pregunte.
    # Lo segundo es para una sede que está parada y un técnico que espera.
    ahora: bool = False

    @model_validator(mode="after")
    def _un_solo_destino(self) -> "RolloutCreate":
        elegidos = [
            campo
            for campo in (self.gateway_ids, self.site_id, self.client_id)
            if campo is not None
        ]
        if len(elegidos) != 1:
            raise ValueError(
                "Elegir exactamente un destino: gateway_ids, site_id o client_id"
            )
        if self.gateway_ids is not None and not self.gateway_ids:
            raise ValueError("La lista de gateways está vacía")
        return self


class RolloutProgramado(BaseModel):
    """Un equipo que quedó con la actualización pedida."""

    gateway_id: uuid.UUID
    numero_serie: str
    version_anterior: str | None
    aplicar_desde: datetime
    # True cuando la versión pedida es anterior a la que corre. No lo impide
    # —volver atrás es cómo se sale de una versión mala— pero se avisa.
    descenso: bool


class RolloutOmitido(BaseModel):
    """Un equipo al que no se le pidió nada, y por qué."""

    gateway_id: uuid.UUID
    numero_serie: str
    motivo: str


class RolloutResult(BaseModel):
    """Qué pasó con cada equipo del destino elegido."""

    version: str
    # False cuando `FIRMWARE_UPDATE_ACTIVO` está apagado: la orden queda
    # escrita, pero ningún equipo va a bajarla hasta que se encienda. Se
    # informa para que el panel lo diga en vez de dejarlo en silencio.
    flota_activa: bool
    programados: list[RolloutProgramado]
    omitidos: list[RolloutOmitido]
