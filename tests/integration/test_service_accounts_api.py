"""Machine-to-machine credentials, end to end.

Two surfaces: the panel that issues them and the token endpoint another system
calls. The assertions that matter most are the refusals — this is the one
credential that lives outside the company's own systems.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import status
from httpx import AsyncClient

from app.models import User
from tests.conftest import auth_header

type Login = Callable[..., Awaitable[str]]

ACCOUNTS = "/api/v1/service-accounts"
TOKEN = "/api/v1/service/token"
FLEET = "/api/v1/fleet"
TARIFFS = "/api/v1/tariffs"


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


@pytest.fixture
async def tecnico_headers(tecnico_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(tecnico_user.email))


async def _create_account(
    client: AsyncClient, headers: dict[str, str], **overrides: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "nombre": f"ApiEMS {uuid.uuid4().hex[:6]}",
        "permisos": ["tariffs:read", "fleet:read"],
        **overrides,
    }
    response = await client.post(ACCOUNTS, json=body, headers=headers)
    assert response.status_code == status.HTTP_201_CREATED, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def _service_token(client: AsyncClient, created: dict[str, Any]) -> str:
    response = await client.post(
        TOKEN,
        json={
            "client_id": created["credencial_id"],
            "client_secret": created["client_secret"],
        },
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    token: str = response.json()["access_token"]
    return token


class TestIssuingOne:
    async def test_the_secret_is_returned_once(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await _create_account(client, admin_headers)

        assert created["client_secret"].startswith("svcsec_")
        assert created["credencial_id"].startswith("svc_")

    async def test_the_secret_never_appears_again(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Not in the detail view, not in the listing, not anywhere."""
        created = await _create_account(client, admin_headers)

        detail = await client.get(f"{ACCOUNTS}/{created['id']}", headers=admin_headers)
        listing = await client.get(ACCOUNTS, headers=admin_headers)

        assert "client_secret" not in detail.json()
        assert created["client_secret"] not in detail.text
        assert created["client_secret"] not in listing.text

    async def test_the_stored_secret_is_not_the_secret(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """A database dump must not be enough to impersonate the consumer."""
        created = await _create_account(client, admin_headers)

        detail = await client.get(f"{ACCOUNTS}/{created['id']}", headers=admin_headers)

        assert "secret_hash" not in detail.json()

    async def test_it_starts_active_and_unused(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await _create_account(client, admin_headers)

        assert created["activo"] is True
        assert created["ultimo_uso_en"] is None

    async def test_a_credential_that_reads_nothing_is_refused(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """A secret with no purpose still has to be rotated and leaked."""
        response = await client.post(
            ACCOUNTS,
            json={"nombre": "Vacia", "permisos": []},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_an_invented_permission_is_refused(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            ACCOUNTS,
            json={"nombre": "Rara", "permisos": ["fleet:write"]},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_the_name_is_unique(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        await _create_account(client, admin_headers, nombre="ApiEMS")

        response = await client.post(
            ACCOUNTS,
            json={"nombre": "ApiEMS", "permisos": ["tariffs:read"]},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    async def test_an_expiry_in_the_past_is_refused(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """It could never mint a token, so it is a mistake, not a choice."""
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()

        response = await client.post(
            ACCOUNTS,
            json={"nombre": "Vencida", "permisos": ["tariffs:read"], "expira_en": past},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_an_unknown_client_is_refused(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            ACCOUNTS,
            json={
                "nombre": "Fantasma",
                "permisos": ["fleet:read"],
                "client_id": str(uuid.uuid4()),
            },
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_two_credentials_never_collide(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        first = await _create_account(client, admin_headers)
        second = await _create_account(client, admin_headers)

        assert first["credencial_id"] != second["credencial_id"]
        assert first["client_secret"] != second["client_secret"]


class TestOnlyAdministratorsManageThem:
    async def test_a_tecnico_cannot_issue_one(
        self, client: AsyncClient, tecnico_headers: dict[str, str]
    ) -> None:
        """Narrower than writing: a credential outlives whoever created it."""
        response = await client.post(
            ACCOUNTS,
            json={"nombre": "Intento", "permisos": ["fleet:read"]},
            headers=tecnico_headers,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_a_tecnico_cannot_even_list_them(
        self, client: AsyncClient, tecnico_headers: dict[str, str]
    ) -> None:
        response = await client.get(ACCOUNTS, headers=tecnico_headers)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_an_anonymous_caller_is_refused(self, client: AsyncClient) -> None:
        response = await client.get(ACCOUNTS)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestExchangingItForAToken:
    async def test_the_credential_alone_is_the_authentication(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """No bearer header: the consumer has nothing else to present."""
        created = await _create_account(client, admin_headers)

        response = await client.post(
            TOKEN,
            json={
                "client_id": created["credencial_id"],
                "client_secret": created["client_secret"],
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["token_type"] == "bearer"
        assert response.json()["expires_in"] == 3600

    async def test_it_echoes_what_was_granted(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await _create_account(
            client, admin_headers, permisos=["tariffs:read"]
        )

        response = await client.post(
            TOKEN,
            json={
                "client_id": created["credencial_id"],
                "client_secret": created["client_secret"],
            },
        )

        assert response.json()["permisos"] == ["tariffs:read"]
        assert response.json()["scope_client_id"] is None

    @pytest.mark.parametrize(
        ("field", "value"),
        [("client_id", "svc_no-existe"), ("client_secret", "svcsec_incorrecto")],
    )
    async def test_every_failure_looks_identical(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        field: str,
        value: str,
    ) -> None:
        """Telling them apart would confirm which identifiers are live."""
        created = await _create_account(client, admin_headers)
        body = {
            "client_id": created["credencial_id"],
            "client_secret": created["client_secret"],
            field: value,
        }

        response = await client.post(TOKEN, json=body)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["error"]["code"] == "authentication_failed"

    async def test_a_deactivated_account_stops_getting_tokens(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await _create_account(client, admin_headers)
        await client.patch(
            f"{ACCOUNTS}/{created['id']}",
            json={"activo": False},
            headers=admin_headers,
        )

        response = await client.post(
            TOKEN,
            json={
                "client_id": created["credencial_id"],
                "client_secret": created["client_secret"],
            },
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_using_it_records_when(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await _create_account(client, admin_headers)

        await _service_token(client, created)

        detail = await client.get(f"{ACCOUNTS}/{created['id']}", headers=admin_headers)
        assert detail.json()["ultimo_uso_en"] is not None


class TestRotation:
    async def test_the_old_secret_stops_working_at_once(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await _create_account(client, admin_headers)

        rotated = await client.post(
            f"{ACCOUNTS}/{created['id']}/secret", headers=admin_headers
        )

        stale = await client.post(
            TOKEN,
            json={
                "client_id": created["credencial_id"],
                "client_secret": created["client_secret"],
            },
        )
        assert stale.status_code == status.HTTP_401_UNAUTHORIZED
        assert rotated.json()["client_secret"] != created["client_secret"]

    async def test_the_new_one_works(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await _create_account(client, admin_headers)

        rotated = await client.post(
            f"{ACCOUNTS}/{created['id']}/secret", headers=admin_headers
        )

        assert await _service_token(client, rotated.json())

    async def test_the_identifier_survives_rotation(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Only the secret changes, so the consumer's config keeps one line."""
        created = await _create_account(client, admin_headers)

        rotated = await client.post(
            f"{ACCOUNTS}/{created['id']}/secret", headers=admin_headers
        )

        assert rotated.json()["credencial_id"] == created["credencial_id"]

    async def test_a_tecnico_cannot_rotate(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        tecnico_headers: dict[str, str],
    ) -> None:
        created = await _create_account(client, admin_headers)

        response = await client.post(
            f"{ACCOUNTS}/{created['id']}/secret", headers=tecnico_headers
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestRevoking:
    async def test_deleting_it_ends_the_credential(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await _create_account(client, admin_headers)

        deleted = await client.delete(
            f"{ACCOUNTS}/{created['id']}", headers=admin_headers
        )

        assert deleted.status_code == status.HTTP_204_NO_CONTENT
        refused = await client.post(
            TOKEN,
            json={
                "client_id": created["credencial_id"],
                "client_secret": created["client_secret"],
            },
        )
        assert refused.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_a_token_dies_when_the_account_is_deactivated(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Permissions are re-read from the row, not trusted from the token."""
        created = await _create_account(client, admin_headers)
        token = await _service_token(client, created)

        await client.patch(
            f"{ACCOUNTS}/{created['id']}",
            json={"activo": False},
            headers=admin_headers,
        )

        response = await client.get(FLEET, headers=auth_header(token))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_narrowing_takes_effect_on_the_next_request(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await _create_account(client, admin_headers)
        token = await _service_token(client, created)

        await client.patch(
            f"{ACCOUNTS}/{created['id']}",
            json={"permisos": ["tariffs:read"]},
            headers=admin_headers,
        )

        response = await client.get(FLEET, headers=auth_header(token))
        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestExpiry:
    async def test_a_deadline_is_honoured(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Past the date the credential stops minting tokens by itself.

        Set through the API as a future date, then moved into the past by
        patching the row — the point is the comparison, not the clock.
        """
        soon = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        created = await _create_account(client, admin_headers, expira_en=soon)

        assert await _service_token(client, created)

    async def test_the_deadline_can_be_lifted(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Clearing it is how a credential is given an extension."""
        soon = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        created = await _create_account(client, admin_headers, expira_en=soon)

        response = await client.patch(
            f"{ACCOUNTS}/{created['id']}",
            json={"expira_en": None},
            headers=admin_headers,
        )

        assert response.json()["expira_en"] is None

    async def test_moving_it_into_the_past_is_refused(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """A credential nobody can use is a mistake, not an expression of intent."""
        created = await _create_account(client, admin_headers)
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()

        response = await client.patch(
            f"{ACCOUNTS}/{created['id']}",
            json={"expira_en": past},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestEditing:
    async def test_the_name_stays_unique(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        await _create_account(client, admin_headers, nombre="Primero")
        second = await _create_account(client, admin_headers, nombre="Segundo")

        response = await client.patch(
            f"{ACCOUNTS}/{second['id']}",
            json={"nombre": "Primero"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    async def test_keeping_its_own_name_is_not_a_conflict(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await _create_account(client, admin_headers, nombre="Estable")

        response = await client.patch(
            f"{ACCOUNTS}/{created['id']}",
            json={"nombre": "Estable", "descripcion": "otra cosa"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK

    async def test_widening_does_not_rotate_the_secret(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """Granting one more permission should not force a redeployment."""
        created = await _create_account(
            client, admin_headers, permisos=["tariffs:read"]
        )

        await client.patch(
            f"{ACCOUNTS}/{created['id']}",
            json={"permisos": ["tariffs:read", "fleet:read"]},
            headers=admin_headers,
        )

        assert await _service_token(client, created)

    async def test_an_unknown_account_is_a_404(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.get(f"{ACCOUNTS}/{uuid.uuid4()}", headers=admin_headers)

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestTheTokenIsNotProofOnItsOwn:
    async def test_a_deleted_account_invalidates_its_token(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """The row is the authority; the token only points at it."""
        created = await _create_account(client, admin_headers)
        token = await _service_token(client, created)

        await client.delete(f"{ACCOUNTS}/{created['id']}", headers=admin_headers)

        response = await client.get(FLEET, headers=auth_header(token))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_a_token_for_a_client_that_is_gone_stops_working(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """A pinned credential dies with the company it was issued for."""
        empresa = await client.post(
            "/api/v1/clients",
            json={"nombre_empresa": "Empresa Efimera"},
            headers=admin_headers,
        )
        created = await _create_account(
            client,
            admin_headers,
            permisos=["fleet:read"],
            client_id=empresa.json()["id"],
        )

        assert created["client_id"] == empresa.json()["id"]
        token = await _service_token(client, created)
        assert (
            await client.get(FLEET, headers=auth_header(token))
        ).status_code == status.HTTP_200_OK
