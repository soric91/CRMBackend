"""Which connection fields belong to which Modbus transport.

Pure rules, shared by the request schemas and the service layer. A creation
validates the payload; an update validates the row's resulting state, since a
PATCH that only flips ``transporte`` still has to leave a coherent row. Both go
through :func:`normalize_transport_fields`, so the two paths cannot drift.
"""

from typing import Any

from app.domain.enums import ModbusTransport, SerialParity

# Serial parameters: meaningful only over RTU.
RTU_FIELDS = ("puerto", "baudrate", "paridad", "bits", "stop_bits")
RTU_DEFAULTS: dict[str, Any] = {
    "puerto": "/dev/ttymxc1",
    "baudrate": 9600,
    "paridad": SerialParity.NONE,
    "bits": 8,
    "stop_bits": 1,
}

# Network parameters: meaningful only over TCP.
TCP_FIELDS = ("host", "puerto_tcp")
TCP_DEFAULTS: dict[str, Any] = {"puerto_tcp": 502}

# The field each transport cannot do without.
REQUIRED_FIELD = {ModbusTransport.RTU: "puerto", ModbusTransport.TCP: "host"}

_LABEL = {ModbusTransport.RTU: "rtu", ModbusTransport.TCP: "tcp"}


class TransportFieldError(ValueError):
    """A field was given that the chosen transport does not use, or is missing."""


def normalize_transport_fields(
    transporte: ModbusTransport, values: dict[str, Any]
) -> dict[str, Any]:
    """Return ``values`` with the fields of the chosen transport filled in.

    Defaults are applied for the transport in use, the other transport's fields
    are forced to ``None``, and passing one of them explicitly is an error
    rather than a silent drop: a caller sending ``host`` on an RTU device has
    misunderstood something, and swallowing it would hide the mistake.
    """
    used, unused, defaults = (
        (RTU_FIELDS, TCP_FIELDS, RTU_DEFAULTS)
        if transporte is ModbusTransport.RTU
        else (TCP_FIELDS, RTU_FIELDS, TCP_DEFAULTS)
    )

    offending = [name for name in unused if values.get(name) is not None]
    if offending:
        raise TransportFieldError(
            f"Transport '{_LABEL[transporte]}' does not use "
            f"{', '.join(sorted(offending))}"
        )

    result = dict(values)
    for name in unused:
        result[name] = None
    for name, default in defaults.items():
        if result.get(name) is None:
            result[name] = default

    required = REQUIRED_FIELD[transporte]
    if result.get(required) is None:
        raise TransportFieldError(
            f"Transport '{_LABEL[transporte]}' requires {required}"
        )

    for name in used:
        result.setdefault(name, None)
    return result


def describe_endpoint(values: dict[str, Any]) -> str:
    """Human-readable address of a device, for conflict messages."""
    if values.get("transporte") is ModbusTransport.TCP or values.get("host"):
        return f"{values.get('host')}:{values.get('puerto_tcp')}"
    return str(values.get("puerto"))
