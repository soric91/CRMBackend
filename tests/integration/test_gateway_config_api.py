"""The firmware's surface: credential, token and configuration."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from fastapi import status
from httpx import AsyncClient

from app.core.config import Settings
from app.core.security import TokenAudience, TokenType, create_token
from app.models import User
from tests.conftest import auth_header

type Login = Callable[..., Awaitable[str]]


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


@pytest.fixture
async def installation(
    client: AsyncClient, admin_headers: dict[str, str]
) -> dict[str, str]:
    """A gateway with one RTU device and two registers, as in the field."""
    created = await client.post(
        "/api/v1/clients", json={"nombre_empresa": "Empresa"}, headers=admin_headers
    )
    site = await client.post(
        f"/api/v1/clients/{created.json()['id']}/sites",
        json={"nombre": "Planta"},
        headers=admin_headers,
    )
    gateway = await client.post(
        f"/api/v1/sites/{site.json()['id']}/gateways",
        json={
            "numero_serie": "GW-1",
            "intervalo_lectura_segundos": 1,
            "log_level": "INFO",
        },
        headers=admin_headers,
    )
    equipment = await client.post(
        f"/api/v1/gateways/{gateway.json()['id']}/equipment",
        json={
            "tipo": "analizador",
            "modbus_id": 11,
            "nombre_dispositivo": "Modbus_DTSU666",
            "device_type": "CT_Meter",
            "marca": "chint",
            "modelo": "DTSU666",
            "puerto": "/dev/ttyRS485",
            "baudrate": 9600,
        },
        headers=admin_headers,
    )
    # Entered exactly as the Chint datasheet prints them: hex digits, hex
    # notation. The backend resolves 2006 to 8198 and writes it back as 0x2006.
    for nombre, registro in (("PhV_phsA", "2006"), ("PhV_phsB", "2008")):
        await client.post(
            f"/api/v1/equipment/{equipment.json()['id']}/variables",
            json={
                "nombre": nombre,
                "registro_modbus": registro,
                "notacion_registro": "hex",
                "tipo_dato": "float32",
                "escala": "1",
            },
            headers=admin_headers,
        )
    return {
        "gateway_id": gateway.json()["id"],
        "gateway_uuid": gateway.json()["uuid"],
        "equipment_id": equipment.json()["id"],
    }


async def _issue_credential(
    client: AsyncClient, headers: dict[str, str], gateway_id: str
) -> str:
    response = await client.post(
        f"/api/v1/gateways/{gateway_id}/credential", headers=headers
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    credential: str = response.json()["credential"]
    return credential


async def _token(
    client: AsyncClient, gateway_uuid: str, credential: str
) -> tuple[int, dict[str, object]]:
    response = await client.post(
        "/api/v1/gateway/token",
        json={"gateway_uuid": gateway_uuid, "credential": credential},
    )
    return response.status_code, response.json()


class TestCredential:
    async def test_it_is_returned_once(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        credential = await _issue_credential(
            client, admin_headers, installation["gateway_id"]
        )

        assert len(credential) >= 32

    async def test_reading_the_state_never_returns_it(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        credential = await _issue_credential(
            client, admin_headers, installation["gateway_id"]
        )

        response = await client.get(
            f"/api/v1/gateways/{installation['gateway_id']}/credential",
            headers=admin_headers,
        )

        assert response.json()["tiene_credencial"] is True
        assert response.json()["credential_emitida_en"] is not None
        assert credential not in response.text

    async def test_a_gateway_starts_without_one(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        response = await client.get(
            f"/api/v1/gateways/{installation['gateway_id']}/credential",
            headers=admin_headers,
        )

        assert response.json()["tiene_credencial"] is False

    async def test_regenerating_invalidates_the_previous_one(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        """That is the point: it is how a leaked credential is taken back."""
        first = await _issue_credential(
            client, admin_headers, installation["gateway_id"]
        )
        second = await _issue_credential(
            client, admin_headers, installation["gateway_id"]
        )

        assert first != second
        code, _ = await _token(client, installation["gateway_uuid"], first)
        assert code == status.HTTP_401_UNAUTHORIZED

    async def test_revoking_stops_the_gateway(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        credential = await _issue_credential(
            client, admin_headers, installation["gateway_id"]
        )
        await client.delete(
            f"/api/v1/gateways/{installation['gateway_id']}/credential",
            headers=admin_headers,
        )

        code, _ = await _token(client, installation["gateway_uuid"], credential)

        assert code == status.HTTP_401_UNAUTHORIZED

    async def test_a_readonly_user_cannot_issue_one(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        db_session: object,
        authenticate: Login,
    ) -> None:
        from app.domain.enums import UserRole
        from app.models import User as UserModel
        from tests.conftest import TEST_PASSWORD_HASH

        reader = UserModel(
            email="lectura@example.com",
            password_hash=TEST_PASSWORD_HASH,
            role=UserRole.SOLO_LECTURA,
        )
        db_session.add(reader)  # type: ignore[attr-defined]
        await db_session.flush()  # type: ignore[attr-defined]

        response = await client.post(
            f"/api/v1/gateways/{installation['gateway_id']}/credential",
            headers=auth_header(await authenticate(reader.email)),
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestToken:
    async def test_a_valid_credential_returns_a_token(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        credential = await _issue_credential(
            client, admin_headers, installation["gateway_id"]
        )

        code, body = await _token(client, installation["gateway_uuid"], credential)

        assert code == status.HTTP_200_OK
        assert body["access_token"]
        assert body["expires_in"] == 24 * 3600

    async def test_a_wrong_credential_is_rejected(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        await _issue_credential(client, admin_headers, installation["gateway_id"])

        code, _ = await _token(client, installation["gateway_uuid"], "no-es-la-buena")

        assert code == status.HTTP_401_UNAUTHORIZED

    async def test_an_unknown_gateway_answers_the_same(
        self, client: AsyncClient, installation: dict[str, str]
    ) -> None:
        """Otherwise the response would confirm which uuids exist."""
        unknown = await _token(client, str(uuid.uuid4()), "cualquier-cosa")
        without = await _token(client, installation["gateway_uuid"], "cualquier-cosa")

        assert unknown[0] == without[0] == status.HTTP_401_UNAUTHORIZED
        assert unknown[1] == without[1]

    async def test_the_token_belongs_to_the_gateway_audience(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        credential = await _issue_credential(
            client, admin_headers, installation["gateway_id"]
        )
        _, body = await _token(client, installation["gateway_uuid"], credential)

        response = await client.get(
            "/api/v1/auth/me", headers=auth_header(str(body["access_token"]))
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_a_crm_token_cannot_fetch_a_configuration(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config",
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestConfiguration:
    @pytest.fixture
    async def gateway_headers(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> dict[str, str]:
        await client.patch(
            f"/api/v1/gateways/{installation['gateway_id']}",
            json={"config_habilitada": True},
            headers=admin_headers,
        )
        credential = await _issue_credential(
            client, admin_headers, installation["gateway_id"]
        )
        _, body = await _token(client, installation["gateway_uuid"], credential)
        return auth_header(str(body["access_token"]))

    async def test_it_carries_the_gateway_settings(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
    ) -> None:
        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config",
            headers=gateway_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["gateway_uuid"] == installation["gateway_uuid"]
        assert body["log"]["loglevel"] == "INFO"
        assert body["mainmodbus"] == {"interval": 1, "start_hour": 0, "stop_hour": 23}

    async def test_the_device_matches_the_firmware_vocabulary(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
    ) -> None:
        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config",
            headers=gateway_headers,
        )

        device = response.json()["devices"][0]
        assert device["name"] == "Modbus_DTSU666"
        assert device["identify_device"] == installation["equipment_id"]
        assert device["device_type"] == "CT_Meter"
        assert device["protocol"] == "RTU"
        assert device["serialport"] == "/dev/ttyRS485"
        assert device["baudrate"] == 9600
        assert device["device_id"] == 11
        assert device["modbusconnect"] is True
        # El firmware necesita saber con qué código de función leer el bloque.
        # Sin esto tendría que adivinarlo, y adivinar mal no da error: devuelve
        # una excepción Modbus que se ve como un medidor que no responde.
        assert device["modbus_function"] == 3

    async def test_registers_come_out_as_hex_and_struct_characters(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
    ) -> None:
        """Exactly the shape of the map files the firmware already reads."""
        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config",
            headers=gateway_headers,
        )

        entry = response.json()["devices"][0]["map"]["PhV_phsA"]
        assert entry["address"] == "0x2006"
        assert entry["data_type"] == "f"
        assert entry["gain"] == "1"

    async def test_every_register_of_the_device_is_present(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_headers: dict[str, str],
    ) -> None:
        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config",
            headers=gateway_headers,
        )

        assert set(response.json()["devices"][0]["map"]) == {"PhV_phsA", "PhV_phsB"}

    async def test_a_tcp_device_carries_host_instead_of_a_port(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
        gateway_headers: dict[str, str],
    ) -> None:
        await client.post(
            f"/api/v1/gateways/{installation['gateway_id']}/equipment",
            json={
                "tipo": "analizador",
                "modbus_id": 12,
                "nombre_dispositivo": "Modbus_TCP",
                "device_type": "CT_Meter",
                "transporte": "tcp",
                "host": "10.0.0.5",
            },
            headers=admin_headers,
        )

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config",
            headers=gateway_headers,
        )

        tcp = next(
            item for item in response.json()["devices"] if item["name"] == "Modbus_TCP"
        )
        assert tcp["protocol"] == "TCP"
        assert tcp["host"] == "10.0.0.5"
        assert tcp["port"] == 502
        assert tcp["serialport"] is None
        assert tcp["baudrate"] is None


class TestItOnlyEverReturnsItsOwn:
    async def test_asking_for_another_gateway_is_404(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        """The whole point of the condition: a gateway fetches only its own."""
        await client.patch(
            f"/api/v1/gateways/{installation['gateway_id']}",
            json={"config_habilitada": True},
            headers=admin_headers,
        )
        credential = await _issue_credential(
            client, admin_headers, installation["gateway_id"]
        )
        _, body = await _token(client, installation["gateway_uuid"], credential)

        response = await client.get(
            f"/api/v1/gateway/{uuid.uuid4()}/config",
            headers=auth_header(str(body["access_token"])),
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_a_second_gateway_cannot_read_the_first(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        site = await client.get(
            f"/api/v1/gateways/{installation['gateway_id']}", headers=admin_headers
        )
        other = await client.post(
            f"/api/v1/sites/{site.json()['site_id']}/gateways",
            json={"numero_serie": "GW-2"},
            headers=admin_headers,
        )
        credential = await _issue_credential(client, admin_headers, other.json()["id"])
        _, body = await _token(client, other.json()["uuid"], credential)

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config",
            headers=auth_header(str(body["access_token"])),
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestTheDownloadSwitch:
    async def test_it_is_off_by_default(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        """A half-configured gateway must not pull an incomplete setup."""
        credential = await _issue_credential(
            client, admin_headers, installation["gateway_id"]
        )
        _, body = await _token(client, installation["gateway_uuid"], credential)

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config",
            headers=auth_header(str(body["access_token"])),
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_turning_it_off_again_stops_the_download(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        await client.patch(
            f"/api/v1/gateways/{installation['gateway_id']}",
            json={"config_habilitada": True},
            headers=admin_headers,
        )
        credential = await _issue_credential(
            client, admin_headers, installation["gateway_id"]
        )
        _, body = await _token(client, installation["gateway_uuid"], credential)
        headers = auth_header(str(body["access_token"]))
        assert (
            await client.get(
                f"/api/v1/gateway/{installation['gateway_uuid']}/config",
                headers=headers,
            )
        ).status_code == status.HTTP_200_OK

        await client.patch(
            f"/api/v1/gateways/{installation['gateway_id']}",
            json={"config_habilitada": False},
            headers=admin_headers,
        )

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config", headers=headers
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestTokenValidity:
    async def test_an_expired_token_is_rejected(
        self,
        client: AsyncClient,
        settings: Settings,
        installation: dict[str, str],
    ) -> None:
        expired = create_token(
            settings,
            subject=installation["gateway_uuid"],
            token_type=TokenType.ACCESS,
            audience=TokenAudience.GATEWAY,
            expires_in=timedelta(seconds=-1),
        )

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config",
            headers=auth_header(expired),
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_revoking_the_credential_invalidates_a_live_token(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        """A token alone is not proof the gateway is still trusted."""
        await client.patch(
            f"/api/v1/gateways/{installation['gateway_id']}",
            json={"config_habilitada": True},
            headers=admin_headers,
        )
        credential = await _issue_credential(
            client, admin_headers, installation["gateway_id"]
        )
        _, body = await _token(client, installation["gateway_uuid"], credential)
        headers = auth_header(str(body["access_token"]))

        await client.delete(
            f"/api/v1/gateways/{installation['gateway_id']}/credential",
            headers=admin_headers,
        )

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config", headers=headers
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_a_missing_token_is_rejected(
        self, client: AsyncClient, installation: dict[str, str]
    ) -> None:
        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestVersioningStopsTheLoop:
    """Polling is cheap and reapplying only happens on a real change."""

    @pytest.fixture
    async def enabled(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> dict[str, str]:
        await client.patch(
            f"/api/v1/gateways/{installation['gateway_id']}",
            json={"config_habilitada": True},
            headers=admin_headers,
        )
        credential = await _issue_credential(
            client, admin_headers, installation["gateway_id"]
        )
        _, body = await _token(client, installation["gateway_uuid"], credential)
        return auth_header(str(body["access_token"]))

    async def test_the_response_carries_a_version_and_an_etag(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        enabled: dict[str, str],
    ) -> None:
        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config", headers=enabled
        )

        version = response.json()["config_version"]
        assert len(version) == 64
        assert response.headers["ETag"] == f'"{version}"'

    async def test_the_version_is_stable_across_requests(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        enabled: dict[str, str],
    ) -> None:
        """`generated_at` changes every call and must not enter the hash."""
        first = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config", headers=enabled
        )
        second = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config", headers=enabled
        )

        assert first.json()["generated_at"] != second.json()["generated_at"]
        assert first.json()["config_version"] == second.json()["config_version"]

    async def test_an_unchanged_configuration_answers_304(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        enabled: dict[str, str],
    ) -> None:
        first = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config", headers=enabled
        )

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config",
            headers={**enabled, "If-None-Match": first.headers["ETag"]},
        )

        assert response.status_code == status.HTTP_304_NOT_MODIFIED
        assert not response.content

    async def test_editing_a_variable_changes_the_version(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
        enabled: dict[str, str],
    ) -> None:
        first = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config", headers=enabled
        )
        await client.post(
            f"/api/v1/equipment/{installation['equipment_id']}/variables",
            json={
                "nombre": "A_phsA",
                "registro_modbus": "200A",
                "notacion_registro": "hex",
            },
            headers=admin_headers,
        )

        response = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config",
            headers={**enabled, "If-None-Match": first.headers["ETag"]},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["config_version"] != first.json()["config_version"]


class TestAcknowledgement:
    @pytest.fixture
    async def enabled(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> dict[str, str]:
        await client.patch(
            f"/api/v1/gateways/{installation['gateway_id']}",
            json={"config_habilitada": True},
            headers=admin_headers,
        )
        credential = await _issue_credential(
            client, admin_headers, installation["gateway_id"]
        )
        _, body = await _token(client, installation["gateway_uuid"], credential)
        return auth_header(str(body["access_token"]))

    async def test_it_records_the_version_and_closes_the_switch(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        enabled: dict[str, str],
    ) -> None:
        config = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config", headers=enabled
        )
        version = config.json()["config_version"]

        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config/ack",
            json={"config_version": version},
            headers=enabled,
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["config_version_aplicada"] == version
        assert body["config_habilitada"] is False
        assert body["desactualizada"] is False
        assert body["config_aplicada_en"] is not None

    async def test_after_acknowledging_the_download_is_refused(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        enabled: dict[str, str],
    ) -> None:
        """What stops the loop: the same document is not handed out again."""
        config = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config", headers=enabled
        )
        await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config/ack",
            json={"config_version": config.json()["config_version"]},
            headers=enabled,
        )

        again = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config", headers=enabled
        )

        assert again.status_code == status.HTTP_403_FORBIDDEN

    async def test_a_stale_version_is_rejected(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        enabled: dict[str, str],
    ) -> None:
        """Otherwise the CRM would believe a device is running something else."""
        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config/ack",
            json={"config_version": "0" * 64},
            headers=enabled,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_another_gateway_cannot_acknowledge(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        enabled: dict[str, str],
    ) -> None:
        response = await client.post(
            f"/api/v1/gateway/{uuid.uuid4()}/config/ack",
            json={"config_version": "a" * 64},
            headers=enabled,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestDriftIsVisibleToThePanel:
    async def test_a_fresh_gateway_reports_nothing_applied(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        response = await client.get(
            f"/api/v1/gateways/{installation['gateway_id']}/config-status",
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["config_version_aplicada"] is None
        assert response.json()["desactualizada"] is True

    async def test_editing_after_an_acknowledgement_shows_as_pending(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        """The reason this endpoint exists: the switch is off and work is due."""
        await client.patch(
            f"/api/v1/gateways/{installation['gateway_id']}",
            json={"config_habilitada": True},
            headers=admin_headers,
        )
        credential = await _issue_credential(
            client, admin_headers, installation["gateway_id"]
        )
        _, body = await _token(client, installation["gateway_uuid"], credential)
        headers = auth_header(str(body["access_token"]))
        config = await client.get(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config", headers=headers
        )
        await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/config/ack",
            json={"config_version": config.json()["config_version"]},
            headers=headers,
        )

        await client.post(
            f"/api/v1/equipment/{installation['equipment_id']}/variables",
            json={"nombre": "A_phsA", "registro_modbus": 999},
            headers=admin_headers,
        )

        status_response = await client.get(
            f"/api/v1/gateways/{installation['gateway_id']}/config-status",
            headers=admin_headers,
        )

        assert status_response.json()["desactualizada"] is True
        assert status_response.json()["config_habilitada"] is False

    async def test_the_status_is_readable_while_the_switch_is_off(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        """The panel has to see what would be delivered, switch or no switch."""
        response = await client.get(
            f"/api/v1/gateways/{installation['gateway_id']}/config-status",
            headers=admin_headers,
        )

        assert response.json()["config_habilitada"] is False
        assert len(response.json()["config_version_actual"]) == 64

    async def test_it_needs_a_crm_token(
        self, client: AsyncClient, installation: dict[str, str]
    ) -> None:
        response = await client.get(
            f"/api/v1/gateways/{installation['gateway_id']}/config-status"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestHeartbeat:
    @pytest.fixture
    async def gateway_token(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> dict[str, str]:
        credential = await _issue_credential(
            client, admin_headers, installation["gateway_id"]
        )
        _, body = await _token(client, installation["gateway_uuid"], credential)
        return auth_header(str(body["access_token"]))

    async def test_it_works_with_the_download_disabled(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_token: dict[str, str],
    ) -> None:
        """A provisioned gateway has to stay visible after it stopped fetching."""
        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/heartbeat",
            json={},
            headers=gateway_token,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["config_habilitada"] is False
        assert response.json()["ultima_conexion"] is not None

    async def test_it_turns_the_gateway_online(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
        gateway_token: dict[str, str],
    ) -> None:
        before = await client.get(
            f"/api/v1/gateways/{installation['gateway_id']}", headers=admin_headers
        )
        assert before.json()["estado"] == "offline"

        await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/heartbeat",
            json={},
            headers=gateway_token,
        )

        after = await client.get(
            f"/api/v1/gateways/{installation['gateway_id']}", headers=admin_headers
        )
        assert after.json()["estado"] == "online"

    async def test_the_device_can_correct_what_the_crm_believes(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
        gateway_token: dict[str, str],
    ) -> None:
        """More reliable than an operator typing a firmware version by hand."""
        await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/heartbeat",
            json={"firmware_version": "2.4.1", "ip_actual": "10.20.30.40"},
            headers=gateway_token,
        )

        response = await client.get(
            f"/api/v1/gateways/{installation['gateway_id']}", headers=admin_headers
        )

        assert response.json()["firmware_version"] == "2.4.1"
        assert response.json()["ip_actual"] == "10.20.30.40"

    async def test_it_says_whether_work_is_waiting(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
        gateway_token: dict[str, str],
    ) -> None:
        """A device that only heartbeats still learns it has a download due."""
        await client.patch(
            f"/api/v1/gateways/{installation['gateway_id']}",
            json={"config_habilitada": True},
            headers=admin_headers,
        )

        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/heartbeat",
            json={},
            headers=gateway_token,
        )

        assert response.json()["config_habilitada"] is True
        assert len(response.json()["config_version_actual"]) == 64

    async def test_another_gateway_cannot_report_for_this_one(
        self,
        client: AsyncClient,
        installation: dict[str, str],
        gateway_token: dict[str, str],
    ) -> None:
        response = await client.post(
            f"/api/v1/gateway/{uuid.uuid4()}/heartbeat",
            json={},
            headers=gateway_token,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_it_needs_a_gateway_token(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installation: dict[str, str],
    ) -> None:
        response = await client.post(
            f"/api/v1/gateway/{installation['gateway_uuid']}/heartbeat",
            json={},
            headers=admin_headers,
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
