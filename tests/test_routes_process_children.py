"""API tests for the nested assignment-process child resources."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.enums import AssignmentProcessStatus, AssignmentStatus
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
            "base_weekly_hours": 18,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["base_weekly_hours"] == 18
    assert body["extra_weekly_hours"] == 0
    assert body["target_weekly_hours"] == 18
    assert body["is_overloaded"] is False
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
    pt = factories.make_process_teacher(session, process, profile, base_weekly_hours=10)
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/teachers/{pt.id}",
        json={"base_weekly_hours": 12},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["base_weekly_hours"] == 12
    assert body["target_weekly_hours"] == 12


def test_delete_process_teacher(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(session, process, profile)
    resp = client.delete(f"/reparto/assignment-processes/{process.id}/teachers/{pt.id}")
    assert resp.status_code == 200
    # Confirm gone (next get returns 404)
    resp = client.get(f"/reparto/assignment-processes/{process.id}/teachers/{pt.id}")
    assert resp.status_code == 404


# ── Process-teacher extra hours (plan §3.8/§7.6) ────────────────────────────


def test_generic_patch_cannot_change_extra_hours(
    client: TestClient, session: Session
) -> None:
    """The generic PATCH must not bypass the audited extra-hours path."""
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(session, process, profile, base_weekly_hours=10)
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/teachers/{pt.id}",
        json={"base_weekly_hours": 12, "extra_weekly_hours": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["base_weekly_hours"] == 12
    # extra_weekly_hours is not part of the update schema: silently ignored.
    assert body["extra_weekly_hours"] == 0
    assert body["target_weekly_hours"] == 12
    assert body["is_overloaded"] is False


def test_update_extra_hours_success_and_audited(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(session, process, profile, base_weekly_hours=10)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teachers/{pt.id}/extra-hours",
        json={"extra_weekly_hours": 4, "reason": "Cover maternity leave"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["base_weekly_hours"] == 10
    assert body["extra_weekly_hours"] == 4
    assert body["target_weekly_hours"] == 14
    assert body["is_overloaded"] is True
    assert body["extra_hours_reason"] == "Cover maternity leave"
    assert body["extra_hours_updated_by_user_id"] is not None
    assert body["extra_hours_updated_at"] is not None

    audit = client.get(f"/reparto/assignment-processes/{process.id}/audit-events/")
    events = [
        event
        for event in audit.json()["data"]
        if event["event_type"] == "process_teacher.extra_hours_updated"
    ]
    assert len(events) == 1
    assert events[0]["reason"] == "Cover maternity leave"


def test_update_extra_hours_to_zero_clears_overload(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=10, extra_weekly_hours=4
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teachers/{pt.id}/extra-hours",
        json={"extra_weekly_hours": 0, "reason": "Overload no longer needed"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["extra_weekly_hours"] == 0
    assert body["target_weekly_hours"] == 10
    assert body["is_overloaded"] is False


def test_update_extra_hours_requires_reason(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(session, process, profile)
    missing = client.post(
        f"/reparto/assignment-processes/{process.id}/teachers/{pt.id}/extra-hours",
        json={"extra_weekly_hours": 4},
    )
    assert missing.status_code == 422
    empty = client.post(
        f"/reparto/assignment-processes/{process.id}/teachers/{pt.id}/extra-hours",
        json={"extra_weekly_hours": 4, "reason": ""},
    )
    assert empty.status_code == 422


def test_update_extra_hours_blocked_below_assigned(
    client: TestClient, session: Session
) -> None:
    """Reducing extra hours below the occupied slot hours is refused (§3.8)."""
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=10, extra_weekly_hours=4
    )
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    activity = factories.make_teaching_activity(
        session, plan, subject, teacher_weekly_hours_per_position=12.0
    )
    # 12 active assigned hours against a target of 14; dropping the 4 extra
    # hours would leave a target of 10 — below what the teacher already holds.
    slot = factories.make_hour_requirement(
        session, process, activity, required_teacher_hours=12.0
    )
    factories.make_assignment(session, process, slot, pt)

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teachers/{pt.id}/extra-hours",
        json={"extra_weekly_hours": 0, "reason": "Try to drop below assigned"},
    )

    assert resp.status_code == 400
    assert "assigned" in resp.json()["detail"].lower()
    # Value unchanged after the blocked attempt.
    current = client.get(f"/reparto/assignment-processes/{process.id}/teachers/{pt.id}")
    assert current.json()["extra_weekly_hours"] == 4


def test_update_extra_hours_ignores_cancelled_assignment(
    client: TestClient, session: Session
) -> None:
    """A cancelled assignment holds no hours, so it cannot block a reduction."""
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=10, extra_weekly_hours=4
    )
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    activity = factories.make_teaching_activity(
        session, plan, subject, teacher_weekly_hours_per_position=12.0
    )
    slot = factories.make_hour_requirement(
        session, process, activity, required_teacher_hours=12.0
    )
    factories.make_assignment(
        session, process, slot, pt, status=AssignmentStatus.CANCELLED
    )

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teachers/{pt.id}/extra-hours",
        json={"extra_weekly_hours": 0, "reason": "Overload no longer needed"},
    )

    assert resp.status_code == 200
    assert resp.json()["extra_weekly_hours"] == 0
    assert resp.json()["target_weekly_hours"] == 10


def test_update_extra_hours_reader_forbidden(
    reader_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(session, process, profile)
    resp = reader_client.post(
        f"/reparto/assignment-processes/{process.id}/teachers/{pt.id}/extra-hours",
        json={"extra_weekly_hours": 4, "reason": "Not allowed"},
    )
    assert resp.status_code == 403


def test_update_extra_hours_final_process_blocked(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(session, process, profile)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teachers/{pt.id}/extra-hours",
        json={"extra_weekly_hours": 4, "reason": "Process is final"},
    )
    assert resp.status_code == 400
    assert "final" in resp.json()["detail"].lower()


def test_update_extra_hours_teacher_not_found(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teachers/{uuid.uuid4()}/extra-hours",
        json={"extra_weekly_hours": 4, "reason": "No such teacher"},
    )
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


# ── Hour requirements and assignments ───────────────────────────────────────
#
# Requirement slots are generated from the teaching plan, never hand-created, so
# this file no longer drives requirement CRUD; the removal of those routes is
# asserted by ``test_routes_hour_requirements.py::
# test_manual_mutation_routes_removed`` and generation is covered by
# ``test_routes_requirement_generation.py``. The complete-slot assignment
# surface (create/list/get/update/cancel plus every §20.9 invariant) lives in
# ``test_routes_assignments.py``.
