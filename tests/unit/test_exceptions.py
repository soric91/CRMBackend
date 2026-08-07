"""Domain exceptions and their HTTP translation."""

import pytest
from fastapi import APIRouter, FastAPI, status
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.core.exceptions import (
    AlreadyExistsError,
    AppError,
    AuthenticationError,
    AuthorizationError,
    BusinessRuleError,
    NotFoundError,
    ValidationError,
    register_exception_handlers,
)


class TestExceptionContract:
    @pytest.mark.parametrize(
        ("error", "expected_status", "expected_code"),
        [
            (NotFoundError, status.HTTP_404_NOT_FOUND, "not_found"),
            (AlreadyExistsError, status.HTTP_409_CONFLICT, "already_exists"),
            (
                ValidationError,
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "validation_error",
            ),
            (
                BusinessRuleError,
                status.HTTP_400_BAD_REQUEST,
                "business_rule_violation",
            ),
            (
                AuthenticationError,
                status.HTTP_401_UNAUTHORIZED,
                "authentication_failed",
            ),
            (AuthorizationError, status.HTTP_403_FORBIDDEN, "not_authorized"),
        ],
    )
    def test_status_and_code(
        self, error: type[AppError], expected_status: int, expected_code: str
    ) -> None:
        assert error.status_code == expected_status
        assert error.code == expected_code

    def test_all_errors_derive_from_app_error(self) -> None:
        assert issubclass(NotFoundError, AppError)

    def test_default_message_is_used_when_none_given(self) -> None:
        assert NotFoundError().message == "Resource not found"

    def test_custom_message_and_details_are_kept(self) -> None:
        error = NotFoundError("Client 7 not found", details={"client_id": 7})
        assert error.message == "Client 7 not found"
        assert error.details == {"client_id": 7}
        assert str(error) == "Client 7 not found"

    def test_details_default_to_empty_dict(self) -> None:
        assert NotFoundError().details == {}


class _Payload(BaseModel):
    quantity: int


def _app_with_failing_routes() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    router = APIRouter()

    @router.get("/domain-error")
    async def _domain_error() -> None:
        raise NotFoundError("Gateway not found", details={"gateway_id": 42})

    @router.get("/unexpected")
    async def _unexpected() -> None:
        raise RuntimeError("something exploded")

    @router.post("/validated")
    async def _validated(payload: _Payload) -> dict[str, int]:
        return {"quantity": payload.quantity}

    app.include_router(router)
    return app


@pytest.fixture
async def error_client() -> AsyncClient:
    transport = ASGITransport(app=_app_with_failing_routes())
    return AsyncClient(transport=transport, base_url="http://test", timeout=5.0)


class TestHandlers:
    async def test_domain_error_maps_to_its_status_and_payload(
        self, error_client: AsyncClient
    ) -> None:
        response = await error_client.get("/domain-error")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json() == {
            "error": {
                "code": "not_found",
                "message": "Gateway not found",
                "details": {"gateway_id": 42},
            }
        }

    async def test_unexpected_error_does_not_leak_internals(self) -> None:
        transport = ASGITransport(
            app=_app_with_failing_routes(), raise_app_exceptions=False
        )
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/unexpected")

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        body = response.json()
        assert body["error"]["code"] == "internal_error"
        assert "something exploded" not in response.text

    async def test_request_validation_error_uses_the_same_envelope(
        self, error_client: AsyncClient
    ) -> None:
        response = await error_client.post("/validated", json={"quantity": "abc"})

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        body = response.json()
        assert body["error"]["code"] == "validation_error"
        assert body["error"]["details"]["errors"]
