"""Tests for ``reparto_service.controllers.base.DomainController``."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from auth_sdk_m8.schemas.base import RoleType
from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.academic_years import AcademicYear
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.departments import Department


def _make_user(
    role: RoleType | str,
    *,
    user_id: uuid.UUID | None = None,
) -> UserModel:
    """Build a user whose privilege claims satisfy the auth-sdk invariant.

    ``is_superuser`` is derived from the role instead of being passed in:
    ``auth_sdk_m8`` rejects any pair where the two claims disagree
    (``SUPERADMIN`` pairs with ``is_superuser=True``, every other role with
    ``False``), so an inconsistent pair cannot be built here either.
    """
    role_value = role.value if isinstance(role, RoleType) else role
    return UserModel(
        id=str(user_id or uuid.uuid4()),
        email="t@example.com",
        is_active=True,
        is_superuser=role_value == RoleType.SUPERADMIN.value,
        role=role_value,
    )


def test_require_writer_passes_for_writer_role() -> None:
    user = _make_user("writer")
    DomainController.require_writer(user)


def test_require_writer_passes_for_admin_role() -> None:
    user = _make_user("admin")
    DomainController.require_writer(user)


def test_require_writer_passes_for_canonical_superadmin() -> None:
    """A ``SUPERADMIN`` (necessarily ``is_superuser``) clears the writer gate."""
    user = _make_user("superadmin")
    assert user.is_superuser is True
    DomainController.require_writer(user)


def test_require_writer_blocks_reader_role() -> None:
    user = _make_user("reader")
    with pytest.raises(HTTPException) as exc:
        DomainController.require_writer(user)
    assert exc.value.status_code == 403


def test_require_writer_blocks_user_role() -> None:
    user = _make_user("user")
    with pytest.raises(HTTPException) as exc:
        DomainController.require_writer(user)
    assert exc.value.status_code == 403


def test_require_writer_accepts_role_enum() -> None:
    user = _make_user(RoleType.WRITER)
    DomainController.require_writer(user)


@pytest.mark.parametrize("role", ["admin", "superadmin"])
def test_require_department_head_passes_for_admin_and_above(role: str) -> None:
    DomainController.require_department_head(_make_user(role))


@pytest.mark.parametrize("role", ["writer", "reader", "user"])
def test_require_department_head_blocks_everything_below_admin(role: str) -> None:
    with pytest.raises(HTTPException) as exc:
        DomainController.require_department_head(_make_user(role))
    assert exc.value.status_code == 403


def test_a_department_head_binding_no_longer_authorizes_anything(
    session: Session,
) -> None:
    """§21.2: ``department_head_user_id`` is attribution, not authorization.

    This is the behaviour change the section exists for. The bound account is
    the department's recorded head and still holds a sub-``ADMIN`` role, and it
    is now refused — a binding is not a credential, and cannot be revoked by
    demoting the account.
    """
    head_user_id = uuid.uuid4()
    process = __import__(
        "tests.factories", fromlist=["make_assignment_process"]
    ).make_assignment_process(session)
    department = session.get(Department, process.department_id)
    assert department is not None
    department.department_head_user_id = head_user_id
    session.add(department)
    session.commit()
    user = _make_user("writer", user_id=head_user_id)

    with pytest.raises(HTTPException) as exc:
        DomainController.require_department_head(user)
    assert exc.value.status_code == 403


def test_get_or_404_returns_item(session: Session) -> None:
    year = AcademicYear(
        label="2026/2027",
        start_date=__import__("datetime").date(2026, 9, 1),
        end_date=__import__("datetime").date(2027, 6, 30),
        created_by_user_id=uuid.uuid4(),
    )
    session.add(year)
    session.commit()
    session.refresh(year)
    result = DomainController.get_or_404(session, AcademicYear, year.id)
    assert result.id == year.id


def test_get_or_404_raises_when_missing(session: Session) -> None:
    with pytest.raises(HTTPException) as exc:
        DomainController.get_or_404(session, AcademicYear, uuid.uuid4())
    assert exc.value.status_code == 404


def test_get_process_or_404_returns_process(
    session: Session,
) -> None:
    process = __import__(
        "tests.factories", fromlist=["make_assignment_process"]
    ).make_assignment_process(session)
    result = DomainController.get_process_or_404(session, process.id)
    assert result.id == process.id


def test_get_process_or_404_raises_when_missing(
    session: Session,
) -> None:
    with pytest.raises(HTTPException) as exc:
        DomainController.get_process_or_404(session, uuid.uuid4())
    assert exc.value.status_code == 404


def test_get_process_or_404_returns_assignment_process_type(
    session: Session,
) -> None:
    process = __import__(
        "tests.factories", fromlist=["make_assignment_process"]
    ).make_assignment_process(session)
    result = DomainController.get_process_or_404(session, process.id)
    assert isinstance(result, AssignmentProcess)
