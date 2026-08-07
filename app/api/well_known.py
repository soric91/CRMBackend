"""Discovery endpoints, served from the root rather than under `/api/v1`.

`/.well-known/` is where a consumer looks without being told, and its meaning
does not change between API versions — pinning it to `v1` would mean moving it
the day `v2` exists, breaking every service that cached the location.
"""

from typing import Any

from fastapi import APIRouter, Response

from app.api.deps import SettingsDep
from app.core.jwks import build_jwks

router = APIRouter(tags=["well-known"])

# A key changes only when somebody rotates it, and a consumer that re-reads
# this on every request would be paying for nothing. An hour is short enough
# that a rotation is picked up the same working day.
CACHE_SECONDS = 3600


@router.get("/.well-known/jwks.json")
async def get_jwks(settings: SettingsDep, response: Response) -> dict[str, Any]:
    """Publish the public half of the signing key.

    Deliberately unauthenticated: this is what makes another service able to
    verify a token this API issued. It carries no secret — the public key
    checks signatures and cannot produce them.

    Answers an empty key set while signing with a shared secret, which is the
    truthful answer rather than an error a consumer would have to special-case.
    """
    response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    return build_jwks(settings.jwt_public_key_pem, settings.jwt_algorithm)
