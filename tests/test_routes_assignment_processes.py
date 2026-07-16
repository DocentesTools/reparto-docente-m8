"""API tests for ``/reparto/assignment-processes`` (CRUD, summary, dashboard)."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.db_models.academic_years import AcademicYear
from reparto_service.db_models.departments import Department
from reparto_service.db_models.schools import School
from reparto_service.enums import TeachingPlanStatus
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


def _seed_planned_process(session: Session):
    """Process + plan + one 4-hour single-position activity slot."""
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    activity = factories.make_teaching_activity(
        session,
        plan,
        subject,
        group_weekly_hours_per_group=4.0,
        teacher_weekly_hours_per_position=4.0,
    )
    slot = factories.make_hour_requirement(
        session, process, activity, required_teacher_hours=4.0
    )
    return process, plan, slot


def test_get_summary_process_without_plan(client: TestClient, session: Session) -> None:
    """A process still in setup has no plan: an empty section, never a 404."""
    process = factories.make_assignment_process(session)

    resp = client.get(f"/reparto/assignment-processes/{process.id}/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["process_id"] == str(process.id)
    assert body["plan_status"] is None
    assert body["plan_balance"] is None
    assert body["total_slots"] == 0
    assert body["blocking_validation_count"] == 0


def test_get_summary_reports_unassigned_slot(
    client: TestClient, session: Session
) -> None:
    """A live slot with no teacher is a blocking assignment finding (§3.6)."""
    process, _plan, _slot = _seed_planned_process(session)

    resp = client.get(f"/reparto/assignment-processes/{process.id}/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["plan_status"] == TeachingPlanStatus.DRAFT.value
    assert body["total_slots"] == 1
    assert body["assigned_slots"] == 0
    assert body["available_slots"] == 1
    assert body["blocking_validation_count"] >= 1


def test_get_dashboard_reports_both_stages_side_by_side(
    client: TestClient, session: Session
) -> None:
    """Planning and assignment are reported on separate axes, never summed (§3.2)."""
    process, plan, slot = _seed_planned_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4.0
    )
    factories.make_assignment(session, process, slot, pt)

    resp = client.get(f"/reparto/assignment-processes/{process.id}/dashboard")

    assert resp.status_code == 200
    body = resp.json()
    assert body["process_id"] == str(process.id)
    # Planning section: its own balances and findings.
    assert body["planning"]["teaching_plan_id"] == str(plan.id)
    assert body["planning"]["balance"]["teacher"]["total_teacher_load"] == "4.00"
    assert body["planning"]["validations"] is not None
    # Assignment section: the participant occupies the slot in full.
    participants = body["assignment"]["summary"]["participants"]
    assert len(participants) == 1
    assert participants[0]["process_teacher_id"] == str(pt.id)
    assert participants[0]["assigned_weekly_hours"] == "4.00"
    assert participants[0]["remaining_weekly_hours"] == "0.00"
    assert body["assignment"]["summary"]["assigned_slots"] == 1
    assert body["assignment"]["summary"]["available_slots"] == 0
    # Every slot covered and every participant on target: nothing blocks.
    assert body["assignment"]["validations"]["is_final_ready"] is True


def test_teacher_lan_summary_returns_only_the_callers_own_row(
    client: TestClient, session: Session, current_user
) -> None:
    """Another participant's hours must never reach a teacher (§8.6, §20.25)."""
    process, _plan, _slot = _seed_planned_process(session)
    linked_profile = factories.make_teacher_profile(
        session,
        display_name="Linked Teacher",
        user_id=uuid.UUID(str(current_user.id)),
    )
    other_profile = factories.make_teacher_profile(session, display_name="Other")
    linked_teacher = factories.make_process_teacher(
        session, process, linked_profile, base_weekly_hours=4.0
    )
    other_teacher = factories.make_process_teacher(
        session, process, other_profile, base_weekly_hours=8.0
    )

    resp = client.get(f"/reparto/assignment-processes/{process.id}/lan/me")

    assert resp.status_code == 200
    body = resp.json()
    assert body["teacher_profile_id"] == str(linked_profile.id)
    assert body["process_teacher_id"] == str(linked_teacher.id)
    # The payload carries one participant row, and it is the caller's own.
    assert body["participant"]["process_teacher_id"] == str(linked_teacher.id)
    assert body["participant"]["display_name"] == "Linked Teacher"
    assert body["participant"]["target_weekly_hours"] == "4.00"
    # The other participant is never named or identified anywhere in the payload.
    assert "Other" not in resp.text
    assert str(other_teacher.id) not in resp.text
    assert str(other_profile.id) not in resp.text
    # The plan balance is aggregate — it names no teacher — and the shared screen
    # shows the same two figures (§8.7), so it is LAN-safe.
    assert body["plan_balance"]["teacher"]["participant_target_total"] == "12.00"
    assert body["available_slots"] == 1


def test_teacher_lan_summary_requires_linked_profile(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.get(f"/reparto/assignment-processes/{process.id}/lan/me")
    assert resp.status_code == 404
    assert "linked" in resp.json()["detail"]


def test_summary_returns_404_for_missing_process(
    client: TestClient,
) -> None:
    resp = client.get(f"/reparto/assignment-processes/{uuid.uuid4()}/summary")
    assert resp.status_code == 404
