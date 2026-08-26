"""Tests for the issuer-backed user-role directory (plan §21.2).

Every case is driven through an ``httpx`` mock transport rather than a live
issuer: the point under test is this module's fail-closed decision table, not
the auth service's behaviour.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from auth_sdk_m8.schemas.base import RoleType

from reparto_service.services.user_directory import (
    IssuerUserDirectory,
    UserDirectoryUnavailable,
    derive_user_directory_url,
)

_ISSUER = "https://auth.example.test/auth/private/v1/jti-status"
_LOOKUP = "https://auth.example.test/auth/users/get"


def _directory(handler) -> IssuerUserDirectory:
    return IssuerUserDirectory(_LOOKUP, transport=httpx.MockTransport(handler))


def test_derive_url_strips_the_jti_status_suffix() -> None:
    assert derive_user_directory_url(_ISSUER) == _LOOKUP


def test_derive_url_accepts_a_bare_base_url() -> None:
    assert derive_user_directory_url("https://auth.example.test/auth/") == _LOOKUP


def test_from_settings_uses_the_configured_issuer() -> None:
    class _Settings:
        INTROSPECTION_URL = _ISSUER

    directory = IssuerUserDirectory.from_settings(_Settings())  # type: ignore[arg-type]
    assert directory._base_url == _LOOKUP


def test_from_settings_fails_closed_without_an_issuer_url() -> None:
    """A stateless deployment configures no issuer — so it confirms nobody."""

    class _Settings:
        INTROSPECTION_URL = None

    directory = IssuerUserDirectory.from_settings(_Settings())  # type: ignore[arg-type]
    with pytest.raises(UserDirectoryUnavailable) as exc:
        directory.role_of(uuid.uuid4(), bearer_token="t")
    assert exc.value.reason == "user_directory_not_configured"


def test_a_missing_bearer_token_is_refused_before_any_request() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request should be made without a token")

    with pytest.raises(UserDirectoryUnavailable) as exc:
        _directory(_handler).role_of(uuid.uuid4(), bearer_token="")
    assert exc.value.reason == "bearer_token_missing"


def test_the_caller_token_is_forwarded_verbatim() -> None:
    user_id = uuid.uuid4()
    seen: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"role": "admin"})

    assert _directory(_handler).role_of(user_id, bearer_token="tok") == RoleType.ADMIN
    assert seen["url"] == f"{_LOOKUP}/{user_id}/"
    assert seen["auth"] == "Bearer tok"


def test_an_unknown_user_is_a_definitive_no() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "User not found"})

    assert _directory(_handler).role_of(uuid.uuid4(), bearer_token="t") is None


@pytest.mark.parametrize("code", [401, 403, 500, 302])
def test_any_other_status_fails_closed(code: int) -> None:
    """Notably ``403``: the issuer's lookup is superuser-gated, so an admin
    caller cannot confirm somebody else and must not be allowed to guess."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json={})

    with pytest.raises(UserDirectoryUnavailable) as exc:
        _directory(_handler).role_of(uuid.uuid4(), bearer_token="t")
    assert exc.value.reason == "user_directory_status"


def test_a_transport_failure_fails_closed() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom")

    with pytest.raises(UserDirectoryUnavailable) as exc:
        _directory(_handler).role_of(uuid.uuid4(), bearer_token="t")
    assert exc.value.reason == "user_directory_transport"


@pytest.mark.parametrize(
    "body", [{"no_role": True}, {"role": "emperor"}, ["not", "an", "object"]]
)
def test_an_unreadable_payload_fails_closed(body: object) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with pytest.raises(UserDirectoryUnavailable) as exc:
        _directory(_handler).role_of(uuid.uuid4(), bearer_token="t")
    assert exc.value.reason == "user_directory_payload"


def test_the_reason_code_never_carries_the_token_or_the_target() -> None:
    user_id = uuid.uuid4()

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "secret"})

    with pytest.raises(UserDirectoryUnavailable) as exc:
        _directory(_handler).role_of(user_id, bearer_token="super-secret-token")
    message = str(exc.value)
    assert "super-secret-token" not in message
    assert str(user_id) not in message
    assert "secret" not in message
