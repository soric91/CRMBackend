"""Create the first administrator.

Run with::

    uv run python -m app.scripts.create_admin

This is the only intended way to create an admin: the API has no public
registration route. The password is typed into a hidden prompt, hashed with the
same `hash_password` the login endpoint verifies against, and written straight
to the database configured in `.env` — it never travels over HTTP and is never
written to a log or a file.
"""

import asyncio
import sys
from getpass import getpass

from pydantic import EmailStr, TypeAdapter, ValidationError

from app.core.config import get_settings
from app.core.database import dispose_engine, get_session_factory
from app.core.security import MAX_PASSWORD_BYTES, PasswordTooLongError, hash_password
from app.domain.enums import UserRole
from app.models import User
from app.repositories.user import UserRepository, normalize_email

MIN_PASSWORD_LENGTH = 8

_email_validator: TypeAdapter[EmailStr] = TypeAdapter(EmailStr)


def prompt_email() -> str:
    """Ask for an address until a valid one is given."""
    while True:
        raw = input("Email del administrador: ").strip()
        try:
            _email_validator.validate_python(raw)
        except ValidationError:
            print("  No parece un email válido. Intentá de nuevo.")
            continue
        return normalize_email(raw)


def prompt_password() -> str:
    """Ask twice for a hidden password until both entries agree."""
    while True:
        password = getpass("Contraseña: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"  Mínimo {MIN_PASSWORD_LENGTH} caracteres.")
            continue
        if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
            print(f"  Máximo {MAX_PASSWORD_BYTES} bytes.")
            continue
        if password != getpass("Repetir contraseña: "):
            print("  No coinciden. Intentá de nuevo.")
            continue
        return password


async def create_admin(email: str, password: str) -> str:
    """Insert an admin user and return its id.

    Returns an explanatory message instead of raising when the address is
    already taken: rerunning the script must not look like a crash.
    """
    get_settings()  # fail early and clearly if .env is incomplete
    async with get_session_factory()() as session:
        users = UserRepository(session)
        if await users.exists_with_email(email):
            return f"Ya existe un usuario con {email}. No se hizo ningún cambio."

        user = await users.add(
            User(
                email=email,
                password_hash=hash_password(password),
                role=UserRole.ADMIN,
                client_id=None,
                is_active=True,
            )
        )
        await session.commit()
        return f"Administrador creado: {user.email} (id {user.id})"


async def main() -> int:
    print("Creación del administrador inicial")
    print(f"Base de datos: {get_settings().safe_database_url}\n")

    email = prompt_email()
    password = prompt_password()

    try:
        print("\n" + await create_admin(email, password))
    except PasswordTooLongError as exc:
        print(f"\nError: {exc}")
        return 1
    finally:
        await dispose_engine()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nCancelado.")
        sys.exit(130)
