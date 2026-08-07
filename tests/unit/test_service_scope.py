"""What a machine principal may do, as rules rather than as endpoints.

The point of these is negative: a service credential lives in another
system's environment file, so the interesting assertions are the ones that
say it *cannot* do something.
"""

import uuid

import pytest

from app.domain.access import AccessScope
from app.domain.enums import ServicePermission, UserRole

SERVICE = uuid.uuid4()
CLIENT = uuid.uuid4()


def _service(
    *permissions: ServicePermission, client_id: uuid.UUID | None = None
) -> AccessScope:
    return AccessScope(
        role=UserRole.SOLO_LECTURA,
        client_id=client_id,
        service_id=SERVICE,
        permissions=frozenset(permissions),
    )


class TestItIsNotAPerson:
    def test_it_says_so(self) -> None:
        assert _service().is_service is True

    def test_a_user_scope_is_not_one(self) -> None:
        assert (
            AccessScope(role=UserRole.ADMIN, user_id=uuid.uuid4()).is_service is False
        )

    def test_it_is_named_as_a_service_when_refused(self) -> None:
        """Error messages should not call a machine by a human role."""
        assert _service().principal == "servicio"

    def test_a_person_is_named_by_their_role(self) -> None:
        assert AccessScope(role=UserRole.TECNICO).principal == "tecnico"


class TestItCanNeverWrite:
    @pytest.mark.parametrize(
        "permissions",
        [(), (ServicePermission.TARIFFS_READ,), tuple(ServicePermission)],
    )
    def test_no_permission_grants_writing(
        self, permissions: tuple[ServicePermission, ...]
    ) -> None:
        """There is no permission that opens writing, including all of them."""
        assert _service(*permissions).can_write is False

    def test_it_cannot_manage_users(self) -> None:
        assert _service(*ServicePermission).can_manage_users is False

    def test_it_cannot_manage_tariffs(self) -> None:
        assert _service(*ServicePermission).can_manage_tariffs is False

    def test_it_cannot_mint_more_credentials(self) -> None:
        """Otherwise a credential could widen its own reach."""
        assert _service(*ServicePermission).can_manage_services is False

    def test_it_is_not_staff(self) -> None:
        """The role it carries is a floor, never a decision."""
        assert _service().is_staff is False


class TestWhatItMayRead:
    def test_tariffs_need_their_permission(self) -> None:
        assert _service(ServicePermission.TARIFFS_READ).can_read_tariffs is True
        assert _service(ServicePermission.FLEET_READ).can_read_tariffs is False

    def test_the_fleet_needs_its_permission(self) -> None:
        assert _service(ServicePermission.FLEET_READ).can_read_fleet is True
        assert _service(ServicePermission.TARIFFS_READ).can_read_fleet is False

    def test_an_empty_credential_reads_nothing(self) -> None:
        empty = _service()
        assert empty.can_read_tariffs is False
        assert empty.can_read_fleet is False

    def test_granting_is_checked_by_membership(self) -> None:
        assert _service(ServicePermission.FLEET_READ).grants(
            ServicePermission.FLEET_READ
        )
        assert not _service().grants(ServicePermission.FLEET_READ)


class TestConfinement:
    def test_a_pinned_credential_sees_only_that_client(self) -> None:
        assert _service(client_id=CLIENT).visible_client_id == CLIENT

    def test_an_unpinned_one_sees_the_platform(self) -> None:
        assert _service().visible_client_id is None

    def test_a_pinned_credential_is_refused_other_clients(self) -> None:
        pinned = _service(ServicePermission.FLEET_READ, client_id=CLIENT)

        assert pinned.may_read_client(CLIENT) is True
        assert pinned.may_read_client(uuid.uuid4()) is False

    def test_it_never_matches_a_user_identity(self) -> None:
        """`is_self` is about accounts; a service has none."""
        assert _service().is_self(uuid.uuid4()) is False
        assert _service().is_self(SERVICE) is False


class TestPeopleAreUnaffected:
    """The change added a principal; it must not have moved the human rules."""

    def test_an_admin_still_writes(self) -> None:
        assert AccessScope(role=UserRole.ADMIN).can_write is True

    def test_a_read_only_user_still_reads_tariffs(self) -> None:
        assert AccessScope(role=UserRole.SOLO_LECTURA).can_read_tariffs is True

    def test_a_client_still_cannot(self) -> None:
        assert AccessScope(role=UserRole.CLIENTE).can_read_tariffs is False

    def test_every_person_may_read_the_fleet(self) -> None:
        """For a person the tree already confines itself; there is no gate."""
        for role in UserRole:
            assert AccessScope(role=role).can_read_fleet is True
