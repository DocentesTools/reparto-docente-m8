"""API tests for the AssignmentProcess lifecycle endpoints.

Covers the state machine (``POST /transition``, ``POST /reopen``) and
the copy-from-previous-year endpoint (``POST /copy-from/{source_id}``)
introduced for the Phase 1 state machine (plan §8.4, §10.2, §14.1).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.enums import AssignmentProcessStatus
from tests import factories


# ── Transition (plan §8.4) ────────────────────────────────────────────────────


def test_transition_draft_to_ready_for_meeting(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/transition",
        json={"target_status": "ready_for_meeting"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready_for_meeting"
    assert resp.json()["closed_at"] is None


def test_transition_to_final_records_close_metadata(
    client: TestClient, session: Session, current_user
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.INTERNAL_REVISION
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/transition",
        json={"target_status": "final", "reason": "approved by leadership"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "final"
    assert body["closed_at"] is not None
    assert body["closed_by_user_id"] == str(current_user.id)


def test_transition_draft_to_final_is_rejected(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/transition",
        json={"target_status": "final"},
    )
    assert resp.status_code == 400
    assert "Illegal transition" in resp.json()["detail"]


def test_transition_final_to_reopen_must_use_reopen_endpoint(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/transition",
        json={"target_status": "reopened"},
    )
    assert resp.status_code == 400
    assert "reopen" in resp.json()["detail"].lower()


def test_transition_self_loop_rejected(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/transition",
        json={"target_status": "draft"},
    )
    assert resp.status_code == 400


def test_transition_returns_404_for_missing_process(
    client: TestClient,
) -> None:
    resp = client.post(
        f"/reparto/assignment-processes/{uuid.uuid4()}/transition",
        json={"target_status": "ready_for_meeting"},
    )
    assert resp.status_code == 404


def test_transition_blocks_reader(session: Session, reader_client: TestClient) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    resp = reader_client.post(
        f"/reparto/assignment-processes/{process.id}/transition",
        json={"target_status": "ready_for_meeting"},
    )
    assert resp.status_code == 403


def test_update_process_rejects_status_field(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}",
        json={"status": "ready_for_meeting"},
    )
    assert resp.status_code == 400
    assert "transition endpoint" in resp.json()["detail"]


# ── Reopen (plan §8.4) ──────────────────────────────────────────────────────


def test_reopen_final_to_reopened_clears_close_metadata(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    # Manually mark a close so the clear is observable.
    process.closed_at = datetime.now(tz=timezone.utc)
    process.closed_by_user_id = uuid.uuid4()
    session.add(process)
    session.commit()
    session.refresh(process)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/reopen",
        json={"reason": "leadership returned it"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "reopened"
    assert body["closed_at"] is None
    assert body["closed_by_user_id"] is None


def test_reopen_rejects_non_final_process(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/reopen",
        json={"reason": "too early"},
    )
    assert resp.status_code == 400
    assert "final" in resp.json()["detail"].lower()


def test_reopen_requires_reason(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/reopen",
        json={},
    )
    assert resp.status_code == 422  # pydantic min_length=1


# ── Copy from previous year (plan §14.1) ─────────────────────────────────────


def _populate_source_process(
    session: Session,
) -> tuple[AssignmentProcess, object, object]:
    source = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    profile_a = factories.make_teacher_profile(session, display_name="Alice")
    profile_b = factories.make_teacher_profile(session, display_name="Bob")
    pt_alice = factories.make_process_teacher(
        session,
        source,
        profile_a,
        available_hours=18,
        selection_position=1,
    )
    factories.make_process_teacher(
        session, source, profile_b, available_hours=20, selection_position=2
    )
    subject = factories.make_subject(session, source, name="Mathematics")
    group = factories.make_teaching_group(
        session, source, stage="ESO", grade=1, group_code="A", label="1 ESO A"
    )
    requirement = factories.make_hour_requirement(
        session, source, group, subject, required_hours=4
    )
    factories.make_assignment(
        session,
        source,
        requirement,
        pt_alice,
        assigned_hours=4,
    )
    return source, profile_a, profile_b


def _make_target_in_same_school(
    session: Session,
    source: AssignmentProcess,
    *,
    status: AssignmentProcessStatus,
) -> AssignmentProcess:
    """Create a target process that shares school / year / department with
    ``source`` so copy-from tests do not trip the school-scope guard."""
    from reparto_service.db_models.academic_years import AcademicYear
    from reparto_service.db_models.departments import Department
    from reparto_service.db_models.schools import School

    academic_year = session.get(AcademicYear, source.academic_year_id)
    school = session.get(School, source.school_id)
    department = session.get(Department, source.department_id)
    if academic_year is None or school is None or department is None:
        # Source was built by the factory which always inserts the three
        # parents; the assertion is here to surface drift in the factory.
        raise AssertionError("source process is missing its parents")
    return factories.make_assignment_process(
        session,
        academic_year=academic_year,
        school=school,
        department=department,
        status=status,
    )


def test_copy_from_copies_structure_only_by_default(
    client: TestClient, session: Session
) -> None:
    source, profile_a, profile_b = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_assignments": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created_from_process_id"] == str(source.id)
    # Subject, group, two teachers and one requirement copied; no assignments.
    resp = client.get(f"/reparto/assignment-processes/{target.id}/subjects/")
    assert resp.json()["count"] == 1
    resp = client.get(f"/reparto/assignment-processes/{target.id}/groups/")
    assert resp.json()["count"] == 1
    resp = client.get(f"/reparto/assignment-processes/{target.id}/teachers/")
    body = resp.json()
    assert body["count"] == 2
    assert {t["teacher_profile_id"] for t in body["data"]} == {
        str(profile_a.id),
        str(profile_b.id),
    }
    # Available hours reset to 0 on copy.
    assert all(t["available_hours"] == 0 for t in body["data"])
    # Selection-order fields preserved from the source.
    positions = sorted(t["selection_position"] for t in body["data"])
    assert positions == [1, 2]
    resp = client.get(f"/reparto/assignment-processes/{target.id}/requirements/")
    assert resp.json()["count"] == 1
    resp = client.get(f"/reparto/assignment-processes/{target.id}/assignments/")
    assert resp.json()["count"] == 0


def test_copy_from_with_assignments_copies_assignments(
    client: TestClient, session: Session
) -> None:
    source, _, _ = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_assignments": True},
    )
    assert resp.status_code == 200
    resp = client.get(f"/reparto/assignment-processes/{target.id}/assignments/")
    body = resp.json()
    assert body["count"] == 1
    # Copied assignments come back as DRAFT with SYSTEM_COPY source.
    assert body["data"][0]["status"] == "draft"
    assert body["data"][0]["source"] == "system_copy"
    assert body["data"][0]["chosen_by_user_id"] is None
    assert body["data"][0]["override_reason"] is None


def test_copy_from_rejects_non_draft_target(
    client: TestClient, session: Session
) -> None:
    source, _, _ = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.READY_FOR_MEETING
    )
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_assignments": False},
    )
    assert resp.status_code == 400
    assert "draft" in resp.json()["detail"]


def test_copy_from_rejects_target_with_existing_data(
    client: TestClient, session: Session
) -> None:
    source, _, _ = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    factories.make_subject(session, target, name="Pre-existing")
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_assignments": False},
    )
    assert resp.status_code == 400
    assert "subjects" in resp.json()["detail"]


def test_copy_from_rejects_target_with_existing_teachers(
    client: TestClient, session: Session
) -> None:
    source, _, _ = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    profile = factories.make_teacher_profile(session, display_name="Carl")
    factories.make_process_teacher(session, target, profile)
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_assignments": False},
    )
    assert resp.status_code == 400
    assert "teachers" in resp.json()["detail"]


def test_copy_from_rejects_self_target(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/copy-from/{process.id}",
        json={"copy_assignments": False},
    )
    assert resp.status_code == 400


def test_copy_from_rejects_different_schools(
    client: TestClient, session: Session
) -> None:
    school_a = factories.make_school(session, name="A")
    school_b = factories.make_school(session, name="B")
    year = factories.make_academic_year(session)
    dept_a = factories.make_department(session, school_a)
    dept_b = factories.make_department(session, school_b)
    source = factories.make_assignment_process(
        session,
        academic_year=year,
        school=school_a,
        department=dept_a,
        status=AssignmentProcessStatus.FINAL,
    )
    target = factories.make_assignment_process(
        session,
        academic_year=year,
        school=school_b,
        department=dept_b,
        status=AssignmentProcessStatus.DRAFT,
    )
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_assignments": False},
    )
    assert resp.status_code == 400
    assert "school" in resp.json()["detail"].lower()


def test_copy_from_404_for_missing_source(client: TestClient, session: Session) -> None:
    target = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{uuid.uuid4()}",
        json={"copy_assignments": False},
    )
    assert resp.status_code == 404


def test_copy_from_blocks_reader(session: Session, reader_client: TestClient) -> None:
    source, _, _ = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    resp = reader_client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_assignments": False},
    )
    assert resp.status_code == 403


# ── Final-close blocks assignment mutations (plan §8.4) ──────────────────────


def test_cannot_mutate_assignments_on_final_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(session, process, profile)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/assignments/",
        json={
            "assignment_process_id": str(process.id),
            "hour_requirement_id": str(requirement.id),
            "process_teacher_id": str(pt.id),
            "assigned_hours": 4,
        },
    )
    assert resp.status_code == 400
    assert "final" in resp.json()["detail"].lower()


# ── Process mutability guard (plan §8.4) ──────────────────────────────────────


def test_cannot_add_teacher_to_final_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    profile = factories.make_teacher_profile(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teachers/",
        json={
            "assignment_process_id": str(process.id),
            "teacher_profile_id": str(profile.id),
            "available_hours": 18,
        },
    )
    assert resp.status_code == 400
    assert "final" in resp.json()["detail"].lower()


def test_cannot_add_subject_to_final_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/subjects/",
        json={"assignment_process_id": str(process.id), "name": "Math"},
    )
    assert resp.status_code == 400
    assert "final" in resp.json()["detail"].lower()


def test_cannot_add_group_to_final_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/groups/",
        json={
            "assignment_process_id": str(process.id),
            "stage": "ESO",
            "grade": 1,
            "group_code": "A",
            "label": "1 ESO A",
        },
    )
    assert resp.status_code == 400
    assert "final" in resp.json()["detail"].lower()


def test_cannot_add_requirement_to_final_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    subject = factories.make_subject(session, process, name="Math")
    group = factories.make_teaching_group(
        session, process, stage="ESO", grade=1, group_code="A", label="1 ESO A"
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/requirements/",
        json={
            "assignment_process_id": str(process.id),
            "subject_id": str(subject.id),
            "teaching_group_id": str(group.id),
            "required_hours": 4,
        },
    )
    assert resp.status_code == 400
    assert "final" in resp.json()["detail"].lower()
