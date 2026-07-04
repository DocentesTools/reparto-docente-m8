"""API tests for ``/reparto/assignment-processes`` (CRUD, summary, dashboard)."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.db_models.academic_years import AcademicYear
from reparto_service.db_models.departments import Department
from reparto_service.db_models.schools import School
from tests import factories


def _seed_dependencies(
    session: Session,
) -> tuple[AcademicYear, School, Department]:
    school = factories.make_school(session)
    dept = factories.make_department(session, school)
    year = AcademicYear(
        label="2026/2027",
        start_date=date(2026, 9, 1),
        end_date=date(2027, 6, 30),
        created_by_user_id=uuid.uuid4(),
    )
    session.add(year)
    session.commit()
    session.refresh(year)
    return year, school, dept


# ── CRUD ─────────────────────────────────────────────────────────────────────


def test_list_processes_empty(client: TestClient) -> None:
    resp = client.get("/reparto/assignment-processes/")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_create_process_success(client: TestClient, session: Session) -> None:
    year, school, dept = _seed_dependencies(session)
    resp = client.post(
        "/reparto/assignment-processes/",
        json={
            "academic_year_id": str(year.id),
            "school_id": str(school.id),
            "department_id": str(dept.id),
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "draft"
    assert body["selection_order_enabled"] is False


def test_create_process_missing_year(client: TestClient, session: Session) -> None:
    school = factories.make_school(session)
    dept = factories.make_department(session, school)
    resp = client.post(
        "/reparto/assignment-processes/",
        json={
            "academic_year_id": str(uuid.uuid4()),
            "school_id": str(school.id),
            "department_id": str(dept.id),
        },
    )
    assert resp.status_code == 404


def test_create_process_blocks_reader(
    session: Session, reader_client: TestClient
) -> None:
    year, school, dept = _seed_dependencies(session)
    resp = reader_client.post(
        "/reparto/assignment-processes/",
        json={
            "academic_year_id": str(year.id),
            "school_id": str(school.id),
            "department_id": str(dept.id),
        },
    )
    assert resp.status_code == 403


def test_get_process(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.get(f"/reparto/assignment-processes/{process.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(process.id)


def test_get_process_not_found(client: TestClient) -> None:
    resp = client.get(f"/reparto/assignment-processes/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_update_process(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}",
        json={"selection_order_enabled": True},
    )
    assert resp.status_code == 200
    assert resp.json()["selection_order_enabled"] is True


# ── Summary / Dashboard ─────────────────────────────────────────────────────


def test_get_summary_empty_process(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.get(f"/reparto/assignment-processes/{process.id}/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["process_id"] == str(process.id)
    assert body["global_balance"]["total_required_hours"] == 0
    assert body["blocking_validation_count"] == 0


def test_get_summary_uncovered_requirement(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    resp = client.get(f"/reparto/assignment-processes/{process.id}/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["global_balance"]["state"] == "pending"
    assert body["blocking_validation_count"] >= 1


def test_get_dashboard_combines_everything(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(session, process, profile, available_hours=4.0)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    factories.make_assignment(session, process, requirement, pt, assigned_hours=4.0)
    resp = client.get(f"/reparto/assignment-processes/{process.id}/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["process_id"] == str(process.id)
    assert len(body["teacher_balances"]) == 1
    assert len(body["requirement_balances"]) == 1
    assert body["global_balance"]["state"] == "balanced"


def test_teacher_lan_summary_returns_only_linked_teacher(
    client: TestClient, session: Session, current_user
) -> None:
    process = factories.make_assignment_process(session)
    linked_profile = factories.make_teacher_profile(
        session,
        display_name="Linked Teacher",
        user_id=uuid.UUID(str(current_user.id)),
    )
    other_profile = factories.make_teacher_profile(session, display_name="Other")
    linked_teacher = factories.make_process_teacher(
        session, process, linked_profile, available_hours=4.0
    )
    factories.make_process_teacher(session, process, other_profile, available_hours=8.0)
    resp = client.get(f"/reparto/assignment-processes/{process.id}/lan/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["teacher_profile_id"] == str(linked_profile.id)
    assert body["process_teacher_id"] == str(linked_teacher.id)
    assert body["teacher_balance"]["display_name"] == "Linked Teacher"


def test_teacher_lan_summary_requires_linked_profile(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.get(f"/reparto/assignment-processes/{process.id}/lan/me")
    assert resp.status_code == 404
    assert "linked" in resp.json()["detail"]


def test_process_summary_event_stream(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    with client.stream(
        "GET", f"/reparto/assignment-processes/{process.id}/events"
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = "".join(resp.iter_text())
    assert "event: process.summary" in text
    assert f'"process_id":"{process.id}"' in text


def test_summary_returns_404_for_missing_process(
    client: TestClient,
) -> None:
    resp = client.get(f"/reparto/assignment-processes/{uuid.uuid4()}/summary")
    assert resp.status_code == 404
