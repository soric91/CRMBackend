"""Monthly tariff endpoints."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import date

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.domain.enums import UserRole
from app.models import User
from tests.conftest import TEST_PASSWORD_HASH, auth_header

type Login = Callable[..., Awaitable[str]]


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


@pytest.fixture
async def tecnico_headers(tecnico_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(tecnico_user.email))


@pytest.fixture
async def readonly_headers(
    db_session: AsyncSession, authenticate: Login
) -> dict[str, str]:
    user = User(
        email="lectura@example.com",
        password_hash=TEST_PASSWORD_HASH,
        role=UserRole.SOLO_LECTURA,
    )
    db_session.add(user)
    await db_session.flush()
    return auth_header(await authenticate(user.email))


@pytest.fixture
async def cliente_headers(
    cliente_user: User, authenticate_monitor: Login
) -> dict[str, str]:
    """Clients log in through the monitoring web, never through the CRM."""
    return auth_header(await authenticate_monitor(cliente_user.email))


async def _create(
    client: AsyncClient,
    headers: dict[str, str],
    mes: str = "2026-01-01",
    valor_importado: str = "780.5000",
    **extra: object,
) -> tuple[int, dict[str, object]]:
    response = await client.post(
        "/api/v1/tariffs",
        json={"mes": mes, "valor_importado": valor_importado, **extra},
        headers=headers,
    )
    return response.status_code, response.json()


class TestCreate:
    async def test_a_month_can_be_registered(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        code, body = await _create(client, admin_headers)

        assert code == status.HTTP_201_CREATED
        assert body["mes"] == "2026-01-01"
        assert body["periodo"] == "enero 2026"
        assert body["valor_importado"] == "780.5000"

    async def test_the_surplus_price_defaults_to_zero(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        _, body = await _create(client, admin_headers)

        assert body["valor_excedente"] == "0.0000"

    async def test_both_prices_can_be_given(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        _, body = await _create(client, admin_headers, valor_excedente="310.0000")

        assert body["valor_excedente"] == "310.0000"

    async def test_any_day_is_snapped_to_the_first_of_the_month(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """The field means a month; the day is representation noise."""
        _, body = await _create(client, admin_headers, mes="2026-05-17")

        assert body["mes"] == "2026-05-01"
        assert body["periodo"] == "mayo 2026"

    async def test_a_second_tariff_for_the_same_month_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        await _create(client, admin_headers, mes="2026-01-01")

        code, body = await _create(client, admin_headers, mes="2026-01-01")

        assert code == status.HTTP_409_CONFLICT
        error = body["error"]
        assert isinstance(error, dict)
        assert error["code"] == "already_exists"
        assert "enero 2026" in str(error["message"])

    async def test_a_normalised_day_still_collides(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Snapping happens before the uniqueness check, not after."""
        await _create(client, admin_headers, mes="2026-01-01")

        code, _ = await _create(client, admin_headers, mes="2026-01-28")

        assert code == status.HTTP_409_CONFLICT

    async def test_the_same_month_of_another_year_is_a_different_period(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        await _create(client, admin_headers, mes="2026-01-01")

        code, _ = await _create(client, admin_headers, mes="2027-01-01")

        assert code == status.HTTP_201_CREATED

    @pytest.mark.parametrize(
        "payload",
        [
            {"mes": "2026-01-01", "valor_importado": "-1"},
            {"mes": "2026-01-01", "valor_importado": "780", "valor_excedente": "-1"},
            {"mes": "not-a-date", "valor_importado": "780"},
            {"valor_importado": "780"},
            {"mes": "2026-01-01"},
        ],
    )
    async def test_malformed_payloads_are_rejected(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        payload: dict[str, str],
    ) -> None:
        response = await client.post(
            "/api/v1/tariffs", json=payload, headers=admin_headers
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestListing:
    async def test_it_comes_back_most_recent_first(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """The period being consulted is almost always the latest."""
        for mes in ("2026-01-01", "2026-06-01", "2026-03-01"):
            await _create(client, admin_headers, mes=mes)

        response = await client.get("/api/v1/tariffs", headers=admin_headers)

        months = [item["mes"] for item in response.json()["items"]]
        assert months == ["2026-06-01", "2026-03-01", "2026-01-01"]

    async def test_it_is_paginated(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        for month in range(1, 4):
            await _create(client, admin_headers, mes=f"2026-0{month}-01")

        response = await client.get(
            "/api/v1/tariffs?limit=2&offset=0", headers=admin_headers
        )

        body = response.json()
        assert len(body["items"]) == 2
        assert body["total"] == 3

    @pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1"])
    async def test_out_of_range_pagination_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], query: str
    ) -> None:
        response = await client.get(f"/api/v1/tariffs?{query}", headers=admin_headers)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_an_empty_platform_returns_an_empty_page(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.get("/api/v1/tariffs", headers=admin_headers)

        assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


class TestUpdate:
    async def test_the_prices_can_be_corrected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        _, created = await _create(client, admin_headers)

        response = await client.patch(
            f"/api/v1/tariffs/{created['id']}",
            json={"valor_importado": "900.0000"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["valor_importado"] == "900.0000"
        assert response.json()["valor_excedente"] == "0.0000"

    async def test_the_period_cannot_be_moved(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Moving a tariff would rewrite costs that were already computed."""
        _, created = await _create(client, admin_headers, mes="2026-01-01")

        response = await client.patch(
            f"/api/v1/tariffs/{created['id']}",
            json={"mes": "2026-09-01", "valor_importado": "900"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["mes"] == "2026-01-01"
        assert response.json()["periodo"] == "enero 2026"

    async def test_a_negative_price_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        _, created = await _create(client, admin_headers)

        response = await client.patch(
            f"/api/v1/tariffs/{created['id']}",
            json={"valor_importado": "-1"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_an_empty_patch_changes_nothing(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        _, created = await _create(client, admin_headers)

        response = await client.patch(
            f"/api/v1/tariffs/{created['id']}", json={}, headers=admin_headers
        )

        assert response.json()["valor_importado"] == "780.5000"


class TestDelete:
    async def test_a_period_can_be_removed(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """The way to fix a tariff filed under the wrong month."""
        _, created = await _create(client, admin_headers)

        response = await client.delete(
            f"/api/v1/tariffs/{created['id']}", headers=admin_headers
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        listing = await client.get("/api/v1/tariffs", headers=admin_headers)
        assert listing.json()["total"] == 0

    async def test_the_month_becomes_available_again(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        _, created = await _create(client, admin_headers, mes="2026-01-01")
        await client.delete(f"/api/v1/tariffs/{created['id']}", headers=admin_headers)

        code, _ = await _create(client, admin_headers, mes="2026-01-01")

        assert code == status.HTTP_201_CREATED


class TestPermissions:
    async def test_a_cliente_is_refused_with_403_not_404(
        self, client: AsyncClient, cliente_headers: dict[str, str]
    ) -> None:
        """Tariffs are not another company's data being hidden."""
        response = await client.get("/api/v1/tariffs", headers=cliente_headers)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["error"]["code"] == "not_authorized"

    async def test_a_readonly_user_can_list(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        readonly_headers: dict[str, str],
    ) -> None:
        await _create(client, admin_headers)

        response = await client.get("/api/v1/tariffs", headers=readonly_headers)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["total"] == 1

    async def test_a_readonly_user_cannot_create(
        self, client: AsyncClient, readonly_headers: dict[str, str]
    ) -> None:
        code, _ = await _create(client, readonly_headers)

        assert code == status.HTTP_403_FORBIDDEN

    async def test_a_tecnico_can_read(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        tecnico_headers: dict[str, str],
    ) -> None:
        await _create(client, admin_headers)

        response = await client.get("/api/v1/tariffs", headers=tecnico_headers)

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/v1/tariffs"),
            ("patch", "/api/v1/tariffs/{}"),
            ("delete", "/api/v1/tariffs/{}"),
        ],
    )
    async def test_a_tecnico_cannot_write(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        tecnico_headers: dict[str, str],
        method: str,
        path: str,
    ) -> None:
        """Prices multiply consumption into money; that is not a tecnico's job."""
        _, created = await _create(client, admin_headers)

        response = await client.request(
            method,
            path.format(created["id"]),
            json={"mes": "2026-02-01", "valor_importado": "900"},
            headers=tecnico_headers,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.parametrize("method", ["get", "post"])
    async def test_an_anonymous_caller_gets_401(
        self, client: AsyncClient, method: str
    ) -> None:
        response = await client.request(
            method,
            "/api/v1/tariffs",
            json={"mes": "2026-01-01", "valor_importado": "780"},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestMissingTargets:
    @pytest.mark.parametrize("method", ["get", "patch", "delete"])
    async def test_an_unknown_id_is_404(
        self, client: AsyncClient, admin_headers: dict[str, str], method: str
    ) -> None:
        response = await client.request(
            method, f"/api/v1/tariffs/{uuid.uuid4()}", json={}, headers=admin_headers
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_a_malformed_id_is_422(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.get("/api/v1/tariffs/not-a-uuid", headers=admin_headers)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestPrecision:
    async def test_the_decimal_scale_survives_a_round_trip(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """These values multiply consumption; a float would drift."""
        _, created = await _create(client, admin_headers, valor_importado="780.1234")

        response = await client.get(
            f"/api/v1/tariffs/{created['id']}", headers=admin_headers
        )

        assert response.json()["valor_importado"] == "780.1234"

    async def test_prices_are_serialised_as_strings(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        _, body = await _create(client, admin_headers)

        assert isinstance(body["valor_importado"], str)
        assert isinstance(body["valor_excedente"], str)


class TestHistoryIsKept:
    async def test_successive_months_coexist(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """A new price never overwrites the previous period."""
        for mes, precio in (
            ("2025-12-01", "700"),
            ("2026-01-01", "780"),
            ("2026-02-01", "810"),
        ):
            await _create(client, admin_headers, mes=mes, valor_importado=precio)

        response = await client.get("/api/v1/tariffs", headers=admin_headers)

        assert response.json()["total"] == 3


def test_the_month_helper_snaps_to_the_first_day() -> None:
    from app.schemas.tariff import _first_of_month

    assert _first_of_month(date(2026, 5, 31)) == date(2026, 5, 1)
    assert _first_of_month(date(2026, 5, 1)) == date(2026, 5, 1)


def test_a_password_hash_is_available_for_fixtures() -> None:
    """Guards the shared hash the role fixtures rely on."""
    assert TEST_PASSWORD_HASH.startswith("$2b$")
    assert hash_password("x" * 10) != TEST_PASSWORD_HASH
