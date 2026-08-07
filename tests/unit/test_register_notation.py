"""Reading and writing register addresses in the base they came in."""

import pytest

from app.domain.enums import RegisterNotation
from app.domain.firmware import as_firmware_address, parse_address


class TestParsing:
    def test_hex_digits_are_read_in_base_sixteen(self) -> None:
        """`2006` off a datasheet is register 8198, not 2006."""
        assert parse_address("2006", RegisterNotation.HEX) == 8198

    def test_the_prefix_is_optional(self) -> None:
        assert parse_address("0x2006", RegisterNotation.HEX) == 8198
        assert parse_address("0X2006", RegisterNotation.HEX) == 8198

    def test_decimal_digits_are_read_in_base_ten(self) -> None:
        assert parse_address("2000", RegisterNotation.DECIMAL) == 2000

    def test_surrounding_blanks_are_ignored(self) -> None:
        assert parse_address("  2006  ", RegisterNotation.HEX) == 8198

    def test_a_prefixed_value_declared_decimal_is_rejected(self) -> None:
        """The two disagree, and guessing which one is right loses data."""
        with pytest.raises(ValueError, match="notation says decimal"):
            parse_address("0x2006", RegisterNotation.DECIMAL)

    @pytest.mark.parametrize("value", ["", "   ", "no-es-un-numero", "12ZZ"])
    def test_nonsense_is_rejected(self, value: str) -> None:
        with pytest.raises(ValueError):
            parse_address(value, RegisterNotation.HEX)

    def test_letters_are_only_valid_in_hex(self) -> None:
        assert parse_address("2A", RegisterNotation.HEX) == 42
        with pytest.raises(ValueError):
            parse_address("2A", RegisterNotation.DECIMAL)


class TestRendering:
    def test_a_hex_address_keeps_the_map_file_shape(self) -> None:
        assert as_firmware_address(8198, RegisterNotation.HEX) == "0x2006"

    def test_a_decimal_address_goes_out_as_plain_digits(self) -> None:
        """Rendering it as hex would point the firmware at another register."""
        assert as_firmware_address(2000, RegisterNotation.DECIMAL) == "2000"

    def test_hex_is_padded_to_four_digits(self) -> None:
        assert as_firmware_address(6, RegisterNotation.HEX) == "0x0006"

    def test_hex_uses_upper_case(self) -> None:
        assert as_firmware_address(43981, RegisterNotation.HEX) == "0xABCD"

    def test_a_wide_address_is_not_truncated(self) -> None:
        assert as_firmware_address(0x12345, RegisterNotation.HEX) == "0x12345"


class TestRoundTrip:
    @pytest.mark.parametrize(
        ("typed", "notation", "stored", "written"),
        [
            ("2006", RegisterNotation.HEX, 8198, "0x2006"),
            ("0x2006", RegisterNotation.HEX, 8198, "0x2006"),
            ("2000", RegisterNotation.DECIMAL, 2000, "2000"),
            ("0", RegisterNotation.DECIMAL, 0, "0"),
        ],
    )
    def test_what_was_typed_comes_back_the_same(
        self, typed: str, notation: RegisterNotation, stored: int, written: str
    ) -> None:
        address = parse_address(typed, notation)

        assert address == stored
        assert as_firmware_address(address, notation) == written

    def test_the_two_bases_disagree_on_the_same_digits(self) -> None:
        """The whole reason the notation is recorded."""
        assert parse_address("2000", RegisterNotation.HEX) != parse_address(
            "2000", RegisterNotation.DECIMAL
        )
