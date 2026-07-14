"""API tests for the nested assignment-process child resources."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from tests import factories


# ── Process teachers ────────────────────────────────────────────────────────


def test_create_process_teacher(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teachers/",
        json={
            "assignment_process_id": str(process.id),
            "teacher_profile_id": str(profile.id),
            "available_hours": 18,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["available_hours"] == 18
    assert body["status"] == "active"


def test_create_process_teacher_wrong_process_id(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teachers/",
        json={
            "assignment_process_id": str(uuid.uuid4()),
            "teacher_profile_id": str(profile.id),
        },
    )
    assert resp.status_code == 400


def test_list_process_teachers(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    p1 = factories.make_teacher_profile(session, display_name="A")
    p2 = factories.make_teacher_profile(session, display_name="B")
    factories.make_process_teacher(session, process, p1)
    factories.make_process_teacher(session, process, p2)
    resp = client.get(f"/reparto/assignment-processes/{process.id}/teachers/")
    assert resp.status_code == 200
    assert resp.json()["count"] == 2


def test_update_process_teacher(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(session, process, profile, available_hours=10)
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/teachers/{pt.id}",
        json={"available_hours": 12},
    )
    assert resp.status_code == 200
    assert resp.json()["available_hours"] == 12


def test_delete_process_teacher(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(session, process, profile)
    resp = client.delete(f"/reparto/assignment-processes/{process.id}/teachers/{pt.id}")
    assert resp.status_code == 200
    # Confirm gone (next get returns 404)
    resp = client.get(f"/reparto/assignment-processes/{process.id}/teachers/{pt.id}")
    assert resp.status_code == 404


# ── Subjects ────────────────────────────────────────────────────────────────


def test_create_subject(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/subjects/",
        json={"assignment_process_id": str(process.id), "name": "Math"},
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "Math"


def test_create_duplicate_subject_rejected(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    client.post(
        f"/reparto/assignment-processes/{process.id}/subjects/",
        json={"assignment_process_id": str(process.id), "name": "Math"},
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/subjects/",
        json={"assignment_process_id": str(process.id), "name": "Math"},
    )
    assert resp.status_code == 400


def test_update_subject(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process, name="Old")
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/subjects/{subject.id}",
        json={"name": "New"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"


def test_delete_subject(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    resp = client.delete(
        f"/reparto/assignment-processes/{process.id}/subjects/{subject.id}"
    )
    assert resp.status_code == 200


def test_create_subject_defaults_planning_fields(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/subjects/",
        json={"assignment_process_id": str(process.id), "name": "Math"},
    )
    assert resp.status_code == 201
    body = resp.json()
    # Sensible planning defaults per plan §5.3; no legacy ``stage`` field.
    assert "stage" not in body
    assert body["allocation_category"] == "main"
    assert body["activity_type"] == "ordinary"
    assert body["default_group_weekly_hours"] is None
    assert body["default_teacher_weekly_hours_per_position"] is None
    assert body["default_required_teacher_count"] == 1
    assert body["allows_multiple_groups"] is False
    assert body["allows_zero_groups"] is False


def test_create_subject_with_planning_fields(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/subjects/",
        json={
            "assignment_process_id": str(process.id),
            "name": "Co-teaching support",
            "allocation_category": "secondary",
            "activity_type": "co_teaching",
            "default_group_weekly_hours": 2.0,
            "default_teacher_weekly_hours_per_position": 2.0,
            "default_required_teacher_count": 2,
            "allows_multiple_groups": True,
            "allows_zero_groups": True,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["allocation_category"] == "secondary"
    assert body["activity_type"] == "co_teaching"
    assert body["default_group_weekly_hours"] == 2.0
    assert body["default_teacher_weekly_hours_per_position"] == 2.0
    assert body["default_required_teacher_count"] == 2
    assert body["allows_multiple_groups"] is True
    assert body["allows_zero_groups"] is True


def test_create_subject_rejects_zero_teacher_count(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/subjects/",
        json={
            "assignment_process_id": str(process.id),
            "name": "Math",
            "default_required_teacher_count": 0,
        },
    )
    assert resp.status_code == 422


def test_update_subject_planning_fields(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process, name="Old")
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/subjects/{subject.id}",
        json={
            "allocation_category": "secondary",
            "activity_type": "tutoring",
            "default_required_teacher_count": 3,
            "allows_zero_groups": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["allocation_category"] == "secondary"
    assert body["activity_type"] == "tutoring"
    assert body["default_required_teacher_count"] == 3
    assert body["allows_zero_groups"] is True


# ── Teaching groups ─────────────────────────────────────────────────────────


def test_create_teaching_group(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    stage = factories.make_classroom_stage(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/groups/",
        json={
            "assignment_process_id": str(process.id),
            "classroom_stage_id": str(stage.id),
            "grade": 1,
            "group_code": "A",
            "label": "1 ESO A",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["label"] == "1 ESO A"


def test_update_teaching_group(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    group = factories.make_teaching_group(session, process, label="Old")
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/groups/{group.id}",
        json={"label": "New"},
    )
    assert resp.status_code == 200
    assert resp.json()["label"] == "New"


# ── Hour requirements ──────────────────────────────────────────────────────


def test_create_hour_requirement(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/requirements/",
        json={
            "assignment_process_id": str(process.id),
            "teaching_group_id": str(group.id),
            "subject_id": str(subject.id),
            "required_hours": 4,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["required_hours"] == 4
    assert body["requirement_type"] == "ordinary"


def test_create_requirement_rejects_zero_hours(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/requirements/",
        json={
            "assignment_process_id": str(process.id),
            "teaching_group_id": str(group.id),
            "subject_id": str(subject.id),
            "required_hours": 0,
        },
    )
    assert resp.status_code == 422  # Pydantic validation


def test_update_requirement(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4
    )
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/requirements/{requirement.id}",
        json={"required_hours": 5},
    )
    assert resp.status_code == 200
    assert resp.json()["required_hours"] == 5


def test_delete_requirement_blocked_when_assignment_exists(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(session, process, profile)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4
    )
    factories.make_assignment(session, process, requirement, pt, assigned_hours=4)
    resp = client.delete(
        f"/reparto/assignment-processes/{process.id}/requirements/{requirement.id}"
    )
    assert resp.status_code == 400


# ── Assignments ─────────────────────────────────────────────────────────────


def _seed_full_process(
    session: Session,
) -> tuple:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(session, process, profile, available_hours=4)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4
    )
    return process, pt, requirement


def test_create_assignment(client: TestClient, session: Session) -> None:
    process, pt, requirement = _seed_full_process(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/assignments/",
        json={
            "assignment_process_id": str(process.id),
            "hour_requirement_id": str(requirement.id),
            "process_teacher_id": str(pt.id),
            "assigned_hours": 4,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["assigned_hours"] == 4
    assert body["source"] == "department_head"


def test_create_assignment_rejects_over_cap_without_override(
    client: TestClient, session: Session
) -> None:
    process, pt, requirement = _seed_full_process(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/assignments/",
        json={
            "assignment_process_id": str(process.id),
            "hour_requirement_id": str(requirement.id),
            "process_teacher_id": str(pt.id),
            "assigned_hours": 5,
        },
    )
    assert resp.status_code == 400
    assert "override" in resp.json()["detail"].lower()


def test_create_assignment_allows_over_cap_with_override(
    client: TestClient, session: Session
) -> None:
    process, pt, requirement = _seed_full_process(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/assignments/",
        json={
            "assignment_process_id": str(process.id),
            "hour_requirement_id": str(requirement.id),
            "process_teacher_id": str(pt.id),
            "assigned_hours": 5,
            "override_reason": "Head approved",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["override_reason"] == "Head approved"


def test_create_assignment_rejects_zero_hours(
    client: TestClient, session: Session
) -> None:
    process, pt, requirement = _seed_full_process(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/assignments/",
        json={
            "assignment_process_id": str(process.id),
            "hour_requirement_id": str(requirement.id),
            "process_teacher_id": str(pt.id),
            "assigned_hours": 0,
        },
    )
    assert resp.status_code == 422  # Pydantic validation


def test_create_assignment_rejects_teacher_from_other_process(
    client: TestClient, session: Session
) -> None:
    process_a = factories.make_assignment_process(session)
    process_b = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt_b = factories.make_process_teacher(
        session, process_b, profile, available_hours=10
    )
    subject = factories.make_subject(session, process_a)
    group = factories.make_teaching_group(session, process_a)
    requirement = factories.make_hour_requirement(
        session, process_a, group, subject, required_hours=4
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process_a.id}/assignments/",
        json={
            "assignment_process_id": str(process_a.id),
            "hour_requirement_id": str(requirement.id),
            "process_teacher_id": str(pt_b.id),
            "assigned_hours": 4,
        },
    )
    assert resp.status_code == 404


def test_list_assignments(client: TestClient, session: Session) -> None:
    process, pt, requirement = _seed_full_process(session)
    factories.make_assignment(session, process, requirement, pt, assigned_hours=4)
    resp = client.get(f"/reparto/assignment-processes/{process.id}/assignments/")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_get_assignment(client: TestClient, session: Session) -> None:
    process, pt, requirement = _seed_full_process(session)
    assignment = factories.make_assignment(
        session, process, requirement, pt, assigned_hours=4
    )
    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/assignments/{assignment.id}"
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == str(assignment.id)


def test_update_assignment(client: TestClient, session: Session) -> None:
    process, pt, requirement = _seed_full_process(session)
    assignment = factories.make_assignment(
        session, process, requirement, pt, assigned_hours=4
    )
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/assignments/{assignment.id}",
        json={"notes": "Updated"},
    )
    assert resp.status_code == 200
    assert resp.json()["notes"] == "Updated"


def test_delete_assignment(client: TestClient, session: Session) -> None:
    process, pt, requirement = _seed_full_process(session)
    assignment = factories.make_assignment(
        session, process, requirement, pt, assigned_hours=4
    )
    resp = client.delete(
        f"/reparto/assignment-processes/{process.id}/assignments/{assignment.id}"
    )
    assert resp.status_code == 200


def test_create_assignment_rejects_wrong_process_id_payload(
    client: TestClient, session: Session
) -> None:
    process, pt, requirement = _seed_full_process(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/assignments/",
        json={
            "assignment_process_id": str(uuid.uuid4()),
            "hour_requirement_id": str(requirement.id),
            "process_teacher_id": str(pt.id),
            "assigned_hours": 4,
        },
    )
    assert resp.status_code == 400


def test_create_assignment_blocked_on_final_process(
    client: TestClient, session: Session
) -> None:
    from reparto_service.enums import AssignmentProcessStatus

    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(session, process, profile, available_hours=4)
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
