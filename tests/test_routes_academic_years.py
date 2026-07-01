"""API tests for ``/reparto/academic-years``."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.app.deps import get_current_user, get_db
from reparto_service.db_models.academic_years import AcademicYear
from reparto_service.enums import AcademicYearStatus
from reparto_service.main import app


# ── Helpers ──────────────────────────────────────────────────────────────────


def _override_auth(user: UserModel) -> None:
    def _u() -> UserModel:
        return user

    app.dependency_overrides[get_current_user] = _u


def _override_db(session: Session) -> None:
    def _d():
        yield session

    app.dependency_overrides[get_db] = _d


def _make_writer_client(session: Session, client: TestClient) -> TestClient:
    return client  # already overridden by the ``client`` fixture


# ── GET /academic-years/ ────────────────────────────────────────────────────


def test_list_years_empty(client: TestClient) -> None:
    resp = client.get("/reparto/academic-years/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["data"] == []


def test_list_years_returns_inserted(client: TestClient, session: Session) -> None:
    year = AcademicYear(
        label="2026/2027",
        start_date=date(2026, 9, 1),
        end_date=date(2027, 6, 30),
        created_by_user_id=uuid.uuid4(),
    )
    session.add(year)
    session.commit()
    session.refresh(year)
    resp = client.get("/reparto/academic-years/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["data"][0]["label"] == "2026/2027"


# ── POST /academic-years/ ───────────────────────────────────────────────────


def test_create_year_success(client: TestClient) -> None:
    resp = client.post(
        "/reparto/academic-years/",
        json={
            "label": "2027/2028",
            "start_date": "2027-09-01",
            "end_date": "2028-06-30",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["label"] == "2027/2028"
    assert body["status"] == AcademicYearStatus.ACTIVE.value


def test_create_year_rejects_inverted_dates(client: TestClient) -> None:
    resp = client.post(
        "/reparto/academic-years/",
        json={
            "label": "2027/2028",
            "start_date": "2028-09-01",
            "end_date": "2028-06-30",
        },
    )
    assert resp.status_code == 400
    assert "start_date" in resp.json()["detail"]


def test_create_year_rejects_reader_role(
    session: Session, reader_client: TestClient
) -> None:
    resp = reader_client.post(
        "/reparto/academic-years/",
        json={
            "label": "2027/2028",
            "start_date": "2027-09-01",
            "end_date": "2028-06-30",
        },
    )
    assert resp.status_code == 403


# ── GET /academic-years/{id} ────────────────────────────────────────────────


def test_get_year_found(client: TestClient, session: Session) -> None:
    year = AcademicYear(
        label="2026/2027",
        start_date=date(2026, 9, 1),
        end_date=date(2027, 6, 30),
        created_by_user_id=uuid.uuid4(),
    )
    session.add(year)
    session.commit()
    session.refresh(year)
    resp = client.get(f"/reparto/academic-years/{year.id}")
    assert resp.status_code == 200
    assert resp.json()["label"] == "2026/2027"


def test_get_year_not_found(client: TestClient) -> None:
    resp = client.get(f"/reparto/academic-years/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── PATCH /academic-years/{id} ───────────────────────────────────────────────


def test_update_year_success(client: TestClient, session: Session) -> None:
    year = AcademicYear(
        label="2026/2027",
        start_date=date(2026, 9, 1),
        end_date=date(2027, 6, 30),
        created_by_user_id=uuid.uuid4(),
    )
    session.add(year)
    session.commit()
    session.refresh(year)
    resp = client.patch(
        f"/reparto/academic-years/{year.id}",
        json={"label": "2026-2027"},
    )
    assert resp.status_code == 200
    assert resp.json()["label"] == "2026-2027"


# ── POST /academic-years/{id}/archive ────────────────────────────────────────


def test_archive_year(client: TestClient, session: Session) -> None:
    year = AcademicYear(
        label="2026/2027",
        start_date=date(2026, 9, 1),
        end_date=date(2027, 6, 30),
        created_by_user_id=uuid.uuid4(),
    )
    session.add(year)
    session.commit()
    session.refresh(year)
    resp = client.post(f"/reparto/academic-years/{year.id}/archive")
    assert resp.status_code == 200
    assert resp.json()["status"] == AcademicYearStatus.ARCHIVED.value
