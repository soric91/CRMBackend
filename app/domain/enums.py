"""Business enumerations.

Plain ``StrEnum`` values with no dependency on SQLAlchemy, Pydantic or FastAPI:
the persistence and API layers both import them from here, so the accepted
values are defined exactly once.
"""

from enum import StrEnum


class ClientStatus(StrEnum):
    """Commercial state of a client."""

    ACTIVO = "activo"
    SUSPENDIDO = "suspendido"
    PROSPECTO = "prospecto"


class GatewayStatus(StrEnum):
    """Last known connectivity of a gateway."""

    ONLINE = "online"
    OFFLINE = "offline"


class EquipmentType(StrEnum):
    """Kind of Modbus device hanging off a gateway."""

    MEDIDOR = "medidor"
    ANALIZADOR = "analizador"
    INVERSOR = "inversor"
    SENSOR = "sensor"


class ModbusRegisterType(StrEnum):
    """Modbus address space a register belongs to."""

    HOLDING = "holding"
    INPUT = "input"
    COIL = "coil"
    DISCRETE = "discrete"


class GatewayLogLevel(StrEnum):
    """Verbosity the firmware writes with, mirrored into its config file."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ModbusTransport(StrEnum):
    """How the gateway reaches the Modbus slave.

    RTU speaks over a serial port; TCP over the network. The two need disjoint
    sets of connection parameters, so the value decides which fields apply.
    """

    RTU = "rtu"
    TCP = "tcp"


class RegisterNotation(StrEnum):
    """The base a register address was read in, and is written back in.

    Device datasheets print addresses in either base, and `2006` means two
    different registers depending on which one. Recording the notation removes
    the guess: the operator says what they are reading, and the firmware gets
    the address in the same shape its map files already use.
    """

    DECIMAL = "decimal"
    HEX = "hex"


class ModbusDataType(StrEnum):
    """Wire format of a variable read over Modbus."""

    INT16 = "int16"
    UINT16 = "uint16"
    INT32 = "int32"
    UINT32 = "uint32"
    FLOAT32 = "float32"


class SerialParity(StrEnum):
    """Parity bit of a serial port."""

    NONE = "N"
    EVEN = "E"
    ODD = "O"


class UserRole(StrEnum):
    """Access level of a panel user."""

    ADMIN = "admin"
    TECNICO = "tecnico"
    CLIENTE = "cliente"
    SOLO_LECTURA = "solo_lectura"


class ServicePermission(StrEnum):
    """What a machine-to-machine credential is allowed to read.

    Every value is a read. A service account can never write: a credential
    that lives in another system's environment file is, in practice, as
    exposed as that system's weakest deployment, and nothing here is worth
    letting it modify the fleet.

    Granted one by one rather than as a role, so a consumer that only needs
    prices never receives the installation tree along with them.
    """

    TARIFFS_READ = "tariffs:read"
    FLEET_READ = "fleet:read"


class AlertType(StrEnum):
    """Condition an alert rule watches for."""

    DESCONEXION = "desconexion"
    VOLTAJE_FUERA_RANGO = "voltaje_fuera_rango"
    FACTOR_POTENCIA_BAJO = "factor_potencia_bajo"
    CONSUMO_EXCESIVO = "consumo_excesivo"


class NotificationChannel(StrEnum):
    """Where an alert notification is delivered."""

    EMAIL = "email"
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
