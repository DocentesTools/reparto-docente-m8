"""Authorization-boundary sweeps over the whole route surface (plan §21).

These tests are deliberately written against the generated OpenAPI document
rather than a hand-kept list of paths: a route added tomorrow is swept the day
it is added, which is the only way a "no route may rely on bare authentication"
rule stays true after the commit that introduced it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from reparto_service.main import app

#: Framework-owned, deliberately public endpoints. Everything else under the
#: API prefix is domain surface and must sit behind the §21.1 reader floor.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/reparto/health/",
        "/reparto/meta",
        "/reparto/ping",
        "/reparto/openapi.json",
        "/reparto/docs",
        "/reparto/redoc",
        "/docs/oauth2-redirect",
    }
)


def domain_operations() -> Iterator[tuple[str, str]]:
    """Yield ``(method, path)`` for every non-public operation in the schema."""
    for path, operations in sorted(app.openapi()["paths"].items()):
        if path in PUBLIC_PATHS:
            continue
        for method in sorted(operations):
            yield method.upper(), path


DOMAIN_OPERATIONS: list[tuple[str, str]] = list(domain_operations())


def concrete(path: str) -> str:
    """Replace every ``{param}`` placeholder with a syntactically valid id."""
    while "{" in path:
        head, _, rest = path.partition("{")
        _, _, tail = rest.partition("}")
        path = f"{head}{uuid.uuid4()}{tail}"
    return path


def test_the_sweep_covers_the_whole_domain_surface() -> None:
    """Guard the guard: an empty sweep would pass every assertion below."""
    assert len(DOMAIN_OPERATIONS) > 100
    assert ("GET", "/reparto/assignment-processes/{process_id}/audit-events/") in (
        DOMAIN_OPERATIONS
    )
    assert (
        "POST",
        "/reparto/assignment-processes/{process_id}/exports/planning-draft",
    ) in DOMAIN_OPERATIONS


@pytest.mark.parametrize(("method", "path"), DOMAIN_OPERATIONS)
def test_every_domain_operation_declares_a_security_scheme(
    method: str, path: str
) -> None:
    """No operation may be reachable without presenting a token (`RBAC-01`).

    A missing ``security`` block is exactly the signature of the pre-§21 reads:
    a handler whose signature never mentioned the current user at all.
    """
    operation = app.openapi()["paths"][path][method.lower()]
    assert operation.get("security"), f"{method} {path} has no security requirement"


@pytest.mark.parametrize(("method", "path"), DOMAIN_OPERATIONS)
def test_unauthenticated_callers_are_rejected(
    unauth_client: TestClient, method: str, path: str
) -> None:
    """401 before anything else — including before body/path validation."""
    response = unauth_client.request(method, concrete(path))
    assert response.status_code == 401, (
        f"{method} {path} answered {response.status_code}"
    )


@pytest.mark.parametrize(("method", "path"), DOMAIN_OPERATIONS)
def test_user_role_has_no_capability_anywhere(
    user_client: TestClient, method: str, path: str
) -> None:
    """``USER`` is a platform identity with zero capability here (§21.1).

    Asserted for reads as well as mutations: the read floor is the whole point
    of the rule, and a 200 here would mean the floor is missing on that route.
    """
    response = user_client.request(method, concrete(path))
    assert response.status_code == 403, (
        f"{method} {path} answered {response.status_code}"
    )
