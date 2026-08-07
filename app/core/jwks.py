"""The public half of the signing key, in the shape other services expect.

A JSON Web Key Set is how an issuer hands out the material needed to *check*
its signatures without handing out the material needed to *make* them. It is
the whole reason `ApiEMS` can trust a token this API minted while remaining
unable to mint one itself.
"""

import base64
import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

# How many characters of the key fingerprint become the `kid`. Enough that two
# keys never collide in practice, short enough to read in a log line.
KEY_ID_LENGTH = 16


def _load_rsa_public_key(public_key_pem: str) -> rsa.RSAPublicKey:
    """Parse a PEM, refusing anything that is not an RSA public key.

    The RS* algorithms are RSA by definition, so an EC or DH key here is a
    configuration mistake that would otherwise surface as a confusing failure
    deep inside the JWT library.
    """
    key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    if not isinstance(key, rsa.RSAPublicKey):
        raise ValueError(f"RS* needs an RSA public key, got {type(key).__name__}.")
    return key


def compute_key_id(public_key_pem: str) -> str:
    """Return a stable identifier derived from the key itself.

    Derived rather than assigned: the same key always produces the same `kid`,
    so a consumer that cached it keeps matching after a restart, and two
    different keys can never accidentally share one during a rotation.
    """
    der = _load_rsa_public_key(public_key_pem).public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    digest = hashlib.sha256(der).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:KEY_ID_LENGTH]


def build_jwks(public_key_pem: str | None, algorithm: str) -> dict[str, Any]:
    """Return the key set to publish.

    An empty set is the honest answer while signing with a shared secret:
    there is no public key, and saying so is better than a 404 that a consumer
    would have to special-case.
    """
    if public_key_pem is None:
        return {"keys": []}

    jwk: dict[str, Any] = json.loads(
        RSAAlgorithm.to_jwk(_load_rsa_public_key(public_key_pem))
    )
    return {
        "keys": [
            {
                **jwk,
                "kid": compute_key_id(public_key_pem),
                "alg": algorithm,
                # Signature verification only. Never encryption: the same key
                # used for both is a well-known way to weaken both.
                "use": "sig",
            }
        ]
    }
