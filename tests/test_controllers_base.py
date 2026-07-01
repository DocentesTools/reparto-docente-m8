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


def _make_user(role: RoleType | str, *, superuser: bool = False) -> UserModel:
    role_value = role.value if isinstance(role, RoleType) else role
    return UserModel(
        id=str(uuid.uuid4()),
        email="t@example.com",
        is_active=True,
        is_superuser=superuser,
        role=role_value,
    )


def test_require_writer_passes_for_superuser() -> None:
    user = _make_user("user", superuser=True)
    DomainController.require_writer(user)  # should not raise


def test_require_writer_passes_for_writer_role() -> None:
    user = _make_user("writer")
    DomainController.require_writer(user)


def test_require_writer_passes_for_admin_role() -> None:
    user = _make_user("admin")
    DomainController.require_writer(user)


def test_require_writer_passes_for_superadmin_role() -> None:
    user = _make_user("superadmin")
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
