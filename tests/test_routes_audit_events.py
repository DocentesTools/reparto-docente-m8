"""API tests for process-scoped audit events."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from tests import factories


def _seed_assignment_process(session: Session) -> tuple:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    process_teacher = factories.make_process_teacher(session, process, profile)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(session, process, group, subject)
    return process, process_teacher, requirement


def test_assignment_create_update_and_delete_are_audited(
    client: TestClient, session: Session
) -> None:
    process, process_teacher, requirement = _seed_assignment_process(session)
    create_resp = client.post(
        f"/reparto/assignment-processes/{process.id}/assignments/",
        json={
            "assignment_process_id": str(process.id),
            "hour_requirement_id": str(requirement.id),
            "process_teacher_id": str(process_teacher.id),
            "assigned_hours": 4,
        },
    )
    assert create_resp.status_code == 201
    assignment_id = create_resp.json()["id"]

    update_resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/assignments/{assignment_id}",
        json={"assigned_hours": 5, "override_reason": "Head approved"},
    )
    assert update_resp.status_code == 200

    delete_resp = client.delete(
        f"/reparto/assignment-processes/{process.id}/assignments/{assignment_id}"
    )
    assert delete_resp.status_code == 200

    audit_resp = client.get(f"/reparto/assignment-processes/{process.id}/audit-events/")
    assert audit_resp.status_code == 200
    events = audit_resp.json()["data"]
    assert [event["event_type"] for event in events] == [
        "assignment.created",
        "assignment.updated",
        "assignment.deleted",
    ]
    assert events[0]["before_json"] is None
    assert events[0]["after_json"]["assigned_hours"] == 4.0
    assert events[1]["before_json"]["assigned_hours"] == 4.0
    assert events[1]["after_json"]["assigned_hours"] == 5.0
    assert events[1]["reason"] == "Head approved"
    assert events[2]["after_json"] is None


def test_setup_resource_mutations_are_audited(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    teacher_resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teachers/",
        json={
            "assignment_process_id": str(process.id),
            "teacher_profile_id": str(profile.id),
            "base_weekly_hours": 18,
        },
    )
    assert teacher_resp.status_code == 201

    subject_resp = client.post(
        f"/reparto/assignment-processes/{process.id}/subjects/",
        json={"assignment_process_id": str(process.id), "name": "Maths"},
    )
    assert subject_resp.status_code == 201

    stage = factories.make_classroom_stage(session)
    group_resp = client.post(
        f"/reparto/assignment-processes/{process.id}/groups/",
        json={
            "assignment_process_id": str(process.id),
            "classroom_stage_id": str(stage.id),
            "grade": 1,
            "group_code": "A",
            "label": "1 ESO A",
        },
    )
    assert group_resp.status_code == 201

    requirement_resp = client.post(
        f"/reparto/assignment-processes/{process.id}/requirements/",
        json={
            "assignment_process_id": str(process.id),
            "teaching_group_id": group_resp.json()["id"],
            "subject_id": subject_resp.json()["id"],
            "required_hours": 4,
        },
    )
    assert requirement_resp.status_code == 201

    audit_resp = client.get(f"/reparto/assignment-processes/{process.id}/audit-events/")
    assert audit_resp.status_code == 200
    assert [event["event_type"] for event in audit_resp.json()["data"]] == [
        "process_teacher.created",
        "subject.created",
        "teaching_group.created",
        "hour_requirement.created",
    ]


def test_process_lifecycle_audit_records_reason(
    client: TestClient, session: Session
) -> None:
    from reparto_service.enums import AssignmentProcessStatus

    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    transition_resp = client.post(
        f"/reparto/assignment-processes/{process.id}/transition",
        json={"target_status": "ready_for_meeting"},
    )
    assert transition_resp.status_code == 200

    audit_resp = client.get(f"/reparto/assignment-processes/{process.id}/audit-events/")
    assert audit_resp.status_code == 200
    event = audit_resp.json()["data"][0]
    assert event["event_type"] == "process.transitioned"
    assert event["before_json"]["status"] == "draft"
    assert event["after_json"]["status"] == "ready_for_meeting"
    assert event["reason"] == "ready_for_meeting"
