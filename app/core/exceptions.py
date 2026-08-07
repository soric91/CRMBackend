"""Domain-level exceptions and their HTTP translation."""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for every error raised by the application layers.

    Carries an HTTP status and a stable machine-readable ``code`` so the API
    layer can translate it without knowing about the domain internals.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "Unexpected internal error"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Resource not found"


class AlreadyExistsError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "already_exists"
    message = "Resource already exists"


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"
    message = "Invalid input"


class BusinessRuleError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "business_rule_violation"
    message = "Business rule violated"


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_failed"
    message = "Invalid credentials"


class PasswordChangeRequiredError(AppError):
    """The caller holds a token that only works for changing its password.

    A code of its own, not a plain 403: the client web has to tell this apart
    from a permission problem and send the user to the right screen.
    """

    status_code = status.HTTP_403_FORBIDDEN
    code = "password_change_required"
    message = "You must change your password before continuing"


class AuthorizationError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "not_authorized"
    message = "Insufficient permissions"


def _error_payload(
    code: str, message: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the application-wide exception handlers to ``app``."""

    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "application error",
            extra={"error_code": exc.code, "error_message": exc.message},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_payload(
                "validation_error",
                "Request payload failed validation",
                # jsonable_encoder, not the raw list: when a custom validator
                # rejects a value, Pydantic puts the ValueError itself in the
                # error's `ctx`, which json.dumps cannot serialise.
                {"errors": jsonable_encoder(exc.errors())},
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_payload("internal_error", "Unexpected internal error"),
        )
