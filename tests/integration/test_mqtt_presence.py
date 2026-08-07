"""Presence arriving over MQTT, and what the CRM does with it."""

import uuid
from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services import mqtt_events
from tests.conftest import auth_header

type Login = Callable[..., Awaitable[str]]


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


@pytest.fixture(autouse=True)
def use_the_test_session(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The listener opens its own session; point it at the in-memory one."""

    class _Factory:
        def __call__(self) -> "_Factory":
            return self

        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(mqtt_events, "get_session_factory", lambda: _Factory())


@pytest.fixture
async def gateway(client: AsyncClient, admin_headers: dict[str, str]) -> dict[str, str]:
    created = await client.post(
        "/api/v1/clients", json={"nombre_empresa": "Empresa"}, headers=admin_headers
    )
    site = await client.post(
        f"/api/v1/clients/{created.json()['id']}/sites",
        json={"nombre": "Planta"},
        headers=admin_headers,
    )
    made = await client.post(
        f"/api/v1/sites/{site.json()['id']}/gateways",
        json={"numero_serie": "GW-1"},
        headers=admin_headers,
    )
    return {"id": made.json()["id"], "uuid": made.json()["uuid"]}


class TestRecordingPresence:
    async def test_a_report_turns_the_gateway_online(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway: dict[str, str],
    ) -> None:
        """MQTT presence spares the wait for the next HTTP call."""
        before = await client.get(
            f"/api/v1/gateways/{gateway['id']}", headers=admin_headers
        )
        assert before.json()["estado"] == "offline"

        await mqtt_events.record_gateway_presence(uuid.UUID(gateway["uuid"]), {})

        after = await client.get(
            f"/api/v1/gateways/{gateway['id']}", headers=admin_headers
        )
        assert after.json()["estado"] == "online"

    async def test_the_device_can_correct_what_the_crm_believes(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway: dict[str, str],
    ) -> None:
        await mqtt_events.record_gateway_presence(
            uuid.UUID(gateway["uuid"]),
            {"firmware_version": "2.4.1", "ip_actual": "10.20.30.40"},
        )

        response = await client.get(
            f"/api/v1/gateways/{gateway['id']}", headers=admin_headers
        )

        assert response.json()["firmware_version"] == "2.4.1"
        assert response.json()["ip_actual"] == "10.20.30.40"

    async def test_empty_fields_do_not_erase_what_is_known(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        gateway: dict[str, str],
    ) -> None:
        await mqtt_events.record_gateway_presence(
            uuid.UUID(gateway["uuid"]), {"firmware_version": "2.4.1"}
        )

        await mqtt_events.record_gateway_presence(
            uuid.UUID(gateway["uuid"]), {"firmware_version": "", "ip_actual": None}
        )

        response = await client.get(
            f"/api/v1/gateways/{gateway['id']}", headers=admin_headers
        )
        assert response.json()["firmware_version"] == "2.4.1"

    async def test_an_unknown_gateway_is_ignored(self, gateway: dict[str, str]) -> None:
        """Either a deleted device, or something publishing where it should not."""
        await mqtt_events.record_gateway_presence(uuid.uuid4(), {})

    async def test_the_uuid_of_a_gateway_can_be_looked_up(
        self, gateway: dict[str, str]
    ) -> None:
        found = await mqtt_events.gateway_uuid_for(uuid.UUID(gateway["id"]))

        assert found == uuid.UUID(gateway["uuid"])

    async def test_looking_up_a_missing_gateway_returns_nothing(self) -> None:
        assert await mqtt_events.gateway_uuid_for(uuid.uuid4()) is None
