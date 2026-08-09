"""Object builders for tests.

Each helper fills in the mandatory columns with plausible values so a test only
has to state what it actually cares about.
"""

from datetime import date
from decimal import Decimal
from itertools import count
from typing import Any

from app.domain.enums import (
    EquipmentType,
    ModbusDataType,
    ModbusTransport,
    SerialParity,
    UserRole,
)
from app.models import (
    Client,
    Equipment,
    Gateway,
    Site,
    Tariff,
    User,
    Variable,
)

# The device name titles a config section, so it is unique per gateway. Tests
# that register several devices should not have to invent names.
_device_names = count(1)


def make_client(**overrides: Any) -> Client:
    return Client(**{"nombre_empresa": "Industrias Andinas", **overrides})


def make_site(client: Client, **overrides: Any) -> Site:
    return Site(
        **{
            "client_id": client.id,
            "nombre": "Planta Norte",
            "timezone": "America/Bogota",
            **overrides,
        }
    )


def make_gateway(site: Site, **overrides: Any) -> Gateway:
    return Gateway(
        **{"site_id": site.id, "numero_serie": "GW-0001", **overrides},
    )


def make_equipment(gateway: Gateway, **overrides: Any) -> Equipment:
    """An RTU device by default, which is what most of the parque is."""
    return Equipment(
        **{
            "gateway_id": gateway.id,
            "tipo": EquipmentType.ANALIZADOR,
            "modbus_id": 1,
            "nombre_dispositivo": f"Modbus_{next(_device_names)}",
            "device_type": "CT_Meter",
            "transporte": ModbusTransport.RTU,
            "puerto": "/dev/ttymxc1",
            "baudrate": 9600,
            "paridad": SerialParity.NONE,
            "bits": 8,
            "stop_bits": 1,
            **overrides,
        }
    )


def make_tcp_equipment(gateway: Gateway, **overrides: Any) -> Equipment:
    """A device reached over Modbus TCP: no serial parameters at all."""
    return Equipment(
        **{
            "gateway_id": gateway.id,
            "tipo": EquipmentType.ANALIZADOR,
            "modbus_id": 1,
            "nombre_dispositivo": f"Modbus_TCP_{next(_device_names)}",
            "device_type": "CT_Meter",
            "transporte": ModbusTransport.TCP,
            "host": "10.0.0.5",
            "puerto_tcp": 502,
            **overrides,
        }
    )


def make_variable(equipment: Equipment, **overrides: Any) -> Variable:
    return Variable(
        **{
            "equipment_id": equipment.id,
            "nombre": "PhV_phsA",
            "registro_modbus": 100,
            "tipo_dato": ModbusDataType.FLOAT32,
            **overrides,
        }
    )


def make_tariff(**overrides: Any) -> Tariff:
    return Tariff(
        **{
            "mes": date(2026, 1, 1),
            "valor_importado": Decimal("780.5000"),
            "valor_excedente": Decimal("310.0000"),
            **overrides,
        }
    )


def make_user(**overrides: Any) -> User:
    return User(
        **{
            "email": "admin@example.com",
            "password_hash": "not-a-real-hash",
            "role": UserRole.ADMIN,
            **overrides,
        }
    )


