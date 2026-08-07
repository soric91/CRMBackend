"""Who may see and change what.

Pure rules: no FastAPI, no SQLAlchemy. The services ask these questions, the
API layer only supplies the answer to "who is calling".
"""

import uuid
from dataclasses import dataclass, field

from app.domain.enums import ServicePermission, UserRole

# Roles that administer the platform. They see every client.
STAFF_ROLES = frozenset({UserRole.ADMIN, UserRole.TECNICO})

# Roles allowed to create, modify or delete. Everything else is read-only.
WRITER_ROLES = frozenset({UserRole.ADMIN, UserRole.TECNICO})


@dataclass(frozen=True)
class AccessScope:
    """What one caller is allowed to reach.

    Two kinds of caller share this type: a person, identified by `user_id` and
    a role, and another system, identified by `service_id` and a set of
    permissions. Keeping them in one object means the services below ask the
    same questions of both and cannot accidentally answer only for people.
    """

    role: UserRole
    client_id: uuid.UUID | None = None
    # Who is calling. Always set by the API layer; optional here so a rule can
    # be reasoned about without inventing an identity.
    user_id: uuid.UUID | None = None
    # Set instead of `user_id` when the caller is another system. Its presence
    # is what makes this a service principal — see :attr:`is_service`.
    service_id: uuid.UUID | None = None
    permissions: frozenset[ServicePermission] = field(default_factory=frozenset)

    @property
    def is_service(self) -> bool:
        """Whether the caller is another system rather than a person.

        Checked first by every capability below. A service carries a `role`
        only so error messages have a word for it; the role never decides
        anything, because a machine credential is not a person with a job.
        """
        return self.service_id is not None

    @property
    def principal(self) -> str:
        """How to name this caller when refusing it something."""
        return "servicio" if self.is_service else self.role.value

    def grants(self, permission: ServicePermission) -> bool:
        """Whether a service credential was issued this permission."""
        return permission in self.permissions

    @property
    def is_staff(self) -> bool:
        """Whether the caller administers the platform rather than one client."""
        return not self.is_service and self.role in STAFF_ROLES

    @property
    def can_write(self) -> bool:
        """Never true for a service.

        There is no permission that grants writing and no way to ask for one.
        A credential sitting in another system's environment file must not be
        able to change what the fleet is.
        """
        return not self.is_service and self.role in WRITER_ROLES

    @property
    def can_manage_users(self) -> bool:
        """Only administrators touch accounts.

        Narrower than :attr:`can_write` on purpose: a `tecnico` who could
        create users would be able to mint an admin and promote itself.
        """
        return not self.is_service and self.role is UserRole.ADMIN

    @property
    def can_manage_services(self) -> bool:
        """Only administrators issue machine credentials.

        A service that could mint service accounts would be able to widen its
        own permissions, which is the one thing the permission list exists to
        prevent.
        """
        return self.can_manage_users

    @property
    def can_read_tariffs(self) -> bool:
        """Tariffs belong to the platform, so internal roles all read them.

        A `cliente` is refused outright: unlike a device it cannot see, this is
        not another company's data being hidden, so 403 is the honest answer.
        A service reads them only if it was issued the permission.
        """
        if self.is_service:
            return self.grants(ServicePermission.TARIFFS_READ)
        return self.role is not UserRole.CLIENTE

    @property
    def can_read_fleet(self) -> bool:
        """Whether the caller may read the aggregate installation tree.

        For a person this is not a separate question — the tree confines itself
        to what they already see. For a service it is: a consumer that only
        needs prices has no business enumerating the devices.
        """
        if self.is_service:
            return self.grants(ServicePermission.FLEET_READ)
        return True

    @property
    def can_manage_tariffs(self) -> bool:
        """Narrower than :attr:`can_write`, like user management.

        These prices multiply consumption to produce money. A `tecnico`
        maintains devices; changing what energy costs is not part of that job.
        """
        return not self.is_service and self.role is UserRole.ADMIN

    def is_self(self, user_id: uuid.UUID) -> bool:
        """Whether ``user_id`` is the caller's own account."""
        return self.user_id is not None and self.user_id == user_id

    @property
    def visible_client_id(self) -> uuid.UUID | None:
        """The single client this caller is confined to, or None for all of them.

        A `cliente` login is pinned to its own company; staff and read-only
        internal users see the whole platform. A service is pinned to whatever
        it was issued for, which may be one client or nothing in particular.
        """
        if self.is_service:
            return self.client_id
        if self.role is UserRole.CLIENTE:
            return self.client_id
        return None

    def may_read_client(self, client_id: uuid.UUID) -> bool:
        """Whether this caller may look at ``client_id``."""
        confined_to = self.visible_client_id
        return confined_to is None or confined_to == client_id

    def may_write_client(self, client_id: uuid.UUID) -> bool:
        """Whether this caller may modify anything under ``client_id``."""
        return self.can_write and self.may_read_client(client_id)
