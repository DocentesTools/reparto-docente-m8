"""Issuer-backed user role lookup (transport layer, plan §21.2).

`Department.department_head_user_id` is descriptive metadata — it authorizes
nothing (§21.2) — but it is metadata a human will read as "this is who runs the
department", so it must stay accurate: it may only name an account that could
actually act as one, i.e. one holding at least ``ADMIN``.

This service does not own the user table and never reads the issuer's database
(``ARCH-NO-CROSS-SERVICE-DATA``). The only supported way to answer "what role
does this user id hold?" is the issuer's own HTTP contract, so this module calls
``GET {AUTH_PREFIX}/users/get/{user_id}/`` with the **caller's own** bearer
token. A lookup can therefore never see more than the caller already may — and
because that endpoint is superuser-gated at the issuer, an ``ADMIN`` caller can
confirm nobody but themselves. That is a deliberate consequence, not an
oversight: naming *another* account as head is a superuser act here, because
nothing weaker can verify the claim.

Fail closed. An unset issuer URL, a missing token, a timeout, a transport error,
a redirect or any unexpected status raises :class:`UserDirectoryUnavailable`,
and an unconfirmable target never becomes a recorded head. The error carries a
bounded, secret-free reason code only — never the bearer token, the target id or
the response body.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
from auth_sdk_m8.schemas.base import RoleType
from fastapi_m8 import ConsumerServiceSettings

# ``INTROSPECTION_URL`` points at ``…/private/v1/jti-status``; the user lookup
# lives at ``…/users/get/{user_id}/`` on the same host and API prefix. Mirrors
# fastapi-m8's ``derive_api_key_introspection_url`` so a deployment that already
# configures the issuer's base URL once need not repeat it.
_JTI_STATUS_SUFFIX = "/private/v1/jti-status"
_USER_LOOKUP_SUFFIX = "/users/get"

_OK = 200
_NOT_FOUND = 404

#: Resolves a user id to the role the issuer currently holds for it, ``None``
#: when the issuer does not know the id, or raises
#: :class:`UserDirectoryUnavailable`. Bound to one request's bearer token by
#: ``core.deps.get_user_role_lookup``.
UserRoleLookup = Callable[[uuid.UUID], RoleType | None]


class UserDirectoryUnavailable(RuntimeError):
    """Raised when a user id could not be resolved with the issuer.

    Attributes:
        reason: A bounded, secret-free reason code safe to log.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def derive_user_directory_url(introspection_url: str) -> str:
    """Derive the issuer's user-lookup base URL from the JTI-status URL.

    Args:
        introspection_url: The configured ``INTROSPECTION_URL``.

    Returns:
        The ``…/users/get`` base URL on the same issuer host and API prefix.
    """
    url = introspection_url.rstrip("/")
    url = url.removesuffix(_JTI_STATUS_SUFFIX)
    return url.rstrip("/") + _USER_LOOKUP_SUFFIX


class IssuerUserDirectory:
    """Resolve user ids to roles against the issuer's owned HTTP contract."""

    def __init__(
        self,
        base_url: str | None,
        *,
        connect_timeout: float = 2.0,
        read_timeout: float = 3.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Build a directory client.

        Args:
            base_url: The ``…/users/get`` base URL, or ``None`` when the issuer
                endpoint is not configured (every lookup then fails closed).
            connect_timeout: Connection timeout in seconds.
            read_timeout: Read timeout in seconds.
            transport: Optional httpx transport override (tests only).
        """
        self._base_url = base_url.rstrip("/") if base_url else None
        self._timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        self._transport = transport

    @classmethod
    def from_settings(cls, settings: ConsumerServiceSettings) -> IssuerUserDirectory:
        """Build the directory from the consumer's configured issuer URL.

        Args:
            settings: This service's settings.

        Returns:
            A directory bound to the derived lookup URL, or one that fails
            closed when ``INTROSPECTION_URL`` is unset (stateless deployments).
        """
        introspection_url = settings.INTROSPECTION_URL
        base_url = (
            derive_user_directory_url(str(introspection_url))
            if introspection_url
            else None
        )
        return cls(base_url)

    def role_of(self, user_id: uuid.UUID, *, bearer_token: str) -> RoleType | None:
        """Return the role the issuer currently holds for *user_id*.

        Args:
            user_id: The candidate department head.
            bearer_token: The caller's raw access token, forwarded verbatim.

        Returns:
            The user's current role, or ``None`` when the issuer answers 404.

        Raises:
            UserDirectoryUnavailable: On any outcome that is not a definitive
                answer — unconfigured endpoint, missing token, transport
                failure, an unreadable body, or an unexpected status (including
                the ``403`` a below-superuser caller receives).
        """
        if self._base_url is None:
            raise UserDirectoryUnavailable("user_directory_not_configured")
        if not bearer_token:
            raise UserDirectoryUnavailable("bearer_token_missing")
        try:
            with httpx.Client(
                timeout=self._timeout,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = client.get(
                    f"{self._base_url}/{user_id}/",
                    headers={"Authorization": f"Bearer {bearer_token}"},
                )
        except httpx.HTTPError as ex:
            raise UserDirectoryUnavailable("user_directory_transport") from ex
        if response.status_code == _NOT_FOUND:
            return None
        if response.status_code != _OK:
            raise UserDirectoryUnavailable("user_directory_status")
        try:
            role = response.json()["role"]
            return RoleType(role)
        except (ValueError, KeyError, TypeError) as ex:
            raise UserDirectoryUnavailable("user_directory_payload") from ex


__all__ = [
    "IssuerUserDirectory",
    "UserDirectoryUnavailable",
    "UserRoleLookup",
    "derive_user_directory_url",
]
