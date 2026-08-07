"""Vocabulary the gateway firmware speaks.

The CRM stores readable names; the firmware reads single letters and hex
strings. Translating happens here, once, so the config endpoint is the only
place that knows about the other side's shapes and the rest of the app keeps
using the readable values.
"""

from app.domain.enums import ModbusDataType, ModbusTransport, RegisterNotation

# `struct` format characters, matching the firmware's DATATYPE enum. Every
# member of ModbusDataType must appear: a reading whose type the gateway cannot
# express would be silently unusable, so the mapping is exhaustive by design.
FIRMWARE_DATA_TYPE: dict[ModbusDataType, str] = {
    ModbusDataType.FLOAT32: "f",
    ModbusDataType.INT16: "h",
    ModbusDataType.UINT16: "H",
    ModbusDataType.INT32: "i",
    ModbusDataType.UINT32: "I",
}

# The firmware writes `protocol = RTU` in upper case.
FIRMWARE_PROTOCOL: dict[ModbusTransport, str] = {
    ModbusTransport.RTU: "RTU",
    ModbusTransport.TCP: "TCP",
}


def as_firmware_data_type(value: ModbusDataType) -> str:
    """Return the struct character the firmware expects for ``value``."""
    return FIRMWARE_DATA_TYPE[value]


def as_firmware_address(register: int, notation: RegisterNotation) -> str:
    """Return a register written in the base it was read in.

    Hex keeps the ``0x2006`` shape the device maps already use. Decimal goes
    out as plain digits: rendering a decimal address as hex would hand the
    firmware a different register than the one the operator meant.
    """
    if notation is RegisterNotation.HEX:
        return f"0x{register:04X}"
    return str(register)


def parse_address(value: str, notation: RegisterNotation) -> int:
    """Return the numeric register behind what the operator typed.

    Accepts ``2006`` or ``0x2006`` when the notation is hex, so pasting
    straight from a datasheet works either way.
    """
    text = value.strip()
    if not text:
        raise ValueError("register address cannot be empty")
    if notation is RegisterNotation.HEX:
        return int(text, 16)
    if text.lower().startswith("0x"):
        raise ValueError("address looks hexadecimal but the notation says decimal")
    return int(text, 10)
