"""Signing with a key pair instead of a shared secret.

The point of RS256 here is one property: another service can check a token
this API issued and still be unable to produce one. Most of what follows is
that property, stated as things that must fail.
"""

import base64
import hashlib
import hmac
import json
from datetime import timedelta
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError as PydanticValidationError

from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.core.jwks import build_jwks, compute_key_id
from app.core.security import (
    TokenAudience,
    TokenType,
    create_token,
    decode_token,
)
from tests.conftest import TEST_DB_URL


def _write_keypair(directory: Path, name: str = "jwt") -> tuple[Path, Path]:
    """Generate an RSA key pair and write both halves as PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_path = directory / f"{name}-private.pem"
    public_path = directory / f"{name}-public.pem"
    private_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


@pytest.fixture(scope="module")
def keypair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """One key pair for the whole module: RSA generation is slow."""
    return _write_keypair(tmp_path_factory.mktemp("keys"))


@pytest.fixture(scope="module")
def other_keypair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """A second, unrelated pair — the impostor."""
    return _write_keypair(tmp_path_factory.mktemp("other-keys"), name="other")


def _rs_settings(keypair: tuple[Path, Path], **overrides: object) -> Settings:
    private_path, public_path = keypair
    return Settings(
        database_dsn=TEST_DB_URL,  # pyright: ignore[reportArgumentType]
        jwt_algorithm="RS256",
        jwt_private_key_path=str(private_path),
        jwt_public_key_path=str(public_path),
        _env_file=None,  # pyright: ignore[reportCallIssue]
        **overrides,  # pyright: ignore[reportArgumentType]
    )


class TestConfiguringIt:
    def test_the_pair_is_loaded_at_startup(self, keypair: tuple[Path, Path]) -> None:
        settings = _rs_settings(keypair)

        assert settings.uses_asymmetric_jwt is True
        assert "BEGIN PRIVATE KEY" in settings.jwt_signing_key
        assert "BEGIN PUBLIC KEY" in settings.jwt_verification_key

    def test_signing_and_verifying_keys_differ(
        self, keypair: tuple[Path, Path]
    ) -> None:
        """The whole point. Under HS* these are the same string."""
        settings = _rs_settings(keypair)

        assert settings.jwt_signing_key != settings.jwt_verification_key

    def test_a_missing_key_stops_the_process(self) -> None:
        """A deployment mistake must not become a 500 on every login."""
        with pytest.raises(PydanticValidationError, match="JWT_PRIVATE_KEY_PATH"):
            Settings(
                database_dsn=TEST_DB_URL,  # pyright: ignore[reportArgumentType]
                jwt_algorithm="RS256",
                _env_file=None,  # pyright: ignore[reportCallIssue]
            )

    def test_an_unreadable_key_is_named_in_the_error(self, tmp_path: Path) -> None:
        with pytest.raises(PydanticValidationError, match="cannot read"):
            Settings(
                database_dsn=TEST_DB_URL,  # pyright: ignore[reportArgumentType]
                jwt_algorithm="RS256",
                jwt_private_key_path=str(tmp_path / "no-existe.pem"),
                jwt_public_key_path=str(tmp_path / "no-existe.pem"),
                _env_file=None,  # pyright: ignore[reportCallIssue]
            )

    def test_a_file_that_is_not_a_pem_is_refused(self, tmp_path: Path) -> None:
        junk = tmp_path / "junk.pem"
        junk.write_text("esto no es una clave")

        with pytest.raises(PydanticValidationError, match="not a PEM"):
            Settings(
                database_dsn=TEST_DB_URL,  # pyright: ignore[reportArgumentType]
                jwt_algorithm="RS256",
                jwt_private_key_path=str(junk),
                jwt_public_key_path=str(junk),
                _env_file=None,  # pyright: ignore[reportCallIssue]
            )

    def test_hs256_still_needs_its_secret(self) -> None:
        with pytest.raises(PydanticValidationError, match="JWT_SECRET_KEY"):
            Settings(
                database_dsn=TEST_DB_URL,  # pyright: ignore[reportArgumentType]
                jwt_algorithm="HS256",
                _env_file=None,  # pyright: ignore[reportCallIssue]
            )

    def test_an_unknown_algorithm_is_refused(self) -> None:
        """`none` is the classic one, and it must never be configurable."""
        with pytest.raises(PydanticValidationError):
            Settings(
                database_dsn=TEST_DB_URL,  # pyright: ignore[reportArgumentType]
                jwt_algorithm="none",
                _env_file=None,  # pyright: ignore[reportCallIssue]
            )


class TestTokensRoundTrip:
    def test_a_token_signed_here_verifies_here(
        self, keypair: tuple[Path, Path]
    ) -> None:
        settings = _rs_settings(keypair)
        token = create_token(
            settings,
            subject="user-1",
            token_type=TokenType.ACCESS,
            expires_in=timedelta(minutes=5),
            audience=TokenAudience.MONITOR,
        )

        claims = decode_token(
            settings,
            token,
            expected_type=TokenType.ACCESS,
            expected_audience=TokenAudience.MONITOR,
        )

        assert claims["sub"] == "user-1"

    def test_it_is_actually_signed_with_rs256(self, keypair: tuple[Path, Path]) -> None:
        settings = _rs_settings(keypair)
        token = create_token(
            settings,
            subject="user-1",
            token_type=TokenType.ACCESS,
            expires_in=timedelta(minutes=5),
        )

        assert jwt.get_unverified_header(token)["alg"] == "RS256"

    def test_the_header_names_the_key(self, keypair: tuple[Path, Path]) -> None:
        """So a consumer holding two keys during a rotation picks the right one."""
        settings = _rs_settings(keypair)
        token = create_token(
            settings,
            subject="user-1",
            token_type=TokenType.ACCESS,
            expires_in=timedelta(minutes=5),
        )

        header_kid = jwt.get_unverified_header(token)["kid"]
        assert header_kid == compute_key_id(settings.jwt_verification_key)

    def test_a_token_from_another_key_is_refused(
        self, keypair: tuple[Path, Path], other_keypair: tuple[Path, Path]
    ) -> None:
        impostor = _rs_settings(other_keypair)
        token = create_token(
            impostor,
            subject="user-1",
            token_type=TokenType.ACCESS,
            expires_in=timedelta(minutes=5),
        )

        with pytest.raises(AuthenticationError):
            decode_token(_rs_settings(keypair), token, expected_type=TokenType.ACCESS)


FORGED_CLAIMS = {
    "sub": "admin",
    "type": "access",
    "aud": "crm",
    "exp": 9999999999,
}


def _b64(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _forge(header: dict[str, object], claims: dict[str, object], key: bytes) -> str:
    """Assemble a JWT by hand, the way an attacker would.

    Not via ``jwt.encode``: PyJWT refuses to use a PEM as an HMAC secret on the
    signing side, which would make the test prove the library's guard instead
    of ours. Whoever is attacking is not using our library.
    """
    signing_input = (
        _b64(json.dumps(header).encode()) + b"." + _b64(json.dumps(claims).encode())
    )
    signature = hmac.new(key, signing_input, hashlib.sha256).digest()
    return (signing_input + b"." + _b64(signature)).decode("ascii")


class TestTheAttackThisPrevents:
    def test_the_public_key_cannot_be_used_to_forge_a_token(
        self, keypair: tuple[Path, Path]
    ) -> None:
        """Algorithm confusion, the reason `algorithms` is pinned to one value.

        The public key is published, so anyone has it. Signing HS256 with that
        PEM as the shared secret produces a token that a verifier accepting
        both families would happily believe — it would check an HMAC using a
        value the attacker also holds. Pinning to exactly one algorithm is the
        entire defence.
        """
        settings = _rs_settings(keypair)
        forged = _forge(
            {"alg": "HS256", "typ": "JWT"},
            FORGED_CLAIMS,
            settings.jwt_verification_key.encode("utf-8"),
        )

        with pytest.raises(AuthenticationError):
            decode_token(settings, forged, expected_type=TokenType.ACCESS)

    def test_the_same_forgery_is_refused_under_hs256_too(
        self, settings: Settings
    ) -> None:
        """Signed with the wrong secret, so it fails for the ordinary reason."""
        forged = _forge({"alg": "HS256", "typ": "JWT"}, FORGED_CLAIMS, b"otro-secreto")

        with pytest.raises(AuthenticationError):
            decode_token(settings, forged, expected_type=TokenType.ACCESS)

    def test_an_unsigned_token_is_refused(self, keypair: tuple[Path, Path]) -> None:
        """`alg: none` asks the verifier to skip the signature entirely."""
        settings = _rs_settings(keypair)
        signing_input = (
            _b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
            + b"."
            + _b64(json.dumps(FORGED_CLAIMS).encode())
        )
        unsigned = (signing_input + b".").decode("ascii")

        with pytest.raises(AuthenticationError):
            decode_token(settings, unsigned, expected_type=TokenType.ACCESS)

    def test_the_private_key_is_never_published(
        self, keypair: tuple[Path, Path]
    ) -> None:
        settings = _rs_settings(keypair)

        document = json.dumps(
            build_jwks(settings.jwt_public_key_pem, settings.jwt_algorithm)
        )

        assert "PRIVATE" not in document
        # `d` is the RSA private exponent. Its presence would turn the public
        # key set into the private one.
        assert '"d"' not in document


class TestTheKeySet:
    def test_it_carries_one_usable_key(self, keypair: tuple[Path, Path]) -> None:
        settings = _rs_settings(keypair)

        document = build_jwks(settings.jwt_public_key_pem, settings.jwt_algorithm)

        assert len(document["keys"]) == 1
        key = document["keys"][0]
        assert key["kty"] == "RSA"
        assert key["alg"] == "RS256"
        assert key["use"] == "sig"
        assert key["kid"] == compute_key_id(settings.jwt_verification_key)

    def test_a_consumer_can_verify_with_only_the_published_key(
        self, keypair: tuple[Path, Path]
    ) -> None:
        """The end-to-end promise, exercised the way `ApiEMS` will.

        Nothing here touches the private key or the settings object — only the
        JSON another service would fetch over HTTP.
        """
        settings = _rs_settings(keypair)
        token = create_token(
            settings,
            subject="cliente-1",
            token_type=TokenType.ACCESS,
            expires_in=timedelta(minutes=5),
            audience=TokenAudience.MONITOR,
            claims={"client_id": "801a7729"},
        )
        published = build_jwks(settings.jwt_public_key_pem, settings.jwt_algorithm)

        public_key = jwt.PyJWK(published["keys"][0]).key
        claims = jwt.decode(token, public_key, algorithms=["RS256"], audience="monitor")

        assert claims["sub"] == "cliente-1"
        assert claims["client_id"] == "801a7729"

    def test_the_key_id_is_stable(self, keypair: tuple[Path, Path]) -> None:
        """A consumer that cached it must keep matching after a restart."""
        settings = _rs_settings(keypair)

        assert compute_key_id(settings.jwt_verification_key) == compute_key_id(
            settings.jwt_verification_key
        )

    def test_two_keys_never_share_an_id(
        self, keypair: tuple[Path, Path], other_keypair: tuple[Path, Path]
    ) -> None:
        assert compute_key_id(
            _rs_settings(keypair).jwt_verification_key
        ) != compute_key_id(_rs_settings(other_keypair).jwt_verification_key)

    def test_a_shared_secret_publishes_nothing(self, settings: Settings) -> None:
        """Empty is the honest answer: there is no public key to hand out."""
        assert build_jwks(settings.jwt_public_key_pem, settings.jwt_algorithm) == {
            "keys": []
        }

    def test_a_non_rsa_key_is_refused(self, tmp_path: Path) -> None:
        """RS* means RSA; anything else is a configuration mistake."""
        from cryptography.hazmat.primitives.asymmetric import ec

        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        with pytest.raises(ValueError, match="RSA public key"):
            compute_key_id(pem.decode("utf-8"))
