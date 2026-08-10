"""What the firmware asks for, and the credential the CRM hands out.

The response is a neutral JSON, not an INI file: the CRM has no business
knowing the firmware's file format. It carries the firmware's own vocabulary —
struct characters, hex addresses, `gain` — so the gateway can lay it out
without translating anything.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import GatewayLogLevel


class GatewayCredentialRead(BaseModel):
    """State of a gateway's credential. Never carries the secret."""

    gateway_id: uuid.UUID
    uuid: uuid.UUID
    numero_serie: str
    tiene_credencial: bool
    credential_emitida_en: datetime | None
    config_habilitada: bool


class GatewayCredentialCreated(GatewayCredentialRead):
    """The only response that carries the secret, and only this once."""

    credential: str = Field(
        description="Shown once. Load it into the gateway's firmware."
    )


class GatewayTokenRequest(BaseModel):
    """What the firmware sends to exchange its credential for a token."""

    gateway_uuid: uuid.UUID
    credential: str


class GatewayTokenResponse(BaseModel):
    """A short-lived token the firmware renews by itself."""

    access_token: str
    token_type: str = "bearer"  # noqa: S105 - the OAuth scheme name
    expires_in: int = Field(description="Token lifetime in seconds")


# --- the configuration itself ------------------------------------------------


class VariableMapEntry(BaseModel):
    """One register, shaped exactly like the firmware's map files."""

    address: str = Field(description="Hex string, e.g. 0x2006")
    data_type: str = Field(description="struct character: f, h, H, i or I")
    gain: str
    # Not read by the firmware today; useful for whatever displays the reading.
    unit: str | None = None
    register_type: str


class DeviceConfig(BaseModel):
    """A device section, plus the map that would become its own file."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    identify_device: uuid.UUID
    device_type: str
    protocol: str

    # Serial, present only on an RTU device.
    serialport: str | None = None
    baudrate: int | None = None
    parity: str | None = None
    bytesize: int | None = None
    stopbits: int | None = None

    # Network, present only on a TCP device.
    host: str | None = None
    port: int | None = None

    device_id: int
    # El código de función con el que se lee el bloque: 3 holding, 4 input,
    # 1 coils, 2 discrete inputs.
    modbus_function: int
    modbusconnect: bool
    modbusread: bool
    blockreading: bool
    map: dict[str, VariableMapEntry]


class LogConfig(BaseModel):
    loglevel: GatewayLogLevel


class MainModbusConfig(BaseModel):
    interval: int
    start_hour: int
    stop_hour: int


class GatewayConfigAck(BaseModel):
    """What the gateway reports after writing its configuration to disk."""

    config_version: str = Field(
        min_length=1, max_length=64, description="The version it applied"
    )


class GatewayHeartbeat(BaseModel):
    """What a gateway reports when it checks in.

    Both fields are optional: the point of the call is the contact itself.
    They let the device correct what the CRM believes about it, which is more
    reliable than an operator typing a firmware version by hand.
    """

    firmware_version: str | None = Field(default=None, max_length=40)
    ip_actual: str | None = Field(default=None, max_length=45)


class GatewayHeartbeatAck(BaseModel):
    """Confirmation, plus what the device should do next."""

    gateway_uuid: uuid.UUID
    ultima_conexion: datetime
    # True when there is a configuration waiting to be downloaded, so a device
    # that only heartbeats still learns it has work to do.
    config_habilitada: bool
    config_version_actual: str


class GatewayConfigStatus(BaseModel):
    """What the panel needs to tell whether a device is up to date."""

    gateway_id: uuid.UUID
    uuid: uuid.UUID
    config_habilitada: bool
    # What would be delivered right now, switch or no switch.
    config_version_actual: str
    config_version_aplicada: str | None
    config_aplicada_en: datetime | None
    ultima_conexion: datetime | None
    # True when the device is running something other than what is configured.
    desactualizada: bool


class GatewayConfigResponse(BaseModel):
    """Everything a gateway needs to start reading."""

    gateway_uuid: uuid.UUID
    numero_serie: str
    firmware_version: str | None
    # When this document was assembled, so the gateway can tell one from another.
    generated_at: datetime
    # Fingerprint of everything below. The gateway stores it, sends it back as
    # `If-None-Match`, and only reapplies when it actually changed.
    config_version: str = ""
    log: LogConfig
    mainmodbus: MainModbusConfig
    devices: list[DeviceConfig]
