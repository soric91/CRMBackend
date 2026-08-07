"""The published key set, over HTTP.

This is the one route another service reads before it trusts anything else,
so the properties that matter are: it needs no credentials, it carries no
secret, and it lives where a consumer will look without being told.
"""

import json

import jwt
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.security import TokenAudience, create_access_token

JWKS = "/.well-known/jwks.json"


async def _get(app: FastAPI, path: str = JWKS) -> tuple[int, str, dict[str, str]]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get(path)
    return response.status_code, response.text, dict(response.headers)


class TestReachingIt:
    async def test_it_needs_no_token(self, client: AsyncClient) -> None:
        """A consumer has nothing to present yet — that is the point."""
        response = await client.get(JWKS)

        assert response.status_code == status.HTTP_200_OK

    async def test_it_is_not_behind_the_version_prefix(
        self, client: AsyncClient
    ) -> None:
        """`/.well-known/` is a fixed location; moving it on v2 breaks callers."""
        response = await client.get("/api/v1/.well-known/jwks.json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_it_asks_to_be_cached(self, client: AsyncClient) -> None:
        """A key changes only on rotation; re-reading it per request is waste."""
        response = await client.get(JWKS)

        assert "max-age" in response.headers["Cache-Control"]

    async def test_it_answers_an_empty_set_under_a_shared_secret(
        self, client: AsyncClient
    ) -> None:
        """Truthful rather than a 404 the consumer would have to special-case."""
        response = await client.get(JWKS)

        assert response.json() == {"keys": []}


class TestWithAKeyPair:
    async def test_it_publishes_the_public_key(self, rs256_app: FastAPI) -> None:
        status_code, body, _ = await _get(rs256_app)

        assert status_code == status.HTTP_200_OK
        keys = json.loads(body)["keys"]
        assert len(keys) == 1
        assert keys[0]["kty"] == "RSA"
        assert keys[0]["use"] == "sig"
        assert keys[0]["alg"] == "RS256"

    async def test_it_never_carries_the_private_half(self, rs256_app: FastAPI) -> None:
        _, body, _ = await _get(rs256_app)

        assert "PRIVATE" not in body
        # The RSA private exponent. Publishing it would hand out the ability
        # to mint tokens along with the ability to check them.
        assert '"d"' not in body

    async def test_a_token_issued_by_the_app_verifies_with_it(
        self, rs256_settings: Settings, rs256_app: FastAPI
    ) -> None:
        """End to end, exactly as `ApiEMS` will do it.

        Nothing below touches the private key or the settings object — only a
        token and the JSON fetched over HTTP.
        """
        token = create_access_token(
            rs256_settings, subject="cliente-1", audience=TokenAudience.MONITOR
        )

        transport = ASGITransport(app=rs256_app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            published = (await http.get(JWKS)).json()

        claims = jwt.decode(
            token,
            jwt.PyJWK(published["keys"][0]).key,
            algorithms=["RS256"],
            audience="monitor",
        )

        assert claims["sub"] == "cliente-1"

    async def test_a_token_from_the_other_installation_is_refused(
        self, rs256_app: FastAPI, settings: Settings
    ) -> None:
        """A token signed with a shared secret must not pass as RS256."""
        hs_token = create_access_token(
            settings, subject="intruso", audience=TokenAudience.MONITOR
        )

        transport = ASGITransport(app=rs256_app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            published = (await http.get(JWKS)).json()

        try:
            jwt.decode(
                hs_token,
                jwt.PyJWK(published["keys"][0]).key,
                algorithms=["RS256"],
                audience="monitor",
            )
        except jwt.PyJWTError:
            return
        raise AssertionError("an HS256 token must not verify against the key set")
