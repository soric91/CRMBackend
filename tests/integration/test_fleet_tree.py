"""The aggregate fleet document.

One request that answers "what does this client have installed", nested, so a
consumer does not have to walk the hierarchy one parent at a time. The rules
worth pinning down here are the depth control, the tenant confinement, and the
ETag — a fingerprint that has to change when the answer does and not before.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from tests.conftest import auth_header

type Login = Callable[..., Awaitable[str]]

FLEET = "/api/v1/fleet"


@pytest.fixture
async def admin_headers(admin_user: User, authenticate: Login) -> dict[str, str]:
    return auth_header(await authenticate(admin_user.email))


async def _install(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    empresa: str,
    serie: str,
    dispositivo: str,
) -> dict[str, str]:
    """Create one full branch: client, site, gateway, device, register."""
    created = await client.post(
        "/api/v1/clients", json={"nombre_empresa": empresa}, headers=headers
    )
    client_id = created.json()["id"]

    site = await client.post(
        f"/api/v1/clients/{client_id}/sites",
        json={"nombre": f"Planta {empresa}"},
        headers=headers,
    )
    gateway = await client.post(
        f"/api/v1/sites/{site.json()['id']}/gateways",
        json={"numero_serie": serie},
        headers=headers,
    )
    equipment = await client.post(
        f"/api/v1/gateways/{gateway.json()['id']}/equipment",
        json={
            "tipo": "analizador",
            "modbus_id": 11,
            "nombre_dispositivo": dispositivo,
            "device_type": "CT_Meter",
            "marca": "chint",
        },
        headers=headers,
    )
    variable = await client.post(
        f"/api/v1/equipment/{equipment.json()['id']}/variables",
        json={
            "nombre": "PhV_phsA",
            "registro_modbus": "2006",
            "notacion_registro": "hex",
            "tipo_dato": "float32",
        },
        headers=headers,
    )
    return {
        "client_id": client_id,
        "site_id": site.json()["id"],
        "gateway_id": gateway.json()["id"],
        "gateway_uuid": gateway.json()["uuid"],
        "equipment_id": equipment.json()["id"],
        "variable_id": variable.json()["id"],
    }


@pytest.fixture
async def installations(
    client: AsyncClient, admin_headers: dict[str, str]
) -> dict[str, dict[str, str]]:
    """Two complete installations belonging to two different clients."""
    return {
        "norte": await _install(
            client,
            admin_headers,
            empresa="Empresa Norte",
            serie="GW-NORTE",
            dispositivo="Medidor_Norte",
        ),
        "sur": await _install(
            client,
            admin_headers,
            empresa="Empresa Sur",
            serie="GW-SUR",
            dispositivo="Medidor_Sur",
        ),
    }


def _only(payload: dict[str, Any], empresa: str) -> dict[str, Any]:
    """The one client in the page with that name."""
    matches = [item for item in payload["items"] if item["nombre_empresa"] == empresa]
    assert len(matches) == 1, f"expected exactly one {empresa}"
    return matches[0]


class TestTheWholeTreeInOneRequest:
    async def test_every_level_arrives_nested(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        """The four requests the caller used to make, collapsed into one."""
        response = await client.get(FLEET, headers=admin_headers)

        assert response.status_code == status.HTTP_200_OK
        norte = _only(response.json(), "Empresa Norte")
        site = norte["sites"][0]
        gateway = site["gateways"][0]
        equipment = gateway["equipment"][0]

        assert site["nombre"] == "Planta Empresa Norte"
        assert gateway["numero_serie"] == "GW-NORTE"
        assert equipment["nombre_dispositivo"] == "Medidor_Norte"
        assert equipment["variables"][0]["nombre"] == "PhV_phsA"

    async def test_the_gateway_uuid_travels_with_it(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        """The identifier the MQTT topics and the firmware's own calls use.

        Without it the document would not let a consumer resolve a reading back
        to the device that produced it, which is the point of asking.
        """
        response = await client.get(FLEET, headers=admin_headers)

        gateway = _only(response.json(), "Empresa Norte")["sites"][0]["gateways"][0]
        assert gateway["uuid"] == installations["norte"]["gateway_uuid"]
        assert gateway["id"] == installations["norte"]["gateway_id"]

    async def test_the_register_comes_with_its_written_form(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        """`2006` in hex is register 8198, and both forms are useful."""
        response = await client.get(FLEET, headers=admin_headers)

        variable = _only(response.json(), "Empresa Norte")["sites"][0]["gateways"][0][
            "equipment"
        ][0]["variables"][0]
        assert variable["registro_modbus"] == 8198
        assert variable["registro_display"] == "0x2006"
        assert variable["notacion_registro"] == "hex"

    async def test_no_secret_is_part_of_the_document(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        """A listing never carries the gateway's credential, in any shape."""
        gateway = _only(
            (await client.get(FLEET, headers=admin_headers)).json(), "Empresa Norte"
        )["sites"][0]["gateways"][0]

        for forbidden in ("credential", "credential_hash", "credential_emitida_en"):
            assert forbidden not in gateway

    async def test_a_client_with_nothing_installed_still_appears(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """An empty installation is an answer, not an omission."""
        await client.post(
            "/api/v1/clients",
            json={"nombre_empresa": "Empresa Sin Nada"},
            headers=admin_headers,
        )

        response = await client.get(FLEET, headers=admin_headers)

        assert _only(response.json(), "Empresa Sin Nada")["sites"] == []


class TestHowDeepItGoes:
    @pytest.mark.parametrize(
        ("nivel", "loaded", "absent"),
        [
            ("sitios", ("sites",), "gateways"),
            ("gateways", ("sites", "gateways"), "equipment"),
            ("equipos", ("sites", "gateways", "equipment"), "variables"),
        ],
    )
    async def test_the_level_below_the_last_one_is_null(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
        nivel: str,
        loaded: tuple[str, ...],
        absent: str,
    ) -> None:
        """`null` says "not asked for"; `[]` would say "there are none"."""
        response = await client.get(
            FLEET, params={"nivel": nivel}, headers=admin_headers
        )

        node: Any = _only(response.json(), "Empresa Norte")
        for step in loaded:
            assert node[step], f"{step} should have been loaded at nivel={nivel}"
            node = node[step][0]
        assert node[absent] is None

    async def test_variables_is_the_default(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        """The deepest level, because that is what interpreting a reading needs."""
        response = await client.get(FLEET, headers=admin_headers)

        variables = _only(response.json(), "Empresa Norte")["sites"][0]["gateways"][0][
            "equipment"
        ][0]["variables"]
        assert variables is not None

    async def test_a_level_that_does_not_exist_is_refused(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.get(
            FLEET, params={"nivel": "todo"}, headers=admin_headers
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_a_shallower_level_does_not_load_what_it_omits(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        """The relationships are `lazy="raise"`.

        A projection that walked an unloaded collection would not return a
        smaller document — it would fail with MissingGreenlet. Getting a 200
        proves the loader chain and the projection agree on the depth.
        """
        response = await client.get(
            FLEET, params={"nivel": "sitios"}, headers=admin_headers
        )

        assert response.status_code == status.HTTP_200_OK


class TestWhoSeesWhat:
    async def test_a_client_login_only_ever_sees_its_own(
        self,
        client: AsyncClient,
        cliente_user: User,
        authenticate_monitor: Login,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        """A `cliente` reaches this through the monitoring web, not the CRM."""
        headers = auth_header(await authenticate_monitor(cliente_user.email))

        response = await client.get(FLEET, headers=headers)

        payload = response.json()
        assert payload["total"] == 1
        assert payload["items"][0]["nombre_empresa"] == "Industrias Andinas"

    async def test_asking_for_another_client_returns_nothing(
        self,
        client: AsyncClient,
        cliente_user: User,
        authenticate_monitor: Login,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        """The filter narrows, it can never widen.

        A confined caller naming somebody else's client gets an empty page —
        not its own rows relabelled, which is what a filter applied *instead
        of* the confinement would produce.
        """
        headers = auth_header(await authenticate_monitor(cliente_user.email))

        response = await client.get(
            FLEET,
            params={"client_id": installations["norte"]["client_id"]},
            headers=headers,
        )

        assert response.json()["total"] == 0
        assert response.json()["items"] == []

    async def test_staff_can_narrow_to_one_client(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        response = await client.get(
            FLEET,
            params={"client_id": installations["sur"]["client_id"]},
            headers=admin_headers,
        )

        payload = response.json()
        assert payload["total"] == 1
        assert payload["items"][0]["nombre_empresa"] == "Empresa Sur"

    async def test_the_crm_surface_refuses_that_same_client(
        self, client: AsyncClient, cliente_user: User
    ) -> None:
        """Both audiences reach the data, but only one surface issues a token.

        A `cliente` has no CRM login at all. Pinned here because this endpoint
        is the one a monitoring consumer would reach for first, and reading it
        through the wrong door has to keep failing.
        """
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": cliente_user.email, "password": "una-clave-de-prueba"},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_it_needs_a_token(self, client: AsyncClient) -> None:
        response = await client.get(FLEET)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestNarrowingAndPaging:
    async def test_the_search_matches_the_company_name(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        response = await client.get(
            FLEET, params={"search": "sur"}, headers=admin_headers
        )

        payload = response.json()
        assert payload["total"] == 1
        assert payload["items"][0]["nombre_empresa"] == "Empresa Sur"

    async def test_paging_is_over_clients(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        """The root of the tree, since a page of registers would mean nothing."""
        response = await client.get(
            FLEET, params={"limit": 1, "offset": 0}, headers=admin_headers
        )

        payload = response.json()
        assert len(payload["items"]) == 1
        assert payload["total"] == 2

    async def test_clients_come_back_in_a_stable_order(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        first = await client.get(FLEET, params={"limit": 1}, headers=admin_headers)
        second = await client.get(
            FLEET, params={"limit": 1, "offset": 1}, headers=admin_headers
        )

        assert first.json()["items"][0]["nombre_empresa"] == "Empresa Norte"
        assert second.json()["items"][0]["nombre_empresa"] == "Empresa Sur"


class TestTheETag:
    async def test_it_is_returned(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        response = await client.get(FLEET, headers=admin_headers)

        assert response.headers["ETag"].startswith('"')

    async def test_the_same_fleet_fingerprints_the_same(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        """Nothing volatile is hashed, or polling would never hit the cache."""
        first = await client.get(FLEET, headers=admin_headers)
        second = await client.get(FLEET, headers=admin_headers)

        assert first.headers["ETag"] == second.headers["ETag"]

    async def test_an_unchanged_fleet_answers_304(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        etag = (await client.get(FLEET, headers=admin_headers)).headers["ETag"]

        response = await client.get(
            FLEET, headers={**admin_headers, "If-None-Match": etag}
        )

        assert response.status_code == status.HTTP_304_NOT_MODIFIED
        assert response.headers["ETag"] == etag

    async def test_the_bare_hash_is_accepted_too(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        """Clients that strip the quotes should not poll for nothing."""
        etag = (await client.get(FLEET, headers=admin_headers)).headers["ETag"]

        response = await client.get(
            FLEET, headers={**admin_headers, "If-None-Match": etag.strip('"')}
        )

        assert response.status_code == status.HTTP_304_NOT_MODIFIED

    async def test_a_new_register_changes_it(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        etag = (await client.get(FLEET, headers=admin_headers)).headers["ETag"]

        await client.post(
            f"/api/v1/equipment/{installations['norte']['equipment_id']}/variables",
            json={"nombre": "A_phsA", "registro_modbus": 300},
            headers=admin_headers,
        )

        response = await client.get(
            FLEET, headers={**admin_headers, "If-None-Match": etag}
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.headers["ETag"] != etag

    async def test_a_client_added_beyond_the_page_still_changes_it(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        """`total` is content too.

        The rows on the page are identical, but the pager is not. Answering 304
        would leave the caller quietly disagreeing with reality.
        """
        page = {"limit": 1, "offset": 0}
        etag = (await client.get(FLEET, params=page, headers=admin_headers)).headers[
            "ETag"
        ]

        await client.post(
            "/api/v1/clients",
            json={"nombre_empresa": "Zeta Ultima"},
            headers=admin_headers,
        )

        response = await client.get(
            FLEET, params=page, headers={**admin_headers, "If-None-Match": etag}
        )
        assert response.status_code == status.HTTP_200_OK

    async def test_the_depth_is_part_of_the_fingerprint(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        installations: dict[str, dict[str, str]],
    ) -> None:
        """A shallower document is a different document, not a stale one."""
        deep = (await client.get(FLEET, headers=admin_headers)).headers["ETag"]

        response = await client.get(
            FLEET,
            params={"nivel": "sitios"},
            headers={**admin_headers, "If-None-Match": deep},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.headers["ETag"] != deep

    async def test_two_callers_seeing_different_fleets_get_different_tags(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        cliente_user: User,
        authenticate_monitor: Login,
        installations: dict[str, dict[str, str]],
    ) -> None:
        """The fingerprint is of the answer, and the answer depends on who asks."""
        confined = auth_header(await authenticate_monitor(cliente_user.email))

        staff_tag = (await client.get(FLEET, headers=admin_headers)).headers["ETag"]
        client_tag = (await client.get(FLEET, headers=confined)).headers["ETag"]

        assert staff_tag != client_tag


class TestItStaysCheap:
    async def test_the_tree_does_not_cost_a_query_per_row(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        db_session: AsyncSession,
        installations: dict[str, dict[str, str]],
    ) -> None:
        """`selectinload` is one SELECT per level, not per row.

        Counted rather than asserted by eye: this is the whole reason the
        endpoint exists, and an accidental lazy load would undo it silently.
        """
        statements: list[str] = []

        from sqlalchemy import event

        def _record(_: object, __: object, statement: str, *args: object) -> None:
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", _record)  # pyright: ignore[reportArgumentType]
        try:
            response = await client.get(FLEET, headers=admin_headers)
        finally:
            event.remove(engine, "before_cursor_execute", _record)  # pyright: ignore[reportArgumentType]

        assert response.status_code == status.HTTP_200_OK
        # The caller's own row, the count, the clients, then one per level.
        assert len(statements) <= 8
