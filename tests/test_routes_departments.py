"""API tests for ``/reparto/departments``."""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from auth_sdk_m8.schemas.base import RoleType
from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.core.deps import get_user_role_lookup
from reparto_service.main import app
from reparto_service.services.user_directory import (
    UserDirectoryUnavailable,
    UserRoleLookup,
)
from tests import factories


def test_list_departments_empty(client: TestClient) -> None:
    resp = client.get("/reparto/departments/")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_create_department_success(client: TestClient, session: Session) -> None:
    school = factories.make_school(session)
    resp = client.post(
        "/reparto/departments/",
        json={"school_id": str(school.id), "name": "Matemáticas"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Matemáticas"
    assert body["slug"] == "matematicas"


def test_create_department_missing_school(client: TestClient) -> None:
    resp = client.post(
        "/reparto/departments/",
        json={"school_id": str(uuid.uuid4()), "name": "X"},
    )
    assert resp.status_code == 404


def test_filter_departments_by_school(client: TestClient, session: Session) -> None:
    s1 = factories.make_school(session, name="A")
    s2 = factories.make_school(session, name="B")
    factories.make_department(session, s1, name="D1")
    factories.make_department(session, s2, name="D2")
    resp = client.get(f"/reparto/departments/?school_id={s1.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["data"][0]["name"] == "D1"


def test_update_department(client: TestClient, session: Session) -> None:
    school = factories.make_school(session)
    dept = factories.make_department(session, school, name="Old")
    resp = client.patch(
        f"/reparto/departments/{dept.id}",
        json={"name": "New"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"


# ── department_head_user_id accuracy (plan §21.2) ────────────────────────────


@pytest.fixture
def role_lookup() -> Generator[dict[str, object]]:
    """Override the issuer lookup with a scripted answer.

    Set ``state["role"]`` to a ``RoleType`` for a known account, ``None`` for an
    id the issuer does not know, or ``state["raises"]`` to simulate an issuer
    that could not be consulted.
    """
    state: dict[str, object] = {"role": RoleType.ADMIN, "raises": None}

    def _override() -> UserRoleLookup:
        def _lookup(user_id: uuid.UUID) -> RoleType | None:
            if state["raises"] is not None:
                raise UserDirectoryUnavailable(str(state["raises"]))
            return state["role"]  # type: ignore[return-value]

        return _lookup

    app.dependency_overrides[get_user_role_lookup] = _override
    yield state
    app.dependency_overrides.pop(get_user_role_lookup, None)


def test_an_admin_may_be_recorded_as_department_head(
    client: TestClient, session: Session, role_lookup: dict[str, object]
) -> None:
    school = factories.make_school(session)
    dept = factories.make_department(session, school)
    head = uuid.uuid4()

    resp = client.patch(
        f"/reparto/departments/{dept.id}",
        json={"department_head_user_id": str(head)},
    )
    assert resp.status_code == 200
    assert resp.json()["department_head_user_id"] == str(head)


@pytest.mark.parametrize("role", [RoleType.WRITER, RoleType.READER, RoleType.USER])
def test_an_account_below_admin_cannot_be_recorded_as_head(
    client: TestClient,
    session: Session,
    role_lookup: dict[str, object],
    role: RoleType,
) -> None:
    """§21.2: the field is descriptive, so it must describe something true."""
    school = factories.make_school(session)
    dept = factories.make_department(session, school)
    role_lookup["role"] = role

    resp = client.patch(
        f"/reparto/departments/{dept.id}",
        json={"department_head_user_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 400
    assert role.value in resp.json()["detail"]
    session.refresh(dept)
    assert dept.department_head_user_id is None


def test_an_unknown_account_cannot_be_recorded_as_head(
    client: TestClient, session: Session, role_lookup: dict[str, object]
) -> None:
    school = factories.make_school(session)
    dept = factories.make_department(session, school)
    role_lookup["role"] = None

    resp = client.patch(
        f"/reparto/departments/{dept.id}",
        json={"department_head_user_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 400
    assert "does not know this user" in resp.json()["detail"]


def test_an_unreachable_issuer_leaves_the_head_unchanged(
    client: TestClient, session: Session, role_lookup: dict[str, object]
) -> None:
    """Fail closed: an unconfirmable head is never recorded."""
    school = factories.make_school(session)
    dept = factories.make_department(session, school)
    role_lookup["raises"] = "user_directory_transport"

    resp = client.patch(
        f"/reparto/departments/{dept.id}",
        json={"department_head_user_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 503
    assert "user_directory_transport" in resp.json()["detail"]
    session.refresh(dept)
    assert dept.department_head_user_id is None


def test_clearing_the_head_needs_no_confirmation(
    client: TestClient, session: Session, role_lookup: dict[str, object]
) -> None:
    """A department whose head has left must not be stranded by the check."""
    school = factories.make_school(session)
    dept = factories.make_department(session, school)
    dept.department_head_user_id = uuid.uuid4()
    session.add(dept)
    session.commit()
    role_lookup["raises"] = "user_directory_transport"

    resp = client.patch(
        f"/reparto/departments/{dept.id}",
        json={"department_head_user_id": None},
    )
    assert resp.status_code == 200
    assert resp.json()["department_head_user_id"] is None


def test_the_head_is_validated_on_creation_too(
    client: TestClient, session: Session, role_lookup: dict[str, object]
) -> None:
    school = factories.make_school(session)
    role_lookup["role"] = RoleType.READER

    resp = client.post(
        "/reparto/departments/",
        json={
            "school_id": str(school.id),
            "name": "Física",
            "department_head_user_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 400


def test_an_update_that_does_not_touch_the_head_skips_the_lookup(
    client: TestClient, session: Session, role_lookup: dict[str, object]
) -> None:
    school = factories.make_school(session)
    dept = factories.make_department(session, school, name="Old")
    role_lookup["raises"] = "user_directory_transport"

    resp = client.patch(f"/reparto/departments/{dept.id}", json={"name": "New"})
    assert resp.status_code == 200
