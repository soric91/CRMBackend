"""Transport field rules, decided without a database."""

import pytest

from app.domain.enums import ModbusTransport, SerialParity
from app.domain.modbus import (
    TransportFieldError,
    describe_endpoint,
    normalize_transport_fields,
)

EMPTY: dict[str, object] = {
    "puerto": None,
    "baudrate": None,
    "paridad": None,
    "bits": None,
    "stop_bits": None,
    "host": None,
    "puerto_tcp": None,
}


class TestRtu:
    def test_it_fills_in_the_serial_defaults(self) -> None:
        result = normalize_transport_fields(ModbusTransport.RTU, dict(EMPTY))

        assert result["puerto"] == "/dev/ttymxc1"
        assert result["baudrate"] == 9600
        assert result["paridad"] is SerialParity.NONE
        assert result["bits"] == 8
        assert result["stop_bits"] == 1

    def test_it_keeps_the_values_that_were_given(self) -> None:
        result = normalize_transport_fields(
            ModbusTransport.RTU, {**EMPTY, "puerto": "/dev/ttyRS485", "baudrate": 19200}
        )

        assert result["puerto"] == "/dev/ttyRS485"
        assert result["baudrate"] == 19200

    def test_it_clears_the_network_fields(self) -> None:
        result = normalize_transport_fields(ModbusTransport.RTU, dict(EMPTY))

        assert result["host"] is None
        assert result["puerto_tcp"] is None

    @pytest.mark.parametrize("field", ["host", "puerto_tcp"])
    def test_a_network_field_is_rejected_not_ignored(self, field: str) -> None:
        """Silently dropping it would hide a misunderstanding from the caller."""
        with pytest.raises(TransportFieldError, match=field):
            normalize_transport_fields(
                ModbusTransport.RTU,
                {**EMPTY, field: "10.0.0.5" if field == "host" else 502},
            )


class TestTcp:
    def test_it_defaults_the_port_to_502(self) -> None:
        result = normalize_transport_fields(
            ModbusTransport.TCP, {**EMPTY, "host": "10.0.0.5"}
        )

        assert result["puerto_tcp"] == 502

    def test_it_keeps_an_explicit_port(self) -> None:
        result = normalize_transport_fields(
            ModbusTransport.TCP, {**EMPTY, "host": "10.0.0.5", "puerto_tcp": 5020}
        )

        assert result["puerto_tcp"] == 5020

    def test_it_clears_every_serial_field(self) -> None:
        result = normalize_transport_fields(
            ModbusTransport.TCP, {**EMPTY, "host": "10.0.0.5"}
        )

        for field in ("puerto", "baudrate", "paridad", "bits", "stop_bits"):
            assert result[field] is None, field

    def test_a_host_is_required(self) -> None:
        with pytest.raises(TransportFieldError, match="requires host"):
            normalize_transport_fields(ModbusTransport.TCP, dict(EMPTY))

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("puerto", "/dev/ttymxc1"),
            ("baudrate", 9600),
            ("bits", 8),
            ("stop_bits", 1),
        ],
    )
    def test_a_serial_field_is_rejected(self, field: str, value: object) -> None:
        with pytest.raises(TransportFieldError, match=field):
            normalize_transport_fields(
                ModbusTransport.TCP, {**EMPTY, "host": "10.0.0.5", field: value}
            )


class TestEndpointDescription:
    def test_a_serial_device_reads_as_its_port(self) -> None:
        assert (
            describe_endpoint(
                {"transporte": ModbusTransport.RTU, "puerto": "/dev/ttyRS485"}
            )
            == "/dev/ttyRS485"
        )

    def test_a_network_device_reads_as_host_and_port(self) -> None:
        assert (
            describe_endpoint(
                {
                    "transporte": ModbusTransport.TCP,
                    "host": "10.0.0.5",
                    "puerto_tcp": 502,
                }
            )
            == "10.0.0.5:502"
        )
