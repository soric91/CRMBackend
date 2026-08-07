"""Password hashing and JWT handling."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.core.security import (
    MAX_PASSWORD_BYTES,
    PasswordTooLongError,
    TokenType,
    create_access_token,
    create_refresh_token,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_a_password_verifies_against_its_own_hash(self) -> None:
        assert verify_password(
            "correct horse battery", hash_password("correct horse battery")
        )

    def test_a_different_password_does_not_verify(self) -> None:
        assert not verify_password("wrong", hash_password("right"))

    def test_the_hash_does_not_contain_the_password(self) -> None:
        assert "supersecreto" not in hash_password("supersecreto")

    def test_the_same_password_hashes_differently_every_time(self) -> None:
        """A per-hash salt stops identical passwords looking identical."""
        assert hash_password("same") != hash_password("same")

    def test_the_hash_is_recognisably_bcrypt(self) -> None:
        assert hash_password("x" * 10).startswith("$2b$")

    def test_verification_is_case_sensitive(self) -> None:
        assert not verify_password("Password", hash_password("password"))

    def test_a_corrupt_hash_reads_as_a_failed_login(self) -> None:
        """A damaged row must not turn a login into a 500."""
        assert verify_password("anything", "not-a-hash") is False

    def test_an_empty_hash_reads_as_a_failed_login(self) -> None:
        assert verify_password("anything", "") is False

    def test_a_password_at_the_bcrypt_limit_is_accepted(self) -> None:
        password = "a" * MAX_PASSWORD_BYTES
        assert verify_password(password, hash_password(password))

    def test_a_longer_password_is_rejected_rather_than_truncated(self) -> None:
        """Silent truncation would make two different passwords equivalent."""
        with pytest.raises(PasswordTooLongError):
            hash_password("a" * (MAX_PASSWORD_BYTES + 1))

    def test_multibyte_characters_count_as_bytes(self) -> None:
        with pytest.raises(PasswordTooLongError):
            hash_password("ñ" * 40)  # 80 bytes in UTF-8


class TestTokenCreation:
    def test_an_access_token_carries_subject_role_and_type(
        self, settings: Settings
    ) -> None:
        token = create_access_token(
            settings, subject="user-1", claims={"role": "admin"}
        )
        payload = decode_token(settings, token, expected_type=TokenType.ACCESS)

        assert payload["sub"] == "user-1"
        assert payload["role"] == "admin"
        assert payload["type"] == "access"

    def test_a_refresh_token_carries_no_role(self, settings: Settings) -> None:
        """Privileges are re-read from the database, never trusted from here."""
        token = create_refresh_token(settings, subject="user-1")
        payload = decode_token(settings, token, expected_type=TokenType.REFRESH)

        assert "role" not in payload

    def test_two_tokens_for_one_subject_are_distinct(self, settings: Settings) -> None:
        first = create_refresh_token(settings, subject="user-1")
        second = create_refresh_token(settings, subject="user-1")
        assert first != second

    def test_the_refresh_token_outlives_the_access_token(
        self, settings: Settings
    ) -> None:
        access = decode_token(
            settings,
            create_access_token(settings, subject="u"),
            expected_type=TokenType.ACCESS,
        )
        refresh = decode_token(
            settings,
            create_refresh_token(settings, subject="u"),
            expected_type=TokenType.REFRESH,
        )
        assert refresh["exp"] > access["exp"]


class TestTokenValidation:
    def test_a_token_signed_with_another_key_is_rejected(
        self, settings: Settings
    ) -> None:
        impostor = settings.model_copy(update={"jwt_secret_key": SecretStr("z" * 40)})
        token = create_access_token(impostor, subject="user-1")

        with pytest.raises(AuthenticationError, match="Invalid token"):
            decode_token(settings, token, expected_type=TokenType.ACCESS)

    def test_an_expired_token_is_rejected(self, settings: Settings) -> None:
        token = create_token(
            settings,
            subject="user-1",
            token_type=TokenType.ACCESS,
            expires_in=timedelta(seconds=-10),
        )

        with pytest.raises(AuthenticationError, match="expired"):
            decode_token(settings, token, expected_type=TokenType.ACCESS)

    def test_a_refresh_token_cannot_be_used_as_an_access_token(
        self, settings: Settings
    ) -> None:
        token = create_refresh_token(settings, subject="user-1")

        with pytest.raises(AuthenticationError, match="Expected a access token"):
            decode_token(settings, token, expected_type=TokenType.ACCESS)

    def test_an_access_token_cannot_be_used_as_a_refresh_token(
        self, settings: Settings
    ) -> None:
        token = create_access_token(settings, subject="user-1")

        with pytest.raises(AuthenticationError):
            decode_token(settings, token, expected_type=TokenType.REFRESH)

    def test_an_unsigned_none_algorithm_token_is_rejected(
        self, settings: Settings
    ) -> None:
        """The classic JWT forgery: claim `alg: none` and drop the signature."""
        forged = jwt.encode(
            {
                "sub": "user-1",
                "type": "access",
                "role": "admin",
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            },
            key="",
            algorithm="none",
        )

        with pytest.raises(AuthenticationError):
            decode_token(settings, forged, expected_type=TokenType.ACCESS)

    def test_a_tampered_payload_is_rejected(self, settings: Settings) -> None:
        token = create_access_token(settings, subject="u", claims={"role": "cliente"})
        header, _, signature = token.split(".")
        other = create_access_token(settings, subject="u", claims={"role": "admin"})
        forged = f"{header}.{other.split('.')[1]}.{signature}"

        with pytest.raises(AuthenticationError):
            decode_token(settings, forged, expected_type=TokenType.ACCESS)

    @pytest.mark.parametrize(
        "token", ["", "garbage", "a.b.c", "Bearer something", "..."]
    )
    def test_malformed_tokens_are_rejected(
        self, settings: Settings, token: str
    ) -> None:
        with pytest.raises(AuthenticationError):
            decode_token(settings, token, expected_type=TokenType.ACCESS)

    def test_a_token_without_an_expiry_is_rejected(self, settings: Settings) -> None:
        """A token that never expires cannot be taken back."""
        forever = jwt.encode(
            {"sub": "user-1", "type": "access"},
            settings.jwt_signing_key,
            algorithm=settings.jwt_algorithm,
        )

        with pytest.raises(AuthenticationError):
            decode_token(settings, forever, expected_type=TokenType.ACCESS)
