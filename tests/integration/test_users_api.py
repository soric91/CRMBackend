"""Account management endpoints."""

import uuid
from collections.abc import Awaitable, Callable

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Client, User
from tests.conftest import TEST_PASSWORD, auth_header

type Login = Callable[..., Awaitable[str]]

NEW_PASSWORD = "una-clave-nueva-larga"


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


@pytest.fixture
async def tecnico_headers(tecnico_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(tecnico_user.email))


@pytest.fixture
async def a_client(db_session: AsyncSession) -> Client:
    client = Client(
        nombre_empresa="Industrias Andinas",
        contacto_email="operador@empresa.com",
    )
    db_session.add(client)
    await db_session.flush()
    return client


class TestCreate:
    async def test_an_admin_creates_a_client_account(
        self, client: AsyncClient, admin_headers: dict[str, str], a_client: Client
    ) -> None:
        """This is what gives a company access to its consumption page."""
        response = await client.post(
            "/api/v1/users",
            json={
                "email": "operador@empresa.com",
                "password": NEW_PASSWORD,
                "role": "cliente",
                "client_id": str(a_client.id),
            },
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["role"] == "cliente"
        assert body["client_id"] == str(a_client.id)
        assert body["is_active"] is True

    async def test_the_new_account_can_log_in(
        self, client: AsyncClient, admin_headers: dict[str, str], a_client: Client
    ) -> None:
        await client.post(
            "/api/v1/users",
            json={
                "email": "operador@empresa.com",
                "password": NEW_PASSWORD,
                "role": "cliente",
                "client_id": str(a_client.id),
            },
            headers=admin_headers,
        )

        # A client belongs to the monitoring web, not to the CRM.
        crm = await client.post(
            "/api/v1/auth/login",
            json={"email": "operador@empresa.com", "password": NEW_PASSWORD},
        )
        monitor = await client.post(
            "/api/v1/auth-monitor/login",
            json={"email": "operador@empresa.com", "password": NEW_PASSWORD},
        )

        assert crm.status_code == status.HTTP_401_UNAUTHORIZED
        assert monitor.status_code == status.HTTP_200_OK
        assert monitor.json()["client_id"] == str(a_client.id)

    async def test_the_password_never_comes_back(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/users",
            json={
                "email": "tec2@example.com",
                "password": NEW_PASSWORD,
                "role": "tecnico",
            },
            headers=admin_headers,
        )

        assert NEW_PASSWORD not in response.text
        assert "password" not in response.json()
        assert "$2b$" not in response.text

    async def test_the_email_is_stored_lowercase(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/users",
            json={
                "email": "Mixed@Example.COM",
                "password": NEW_PASSWORD,
                "role": "tecnico",
            },
            headers=admin_headers,
        )

        assert response.json()["email"] == "mixed@example.com"

    async def test_a_duplicate_email_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], admin_user: User
    ) -> None:
        response = await client.post(
            "/api/v1/users",
            json={
                "email": admin_user.email.upper(),
                "password": NEW_PASSWORD,
                "role": "tecnico",
            },
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    @pytest.mark.parametrize(
        "payload",
        [
            {"email": "no-an-email", "password": "una-clave-larga", "role": "tecnico"},
            {"email": "a@b.com", "password": "corta", "role": "tecnico"},
            {"email": "a@b.com", "password": "una-clave-larga", "role": "superadmin"},
            {"email": "a@b.com", "password": "una-clave-larga"},
        ],
    )
    async def test_malformed_payloads_are_rejected(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        payload: dict[str, str],
    ) -> None:
        response = await client.post(
            "/api/v1/users", json=payload, headers=admin_headers
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestRoleAndClientBinding:
    async def test_a_cliente_without_a_client_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/users",
            json={
                "email": "huerfano@example.com",
                "password": NEW_PASSWORD,
                "role": "cliente",
            },
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "client_id" in response.json()["error"]["message"]

    async def test_a_cliente_pointing_at_a_missing_client_is_404(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/users",
            json={
                "email": "fantasma@example.com",
                "password": NEW_PASSWORD,
                "role": "cliente",
                "client_id": str(uuid.uuid4()),
            },
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("role", ["admin", "tecnico", "solo_lectura"])
    async def test_a_staff_account_bound_to_a_client_is_rejected(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        a_client: Client,
        role: str,
    ) -> None:
        """Otherwise an internal account would look scoped without being so."""
        response = await client.post(
            "/api/v1/users",
            json={
                "email": f"nuevo-{role}@example.com",
                "password": NEW_PASSWORD,
                "role": role,
                "client_id": str(a_client.id),
            },
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_promoting_a_cliente_to_staff_drops_its_client(
        self, client: AsyncClient, admin_headers: dict[str, str], a_client: Client
    ) -> None:
        created = await client.post(
            "/api/v1/users",
            json={
                "email": "sube@example.com",
                "password": NEW_PASSWORD,
                "role": "cliente",
                "client_id": str(a_client.id),
            },
            headers=admin_headers,
        )

        response = await client.patch(
            f"/api/v1/users/{created.json()['id']}",
            json={"role": "tecnico"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["client_id"] is None

    async def test_demoting_staff_to_cliente_needs_a_client(
        self, client: AsyncClient, admin_headers: dict[str, str], tecnico_user: User
    ) -> None:
        response = await client.patch(
            f"/api/v1/users/{tecnico_user.id}",
            json={"role": "cliente"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_demoting_staff_to_cliente_with_a_client_works(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        tecnico_user: User,
        a_client: Client,
    ) -> None:
        response = await client.patch(
            f"/api/v1/users/{tecnico_user.id}",
            json={"role": "cliente", "client_id": str(a_client.id)},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["client_id"] == str(a_client.id)


class TestOnlyAdminsManageAccounts:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/v1/users"),
            ("post", "/api/v1/users"),
            ("get", "/api/v1/users/{}"),
            ("patch", "/api/v1/users/{}"),
            ("delete", "/api/v1/users/{}"),
            ("post", "/api/v1/users/{}/password"),
        ],
    )
    async def test_a_tecnico_is_forbidden_everywhere(
        self,
        client: AsyncClient,
        tecnico_headers: dict[str, str],
        admin_user: User,
        method: str,
        path: str,
    ) -> None:
        """A tecnico able to create users could mint an admin and promote itself."""
        response = await client.request(
            method,
            path.format(admin_user.id),
            json={
                "email": "x@example.com",
                "password": NEW_PASSWORD,
                "role": "admin",
                "new_password": NEW_PASSWORD,
            },
            headers=tecnico_headers,
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_a_cliente_is_forbidden(
        self, client: AsyncClient, cliente_user: User, authenticate_monitor: Login
    ) -> None:
        headers = auth_header(await authenticate_monitor(cliente_user.email))

        response = await client.get("/api/v1/users", headers=headers)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_an_anonymous_caller_gets_401(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/users")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestListing:
    async def test_it_lists_every_account(
        self, client: AsyncClient, admin_headers: dict[str, str], tecnico_user: User
    ) -> None:
        response = await client.get("/api/v1/users", headers=admin_headers)

        assert response.json()["total"] == 2

    async def test_it_can_be_narrowed_to_one_role(
        self, client: AsyncClient, admin_headers: dict[str, str], tecnico_user: User
    ) -> None:
        response = await client.get("/api/v1/users?role=tecnico", headers=admin_headers)

        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["role"] == "tecnico"

    async def test_it_can_be_narrowed_to_one_client(
        self, client: AsyncClient, admin_headers: dict[str, str], cliente_user: User
    ) -> None:
        response = await client.get(
            f"/api/v1/users?client_id={cliente_user.client_id}", headers=admin_headers
        )

        assert response.json()["total"] == 1

    async def test_no_listing_ever_carries_a_hash(
        self, client: AsyncClient, admin_headers: dict[str, str], tecnico_user: User
    ) -> None:
        response = await client.get("/api/v1/users", headers=admin_headers)

        assert "$2b$" not in response.text
        assert "password" not in response.text


class TestDeactivation:
    async def test_a_deactivated_account_cannot_log_in(
        self, client: AsyncClient, admin_headers: dict[str, str], tecnico_user: User
    ) -> None:
        await client.patch(
            f"/api/v1/users/{tecnico_user.id}",
            json={"is_active": False},
            headers=admin_headers,
        )

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": tecnico_user.email, "password": TEST_PASSWORD},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_it_can_be_reactivated(
        self, client: AsyncClient, admin_headers: dict[str, str], tecnico_user: User
    ) -> None:
        await client.patch(
            f"/api/v1/users/{tecnico_user.id}",
            json={"is_active": False},
            headers=admin_headers,
        )
        await client.patch(
            f"/api/v1/users/{tecnico_user.id}",
            json={"is_active": True},
            headers=admin_headers,
        )

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": tecnico_user.email, "password": TEST_PASSWORD},
        )

        assert response.status_code == status.HTTP_200_OK


class TestSelfLockout:
    async def test_an_admin_cannot_deactivate_itself(
        self, client: AsyncClient, admin_headers: dict[str, str], admin_user: User
    ) -> None:
        response = await client.patch(
            f"/api/v1/users/{admin_user.id}",
            json={"is_active": False},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_an_admin_cannot_delete_itself(
        self, client: AsyncClient, admin_headers: dict[str, str], admin_user: User
    ) -> None:
        response = await client.delete(
            f"/api/v1/users/{admin_user.id}", headers=admin_headers
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_the_last_admin_cannot_be_demoted(
        self, client: AsyncClient, admin_headers: dict[str, str], admin_user: User
    ) -> None:
        """Losing every administrator would make the platform unmanageable."""
        response = await client.patch(
            f"/api/v1/users/{admin_user.id}",
            json={"role": "tecnico"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_a_second_admin_makes_the_first_removable(
        self, client: AsyncClient, admin_headers: dict[str, str], admin_user: User
    ) -> None:
        created = await client.post(
            "/api/v1/users",
            json={
                "email": "segundo@example.com",
                "password": NEW_PASSWORD,
                "role": "admin",
            },
            headers=admin_headers,
        )

        response = await client.delete(
            f"/api/v1/users/{created.json()['id']}", headers=admin_headers
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT


class TestPasswordReset:
    async def test_an_admin_can_set_someone_elses_password(
        self, client: AsyncClient, admin_headers: dict[str, str], tecnico_user: User
    ) -> None:
        response = await client.post(
            f"/api/v1/users/{tecnico_user.id}/password",
            json={"new_password": NEW_PASSWORD},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": tecnico_user.email, "password": NEW_PASSWORD},
        )
        assert login.status_code == status.HTTP_200_OK

    async def test_the_old_password_stops_working(
        self, client: AsyncClient, admin_headers: dict[str, str], tecnico_user: User
    ) -> None:
        await client.post(
            f"/api/v1/users/{tecnico_user.id}/password",
            json={"new_password": NEW_PASSWORD},
            headers=admin_headers,
        )

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": tecnico_user.email, "password": TEST_PASSWORD},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_a_short_password_is_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], tecnico_user: User
    ) -> None:
        response = await client.post(
            f"/api/v1/users/{tecnico_user.id}/password",
            json={"new_password": "corta"},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestChangeOwnPassword:
    async def test_a_staff_user_can_rotate_its_own_password(
        self, client: AsyncClient, tecnico_user: User, authenticate: Login
    ) -> None:
        headers = auth_header(await authenticate(tecnico_user.email))

        response = await client.post(
            "/api/v1/auth/password",
            json={
                "current_password": TEST_PASSWORD,
                "new_password": NEW_PASSWORD,
            },
            headers=headers,
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": tecnico_user.email, "password": NEW_PASSWORD},
        )
        assert login.status_code == status.HTTP_200_OK

    async def test_the_current_password_is_required(
        self, client: AsyncClient, tecnico_user: User, authenticate: Login
    ) -> None:
        """A stolen token alone must not be enough to take the account over."""
        headers = auth_header(await authenticate(tecnico_user.email))

        response = await client.post(
            "/api/v1/auth/password",
            json={
                "current_password": "no-es-la-actual",
                "new_password": NEW_PASSWORD,
            },
            headers=headers,
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_it_needs_authentication(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/password",
            json={
                "current_password": TEST_PASSWORD,
                "new_password": NEW_PASSWORD,
            },
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestMissingTargets:
    async def test_getting_an_unknown_user_is_404(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.get(
            f"/api/v1/users/{uuid.uuid4()}", headers=admin_headers
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["patch", "delete"])
    async def test_changing_an_unknown_user_is_404(
        self, client: AsyncClient, admin_headers: dict[str, str], method: str
    ) -> None:
        response = await client.request(
            method, f"/api/v1/users/{uuid.uuid4()}", json={}, headers=admin_headers
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_resetting_an_unknown_users_password_is_404(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            f"/api/v1/users/{uuid.uuid4()}/password",
            json={"new_password": NEW_PASSWORD},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestConsumptionFlow:
    async def test_a_client_reaches_its_consumption_flag_through_the_monitor(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        a_client: Client,
    ) -> None:
        """The whole point of v1, end to end and through the right surface."""
        await client.patch(
            f"/api/v1/clients/{a_client.id}",
            json={"puede_ver_consumo": True},
            headers=admin_headers,
        )
        granted = await client.post(
            f"/api/v1/clients/{a_client.id}/monitor-access", headers=admin_headers
        )
        temporary = granted.json()["temporary_password"]

        login = await client.post(
            "/api/v1/auth-monitor/login",
            json={"email": granted.json()["email"], "password": temporary},
        )
        assert login.json()["client_id"] == str(a_client.id)
        assert login.json()["must_change_password"] is True

        # The password came from an administrator, so it has to be replaced
        # before the token reaches anything else.
        changed = await client.post(
            "/api/v1/auth-monitor/password",
            json={
                "current_password": temporary,
                "new_password": "la-clave-que-elige-el-cliente",
            },
            headers=auth_header(login.json()["access_token"]),
        )
        assert changed.status_code == status.HTTP_200_OK

        own = await client.get(
            f"/api/v1/clients/{a_client.id}",
            headers=auth_header(changed.json()["access_token"]),
        )

        assert own.status_code == status.HTTP_200_OK
        assert own.json()["puede_ver_consumo"] is True
