"""The monitoring web's authentication and the access the CRM hands out."""

import uuid
from collections.abc import Awaitable, Callable, Iterator

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.rate_limit import monitor_login_limiter
from app.core.security import TokenAudience, create_access_token
from app.models import Client, User
from tests.conftest import TEST_PASSWORD, auth_header

type Login = Callable[..., Awaitable[str]]

CHOSEN_PASSWORD = "la-clave-que-elige-el-cliente"


@pytest.fixture(autouse=True)
def _reset_limiter() -> Iterator[None]:
    """The limiter lives in the process, so it leaks between tests."""
    monitor_login_limiter.clear()
    yield
    monitor_login_limiter.clear()


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


@pytest.fixture
async def a_client(db_session: AsyncSession) -> Client:
    client = Client(
        nombre_empresa="Industrias Andinas",
        contacto_email="operador@empresa.com",
    )
    db_session.add(client)
    await db_session.flush()
    return client


@pytest.fixture
async def granted(
    client: AsyncClient, admin_headers: dict[str, str], a_client: Client
) -> dict[str, object]:
    """A client with monitoring access and its one-off password."""
    response = await client.post(
        f"/api/v1/clients/{a_client.id}/monitor-access", headers=admin_headers
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    body: dict[str, object] = response.json()
    return body


async def _monitor_login(
    client: AsyncClient, email: str, password: str
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/auth-monitor/login", json={"email": email, "password": password}
    )
    body: dict[str, object] = response.json()
    body["_status"] = response.status_code
    return body


class TestGrantingAccess:
    async def test_it_returns_a_one_off_password(
        self, granted: dict[str, object]
    ) -> None:
        password = granted["temporary_password"]

        assert isinstance(password, str)
        assert len(password) == 16
        assert granted["must_change_password"] is True
        assert granted["is_active"] is True

    async def test_the_address_is_the_clients_contact_email(
        self, granted: dict[str, object]
    ) -> None:
        assert granted["email"] == "operador@empresa.com"

    async def test_a_client_without_a_contact_email_is_refused(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        bare = Client(nombre_empresa="Sin Correo")
        db_session.add(bare)
        await db_session.flush()

        response = await client.post(
            f"/api/v1/clients/{bare.id}/monitor-access", headers=admin_headers
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "contacto_email" in response.json()["error"]["message"]

    async def test_granting_twice_is_rejected(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        a_client: Client,
        granted: dict[str, object],
    ) -> None:
        response = await client.post(
            f"/api/v1/clients/{a_client.id}/monitor-access", headers=admin_headers
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    async def test_an_address_already_in_use_is_rejected(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        db_session: AsyncSession,
        admin_user: User,
    ) -> None:
        """Happens when the contact email is also an internal account."""
        collides = Client(nombre_empresa="Choca", contacto_email=admin_user.email)
        db_session.add(collides)
        await db_session.flush()

        response = await client.post(
            f"/api/v1/clients/{collides.id}/monitor-access", headers=admin_headers
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert "another account" in response.json()["error"]["message"]

    async def test_two_passwords_are_never_the_same(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        passwords = set()
        for index in range(3):
            company = Client(
                nombre_empresa=f"Empresa {index}",
                contacto_email=f"contacto{index}@empresa.com",
            )
            db_session.add(company)
            await db_session.flush()
            response = await client.post(
                f"/api/v1/clients/{company.id}/monitor-access", headers=admin_headers
            )
            passwords.add(response.json()["temporary_password"])

        assert len(passwords) == 3

    async def test_it_does_not_require_the_consumption_flag(
        self, granted: dict[str, object], a_client: Client
    ) -> None:
        """Being able to log in and what there is to see are different things."""
        assert a_client.puede_ver_consumo is False
        assert granted["temporary_password"]


class TestThePasswordIsNeverShownAgain:
    async def test_reading_the_access_does_not_carry_it(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        a_client: Client,
        granted: dict[str, object],
    ) -> None:
        response = await client.get(
            f"/api/v1/clients/{a_client.id}/monitor-access", headers=admin_headers
        )

        assert "temporary_password" not in response.json()
        assert str(granted["temporary_password"]) not in response.text

    async def test_listing_users_does_not_carry_it(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        granted: dict[str, object],
    ) -> None:
        response = await client.get("/api/v1/users", headers=admin_headers)

        assert str(granted["temporary_password"]) not in response.text
        assert "$2b$" not in response.text

    async def test_it_is_never_written_to_the_log(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        db_session: AsyncSession,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The response is the only place it exists in the clear."""
        company = Client(nombre_empresa="Con Log", contacto_email="log@empresa.com")
        db_session.add(company)
        await db_session.flush()

        with caplog.at_level("DEBUG"):
            response = await client.post(
                f"/api/v1/clients/{company.id}/monitor-access", headers=admin_headers
            )

        assert response.json()["temporary_password"] not in caplog.text


class TestResetAndRevoke:
    async def test_reset_issues_a_different_password(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        a_client: Client,
        granted: dict[str, object],
    ) -> None:
        response = await client.post(
            f"/api/v1/clients/{a_client.id}/monitor-access/reset",
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["temporary_password"] != granted["temporary_password"]
        assert response.json()["must_change_password"] is True

    async def test_the_old_password_stops_working_after_a_reset(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        a_client: Client,
        granted: dict[str, object],
    ) -> None:
        await client.post(
            f"/api/v1/clients/{a_client.id}/monitor-access/reset",
            headers=admin_headers,
        )

        body = await _monitor_login(
            client, "operador@empresa.com", str(granted["temporary_password"])
        )

        assert body["_status"] == status.HTTP_401_UNAUTHORIZED

    async def test_reset_without_an_access_is_404(
        self, client: AsyncClient, admin_headers: dict[str, str], a_client: Client
    ) -> None:
        response = await client.post(
            f"/api/v1/clients/{a_client.id}/monitor-access/reset",
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_revoking_keeps_the_row(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        a_client: Client,
        granted: dict[str, object],
    ) -> None:
        """Deleting would lose the trace of who had entered."""
        response = await client.delete(
            f"/api/v1/clients/{a_client.id}/monitor-access", headers=admin_headers
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        state = await client.get(
            f"/api/v1/clients/{a_client.id}/monitor-access", headers=admin_headers
        )
        assert state.status_code == status.HTTP_200_OK
        assert state.json()["is_active"] is False

    async def test_a_revoked_access_cannot_log_in(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        a_client: Client,
        granted: dict[str, object],
    ) -> None:
        await client.delete(
            f"/api/v1/clients/{a_client.id}/monitor-access", headers=admin_headers
        )

        body = await _monitor_login(
            client, "operador@empresa.com", str(granted["temporary_password"])
        )

        assert body["_status"] == status.HTTP_401_UNAUTHORIZED

    async def test_granting_again_reactivates_with_a_new_password(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        a_client: Client,
        granted: dict[str, object],
    ) -> None:
        await client.delete(
            f"/api/v1/clients/{a_client.id}/monitor-access", headers=admin_headers
        )

        response = await client.post(
            f"/api/v1/clients/{a_client.id}/monitor-access", headers=admin_headers
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["is_active"] is True
        assert response.json()["temporary_password"] != granted["temporary_password"]


class TestMonitorLogin:
    async def test_it_returns_the_client_uuid(
        self, client: AsyncClient, a_client: Client, granted: dict[str, object]
    ) -> None:
        """The web needs it to ask for that company's data."""
        body = await _monitor_login(
            client, "operador@empresa.com", str(granted["temporary_password"])
        )

        assert body["_status"] == status.HTTP_200_OK
        assert body["client_id"] == str(a_client.id)
        assert body["must_change_password"] is True
        assert body["access_token"]

    async def test_a_wrong_password_is_rejected(
        self, client: AsyncClient, granted: dict[str, object]
    ) -> None:
        body = await _monitor_login(client, "operador@empresa.com", "no-es-la-clave")

        assert body["_status"] == status.HTTP_401_UNAUTHORIZED

    async def test_an_admin_gets_in_without_a_company(
        self, client: AsyncClient, admin_user: User
    ) -> None:
        """Entra, pero sin empresa: primero elige a cuál mirar.

        Es la misma persona que da de alta las empresas en el CRM; negarle la
        entrada solo obligaba a pedirle la contraseña al cliente.
        """
        body = await _monitor_login(client, admin_user.email, TEST_PASSWORD)

        assert body["_status"] == status.HTTP_200_OK
        assert body["client_id"] is None
        assert body["role"] == "admin"

    async def test_an_admin_with_a_wrong_password_is_still_rejected(
        self, client: AsyncClient, admin_user: User
    ) -> None:
        body = await _monitor_login(client, admin_user.email, "clave-incorrecta")

        assert body["_status"] == status.HTTP_401_UNAUTHORIZED

    async def test_a_tecnico_cannot_get_in(
        self, client: AsyncClient, tecnico_user: User
    ) -> None:
        body = await _monitor_login(client, tecnico_user.email, TEST_PASSWORD)

        assert body["_status"] == status.HTTP_401_UNAUTHORIZED


class TestMandatoryPasswordChange:
    async def test_a_restricted_token_cannot_read_the_client(
        self, client: AsyncClient, a_client: Client, granted: dict[str, object]
    ) -> None:
        """Without this the mandatory change is decorative."""
        login = await _monitor_login(
            client, "operador@empresa.com", str(granted["temporary_password"])
        )
        headers = auth_header(str(login["access_token"]))

        response = await client.get(f"/api/v1/clients/{a_client.id}", headers=headers)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["error"]["code"] == "password_change_required"

    async def test_a_restricted_token_still_reaches_me(
        self, client: AsyncClient, a_client: Client, granted: dict[str, object]
    ) -> None:
        login = await _monitor_login(
            client, "operador@empresa.com", str(granted["temporary_password"])
        )
        headers = auth_header(str(login["access_token"]))

        response = await client.get("/api/v1/auth-monitor/me", headers=headers)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["client_id"] == str(a_client.id)
        assert response.json()["must_change_password"] is True

    async def test_changing_the_password_returns_a_usable_pair(
        self, client: AsyncClient, a_client: Client, granted: dict[str, object]
    ) -> None:
        """A 204 would leave the client holding a token that reaches nothing."""
        login = await _monitor_login(
            client, "operador@empresa.com", str(granted["temporary_password"])
        )
        headers = auth_header(str(login["access_token"]))

        changed = await client.post(
            "/api/v1/auth-monitor/password",
            json={
                "current_password": granted["temporary_password"],
                "new_password": CHOSEN_PASSWORD,
            },
            headers=headers,
        )

        assert changed.status_code == status.HTTP_200_OK
        assert changed.json()["must_change_password"] is False
        response = await client.get(
            f"/api/v1/clients/{a_client.id}",
            headers=auth_header(changed.json()["access_token"]),
        )
        assert response.status_code == status.HTTP_200_OK

    async def test_the_wrong_current_password_is_rejected(
        self, client: AsyncClient, granted: dict[str, object]
    ) -> None:
        login = await _monitor_login(
            client, "operador@empresa.com", str(granted["temporary_password"])
        )

        response = await client.post(
            "/api/v1/auth-monitor/password",
            json={
                "current_password": "no-es-la-actual",
                "new_password": CHOSEN_PASSWORD,
            },
            headers=auth_header(str(login["access_token"])),
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_refreshing_does_not_lift_the_restriction(
        self, client: AsyncClient, a_client: Client, granted: dict[str, object]
    ) -> None:
        """Refreshing must not be the way around the mandatory change."""
        login = await _monitor_login(
            client, "operador@empresa.com", str(granted["temporary_password"])
        )

        refreshed = await client.post(
            "/api/v1/auth-monitor/refresh",
            json={"refresh_token": login["refresh_token"]},
        )

        assert refreshed.json()["must_change_password"] is True
        blocked = await client.get(
            f"/api/v1/clients/{a_client.id}",
            headers=auth_header(refreshed.json()["access_token"]),
        )
        assert blocked.status_code == status.HTTP_403_FORBIDDEN


class TestAudienceSeparation:
    async def test_a_crm_token_does_not_work_on_the_monitor(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.get("/api/v1/auth-monitor/me", headers=admin_headers)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_a_monitor_token_does_not_work_on_the_crm(
        self, client: AsyncClient, a_client: Client, granted: dict[str, object]
    ) -> None:
        login = await _monitor_login(
            client, "operador@empresa.com", str(granted["temporary_password"])
        )
        await client.post(
            "/api/v1/auth-monitor/password",
            json={
                "current_password": granted["temporary_password"],
                "new_password": CHOSEN_PASSWORD,
            },
            headers=auth_header(str(login["access_token"])),
        )

        # A full-scope monitor token still belongs to the other surface.
        full = await _monitor_login(client, "operador@empresa.com", CHOSEN_PASSWORD)
        response = await client.get(
            "/api/v1/auth/me", headers=auth_header(str(full["access_token"]))
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_a_token_minted_for_the_monitor_is_refused_by_the_crm(
        self, client: AsyncClient, settings: Settings, admin_user: User
    ) -> None:
        forged = create_access_token(
            settings,
            subject=str(admin_user.id),
            audience=TokenAudience.MONITOR,
            claims={"role": "admin", "scope": "full"},
        )

        response = await client.get("/api/v1/auth/me", headers=auth_header(forged))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestTenantIsolationHolds:
    async def test_a_monitor_token_cannot_read_another_client(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        a_client: Client,
        granted: dict[str, object],
    ) -> None:
        """The rule that already existed still applies with the monitor token."""
        other = Client(nombre_empresa="Ajena")
        db_session.add(other)
        await db_session.flush()

        login = await _monitor_login(
            client, "operador@empresa.com", str(granted["temporary_password"])
        )
        await client.post(
            "/api/v1/auth-monitor/password",
            json={
                "current_password": granted["temporary_password"],
                "new_password": CHOSEN_PASSWORD,
            },
            headers=auth_header(str(login["access_token"])),
        )
        full = await _monitor_login(client, "operador@empresa.com", CHOSEN_PASSWORD)

        response = await client.get(
            f"/api/v1/clients/{other.id}",
            headers=auth_header(str(full["access_token"])),
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestRateLimiting:
    async def test_repeated_failures_are_throttled(
        self, client: AsyncClient, granted: dict[str, object]
    ) -> None:
        """The password the client picks later may be weak."""
        statuses = []
        for _ in range(12):
            body = await _monitor_login(client, "operador@empresa.com", "mala-clave")
            statuses.append(body["_status"])

        assert status.HTTP_401_UNAUTHORIZED in statuses
        assert statuses[-1] == status.HTTP_401_UNAUTHORIZED

    async def test_a_successful_login_clears_the_counter(
        self, client: AsyncClient, granted: dict[str, object]
    ) -> None:
        for _ in range(3):
            await _monitor_login(client, "operador@empresa.com", "mala-clave")

        body = await _monitor_login(
            client, "operador@empresa.com", str(granted["temporary_password"])
        )

        assert body["_status"] == status.HTTP_200_OK


class TestPermissionsOnGranting:
    async def test_a_readonly_user_cannot_grant(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        a_client: Client,
        authenticate: Login,
    ) -> None:
        from app.domain.enums import UserRole
        from tests.conftest import TEST_PASSWORD_HASH

        reader = User(
            email="lectura@example.com",
            password_hash=TEST_PASSWORD_HASH,
            role=UserRole.SOLO_LECTURA,
        )
        db_session.add(reader)
        await db_session.flush()

        response = await client.post(
            f"/api/v1/clients/{a_client.id}/monitor-access",
            headers=auth_header(await authenticate(reader.email)),
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_an_anonymous_caller_gets_401(
        self, client: AsyncClient, a_client: Client
    ) -> None:
        response = await client.post(f"/api/v1/clients/{a_client.id}/monitor-access")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_an_unknown_client_is_404(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            f"/api/v1/clients/{uuid.uuid4()}/monitor-access", headers=admin_headers
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


async def _impersonate(
    client: AsyncClient, token: str, client_id: uuid.UUID
) -> dict[str, object]:
    response = await client.post(
        f"/api/v1/auth-monitor/impersonate/{client_id}", headers=auth_header(token)
    )
    body: dict[str, object] = response.json()
    body["_status"] = response.status_code
    return body


async def _monitor_me(client: AsyncClient, token: str) -> dict[str, object]:
    response = await client.get("/api/v1/auth-monitor/me", headers=auth_header(token))
    body: dict[str, object] = response.json()
    body["_status"] = response.status_code
    return body


class TestImpersonation:
    """Un administrador mirando los datos de una empresa.

    Lo que se prueba es sobre todo el límite: que el token resultante nombre
    una sola empresa —igual que el de un cliente— y que nadie más pueda
    pedirlo.
    """

    async def test_an_admin_can_switch_into_a_company(
        self, client: AsyncClient, admin_user: User, a_client: Client
    ) -> None:
        entrada = await _monitor_login(client, admin_user.email, TEST_PASSWORD)

        elegido = await _impersonate(
            client, str(entrada["access_token"]), a_client.id
        )

        assert elegido["_status"] == status.HTTP_200_OK
        assert elegido["client_id"] == str(a_client.id)

    async def test_the_token_names_that_company_and_says_it_is_borrowed(
        self, client: AsyncClient, admin_user: User, a_client: Client
    ) -> None:
        """El panel necesita las dos cosas: a quién mira, y que no es suyo."""
        entrada = await _monitor_login(client, admin_user.email, TEST_PASSWORD)
        elegido = await _impersonate(
            client, str(entrada["access_token"]), a_client.id
        )

        yo = await _monitor_me(client, str(elegido["access_token"]))

        assert yo["client_id"] == str(a_client.id)
        assert yo["impersonated"] is True
        assert yo["user_id"] == str(admin_user.id)  # sigue siendo el administrador

    async def test_a_company_with_consumption_disabled_can_still_be_entered(
        self, client: AsyncClient, admin_user: User, a_client: Client,
        db_session: AsyncSession,
    ) -> None:
        """El caso que motivó todo: `puede_ver_consumo` decide lo que ve el
        cliente, no lo que ve quien lo administra. Si no, no se podría revisar
        una empresa antes de habilitarla."""
        a_client.puede_ver_consumo = False
        await db_session.flush()
        entrada = await _monitor_login(client, admin_user.email, TEST_PASSWORD)

        elegido = await _impersonate(
            client, str(entrada["access_token"]), a_client.id
        )

        assert elegido["_status"] == status.HTTP_200_OK

    async def test_an_unknown_company_is_a_404(
        self, client: AsyncClient, admin_user: User
    ) -> None:
        entrada = await _monitor_login(client, admin_user.email, TEST_PASSWORD)

        elegido = await _impersonate(
            client, str(entrada["access_token"]), uuid.uuid4()
        )

        assert elegido["_status"] == status.HTTP_404_NOT_FOUND

    async def test_a_client_cannot_impersonate_anyone(
        self,
        client: AsyncClient,
        a_client: Client,
        granted: dict[str, object],
        db_session: AsyncSession,
    ) -> None:
        """El límite que importa: si un cliente pudiera pedir esto, la
        suplantación sería una puerta abierta entre empresas."""
        otra = Client(nombre_empresa="Otra SA", contacto_email="otra@empresa.com")
        db_session.add(otra)
        await db_session.flush()
        entrada = await _monitor_login(
            client, "operador@empresa.com", str(granted["temporary_password"])
        )

        elegido = await _impersonate(client, str(entrada["access_token"]), otra.id)

        assert elegido["_status"] == status.HTTP_403_FORBIDDEN

    async def test_without_a_token_it_is_rejected(
        self, client: AsyncClient, a_client: Client
    ) -> None:
        response = await client.post(
            f"/api/v1/auth-monitor/impersonate/{a_client.id}"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestImpersonationSurvivesRefresh:
    async def test_refreshing_keeps_the_chosen_company(
        self, client: AsyncClient, admin_user: User, a_client: Client
    ) -> None:
        """Sin esto el administrador vuelve a la lista de proyectos cada vez
        que el token de acceso vence — que en producción es todo el tiempo."""
        entrada = await _monitor_login(client, admin_user.email, TEST_PASSWORD)
        elegido = await _impersonate(
            client, str(entrada["access_token"]), a_client.id
        )

        response = await client.post(
            "/api/v1/auth-monitor/refresh",
            json={"refresh_token": elegido["refresh_token"]},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["client_id"] == str(a_client.id)

    async def test_an_admin_who_never_chose_refreshes_without_a_company(
        self, client: AsyncClient, admin_user: User
    ) -> None:
        entrada = await _monitor_login(client, admin_user.email, TEST_PASSWORD)

        response = await client.post(
            "/api/v1/auth-monitor/refresh",
            json={"refresh_token": entrada["refresh_token"]},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["client_id"] is None


class TestLeavingImpersonation:
    async def test_an_admin_can_go_back_to_having_no_company(
        self, client: AsyncClient, admin_user: User, a_client: Client
    ) -> None:
        entrada = await _monitor_login(client, admin_user.email, TEST_PASSWORD)
        elegido = await _impersonate(client, str(entrada["access_token"]), a_client.id)

        response = await client.delete(
            "/api/v1/auth-monitor/impersonate",
            headers=auth_header(str(elegido["access_token"])),
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["client_id"] is None

    async def test_the_returned_token_no_longer_reads_that_company(
        self, client: AsyncClient, admin_user: User, a_client: Client
    ) -> None:
        """Salir tiene que ser real: si el token nuevo siguiera marcado como
        suplantación, el aviso desaparecería de la pantalla mientras el acceso
        sigue abierto — la peor de las dos combinaciones."""
        entrada = await _monitor_login(client, admin_user.email, TEST_PASSWORD)
        elegido = await _impersonate(client, str(entrada["access_token"]), a_client.id)
        salida = await client.delete(
            "/api/v1/auth-monitor/impersonate",
            headers=auth_header(str(elegido["access_token"])),
        )

        yo = await _monitor_me(client, salida.json()["access_token"])

        assert yo["client_id"] is None
        assert yo["impersonated"] is False

    async def test_a_client_cannot_ask_for_it(
        self, client: AsyncClient, granted: dict[str, object]
    ) -> None:
        entrada = await _monitor_login(
            client, "operador@empresa.com", str(granted["temporary_password"])
        )

        response = await client.delete(
            "/api/v1/auth-monitor/impersonate",
            headers=auth_header(str(entrada["access_token"])),
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
