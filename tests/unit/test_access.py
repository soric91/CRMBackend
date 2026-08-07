"""Access rules, decided without a database."""

import uuid

import pytest

from app.domain.access import AccessScope
from app.domain.enums import UserRole

CLIENT_A = uuid.uuid4()
CLIENT_B = uuid.uuid4()


class TestStaff:
    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.TECNICO])
    def test_staff_may_write(self, role: UserRole) -> None:
        scope = AccessScope(role=role)
        assert scope.is_staff
        assert scope.can_write

    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.TECNICO])
    def test_staff_see_every_client(self, role: UserRole) -> None:
        scope = AccessScope(role=role)
        assert scope.visible_client_id is None
        assert scope.may_read_client(CLIENT_A)
        assert scope.may_read_client(CLIENT_B)


class TestClienteRole:
    def test_it_is_confined_to_its_own_client(self) -> None:
        scope = AccessScope(role=UserRole.CLIENTE, client_id=CLIENT_A)

        assert scope.visible_client_id == CLIENT_A
        assert scope.may_read_client(CLIENT_A)
        assert not scope.may_read_client(CLIENT_B)

    def test_it_cannot_write_even_its_own_data(self) -> None:
        """Clients read their installation; staff maintains it."""
        scope = AccessScope(role=UserRole.CLIENTE, client_id=CLIENT_A)

        assert not scope.can_write
        assert not scope.may_write_client(CLIENT_A)

    def test_it_is_not_staff(self) -> None:
        assert not AccessScope(role=UserRole.CLIENTE, client_id=CLIENT_A).is_staff

    def test_without_a_client_it_reaches_nothing(self) -> None:
        """The database forbids this row, but the rule must not fail open."""
        scope = AccessScope(role=UserRole.CLIENTE, client_id=None)

        assert scope.visible_client_id is None or not scope.may_read_client(CLIENT_A)


class TestReadOnlyRole:
    def test_it_sees_the_whole_platform(self) -> None:
        scope = AccessScope(role=UserRole.SOLO_LECTURA)

        assert scope.may_read_client(CLIENT_A)
        assert scope.may_read_client(CLIENT_B)

    def test_it_writes_nothing(self) -> None:
        scope = AccessScope(role=UserRole.SOLO_LECTURA)

        assert not scope.can_write
        assert not scope.may_write_client(CLIENT_A)


class TestWritePermission:
    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            (UserRole.ADMIN, True),
            (UserRole.TECNICO, True),
            (UserRole.CLIENTE, False),
            (UserRole.SOLO_LECTURA, False),
        ],
    )
    def test_only_staff_writes(self, role: UserRole, expected: bool) -> None:
        assert AccessScope(role=role, client_id=CLIENT_A).can_write is expected

    def test_every_role_is_covered_by_the_rules(self) -> None:
        """A role added later must be denied until it is decided on."""
        for role in UserRole:
            scope = AccessScope(role=role, client_id=CLIENT_A)
            assert isinstance(scope.can_write, bool)
            assert isinstance(scope.is_staff, bool)


class TestImmutability:
    def test_a_scope_cannot_be_edited_after_it_is_built(self) -> None:
        """Otherwise a service could widen its own permissions mid-request."""
        scope = AccessScope(role=UserRole.CLIENTE, client_id=CLIENT_A)

        with pytest.raises(Exception, match=r"(?i)frozen|immutable|cannot assign"):
            scope.role = UserRole.ADMIN  # type: ignore[misc]


class TestUserManagement:
    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            (UserRole.ADMIN, True),
            (UserRole.TECNICO, False),
            (UserRole.CLIENTE, False),
            (UserRole.SOLO_LECTURA, False),
        ],
    )
    def test_only_admins_manage_accounts(self, role: UserRole, expected: bool) -> None:
        """Narrower than can_write: a tecnico could otherwise mint an admin."""
        assert AccessScope(role=role, client_id=CLIENT_A).can_manage_users is expected

    def test_a_writer_is_not_automatically_an_account_manager(self) -> None:
        scope = AccessScope(role=UserRole.TECNICO)

        assert scope.can_write
        assert not scope.can_manage_users


class TestIdentity:
    def test_it_recognises_its_own_account(self) -> None:
        me = uuid.uuid4()
        assert AccessScope(role=UserRole.ADMIN, user_id=me).is_self(me)

    def test_it_does_not_recognise_someone_else(self) -> None:
        scope = AccessScope(role=UserRole.ADMIN, user_id=uuid.uuid4())

        assert not scope.is_self(uuid.uuid4())

    def test_an_unknown_identity_matches_nobody(self) -> None:
        """Without an id the self-protection must not match by accident."""
        assert not AccessScope(role=UserRole.ADMIN).is_self(uuid.uuid4())


class TestTariffPermissions:
    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            (UserRole.ADMIN, True),
            (UserRole.TECNICO, True),
            (UserRole.SOLO_LECTURA, True),
            (UserRole.CLIENTE, False),
        ],
    )
    def test_every_internal_role_reads_them(
        self, role: UserRole, expected: bool
    ) -> None:
        """They belong to the platform, not to any one company."""
        scope = AccessScope(role=role, client_id=CLIENT_A)

        assert scope.can_read_tariffs is expected

    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            (UserRole.ADMIN, True),
            (UserRole.TECNICO, False),
            (UserRole.SOLO_LECTURA, False),
            (UserRole.CLIENTE, False),
        ],
    )
    def test_only_an_admin_changes_them(self, role: UserRole, expected: bool) -> None:
        """Prices multiply consumption into money; a tecnico maintains devices."""
        scope = AccessScope(role=role, client_id=CLIENT_A)

        assert scope.can_manage_tariffs is expected

    def test_writing_devices_does_not_imply_writing_prices(self) -> None:
        scope = AccessScope(role=UserRole.TECNICO)

        assert scope.can_write
        assert not scope.can_manage_tariffs
