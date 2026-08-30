"""Tests for the redesigned complete-slot ``Assignment`` (plan §5.10, §20.9).

An assignment binds one process teacher to one indivisible requirement slot in
full. There is no ``assigned_hours``, no shared/partial coverage and no
over-assignment override. Both the department-head manual path and the teacher
LAN direct-choice path go through one shared complete-slot routine, and the two
active partial-unique indexes (one live assignment per requirement; distinct
teacher per activity) plus the composite FK enforce the invariants at the DB.
"""

from __future__ import annotations

import uuid

import pytest
from auth_sdk_m8.schemas.user import UserModel
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.audit_events import AuditEvent
from reparto_service.db_models.feasibility_witnesses import FeasibilityWitness
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    AssignmentProcessStatus,
    AssignmentSource,
    AssignmentStatus,
    FeasibilityStatus,
    HourRequirementStatus,
    MeetingSessionStatus,
    ProcessTeacherStatus,
    SelectionOrderMode,
    SelectionTurnStatus,
    TeachingPlanStatus,
)
from tests import factories


def _set_plan_status(
    session: Session, process_id: uuid.UUID, plan_status: TeachingPlanStatus
) -> None:
    """Force the process's teaching plan into ``plan_status`` for gate tests."""
    plan = session.exec(
        select(TeachingPlan).where(TeachingPlan.assignment_process_id == process_id)
    ).one()
    plan.status = plan_status
    session.add(plan)
    session.commit()


def _plan_setup(session: Session, *, required_teacher_count: int = 2):
    """Process + plan + subject + one co-teaching activity with two slots."""
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    activity = factories.make_teaching_activity(
        session, plan, subject, required_teacher_count=required_teacher_count
    )
    slot0 = factories.make_hour_requirement(
        session, process, activity, position_index=0
    )
    slot1 = factories.make_hour_requirement(
        session, process, activity, position_index=1
    )
    return process, activity, slot0, slot1


def _make_teacher(session: Session, process, *, selection_position=None):
    profile = factories.make_teacher_profile(session)
    return factories.make_process_teacher(
        session, process, profile, selection_position=selection_position
    )


def _assignments_path(process_id) -> str:
    return f"/reparto/assignment-processes/{process_id}/assignments"


def _undo_path(process_id, assignment_id) -> str:
    return f"{_assignments_path(process_id)}/{assignment_id}/undo"


def _reassign_path(process_id, assignment_id) -> str:
    return f"{_assignments_path(process_id)}/{assignment_id}/reassign"


# ── Manual (department-head) create ───────────────────────────────────────────


def test_create_assignment_occupies_slot(client: TestClient, session: Session) -> None:
    process, activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(teacher.id),
            "notes": "manual",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == AssignmentStatus.ACTIVE.value
    assert body["source"] == AssignmentSource.DEPARTMENT_HEAD.value
    # Activity is denormalised server-side from the requirement (plan §20.9).
    assert body["teaching_activity_id"] == str(activity.id)
    assert body["confirmed_by_user_id"] is None
    # The slot flips to ASSIGNED.
    session.refresh(slot0)
    assert slot0.status == HourRequirementStatus.ASSIGNED


def test_create_assignment_immutable_process(
    client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    process.status = AssignmentProcessStatus.FINAL
    session.add(process)
    session.commit()
    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(teacher.id),
        },
    )
    assert resp.status_code == 400
    assert "reopen" in resp.json()["detail"]


def test_create_assignment_unknown_requirement(
    client: TestClient, session: Session
) -> None:
    process, _activity, _slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(uuid.uuid4()),
            "process_teacher_id": str(teacher.id),
        },
    )
    assert resp.status_code == 404
    assert "HourRequirement" in resp.json()["detail"]


def test_create_assignment_requirement_other_process(
    client: TestClient, session: Session
) -> None:
    process, _activity, _slot0, _slot1 = _plan_setup(session)
    other_process, _oa, other_slot, _os1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(other_slot.id),
            "process_teacher_id": str(teacher.id),
        },
    )
    assert resp.status_code == 404


def test_create_assignment_unknown_teacher(
    client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 404
    assert "ProcessTeacher" in resp.json()["detail"]


def test_create_assignment_teacher_other_process(
    client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    other_process = factories.make_assignment_process(session)
    other_teacher = _make_teacher(session, other_process)
    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(other_teacher.id),
        },
    )
    assert resp.status_code == 404


def test_create_assignment_requirement_not_available(
    client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    slot0.status = HourRequirementStatus.STALE
    session.add(slot0)
    session.commit()
    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(teacher.id),
        },
    )
    assert resp.status_code == 400
    assert "not available" in resp.json()["detail"]


def test_create_assignment_slot_already_assigned(
    client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    first = _make_teacher(session, process)
    factories.make_assignment(session, process, slot0, first)
    slot0.status = HourRequirementStatus.ASSIGNED
    session.add(slot0)
    session.commit()
    second = _make_teacher(session, process)
    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(second.id),
        },
    )
    # Requirement is no longer AVAILABLE, so the status guard trips first.
    assert resp.status_code == 400
    assert "not available" in resp.json()["detail"]


def test_create_assignment_slot_already_assigned_available_status(
    client: TestClient, session: Session
) -> None:
    """Belt-and-suspenders: a live assignment blocks even if the slot's status
    still reads AVAILABLE (the one-active-per-requirement guard)."""
    process, _activity, slot0, _slot1 = _plan_setup(session)
    first = _make_teacher(session, process)
    factories.make_assignment(session, process, slot0, first)
    # Leave slot0.status == AVAILABLE to exercise _ensure_slot_unassigned.
    second = _make_teacher(session, process)
    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(second.id),
        },
    )
    assert resp.status_code == 400
    assert "already assigned" in resp.json()["detail"]


def test_create_assignment_distinct_teacher_rule(
    client: TestClient, session: Session
) -> None:
    process, _activity, slot0, slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    factories.make_assignment(session, process, slot0, teacher)
    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot1.id),
            "process_teacher_id": str(teacher.id),
        },
    )
    assert resp.status_code == 400
    assert "distinct teachers" in resp.json()["detail"]


def test_create_assignment_distinct_teachers_both_positions(
    client: TestClient, session: Session
) -> None:
    """Two distinct teachers may occupy both co-teaching positions (plan §3.7)."""
    process, activity, slot0, slot1 = _plan_setup(session)
    t0 = _make_teacher(session, process)
    t1 = _make_teacher(session, process)
    r0 = client.post(
        f"{_assignments_path(process.id)}/",
        json={"hour_requirement_id": str(slot0.id), "process_teacher_id": str(t0.id)},
    )
    r1 = client.post(
        f"{_assignments_path(process.id)}/",
        json={"hour_requirement_id": str(slot1.id), "process_teacher_id": str(t1.id)},
    )
    assert r0.status_code == 201
    assert r1.status_code == 201
    rows = session.exec(
        select(Assignment).where(Assignment.teaching_activity_id == activity.id)
    ).all()
    assert {row.process_teacher_id for row in rows} == {t0.id, t1.id}


# ── Read ──────────────────────────────────────────────────────────────────────


def test_list_assignments(client: TestClient, session: Session) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    factories.make_assignment(session, process, slot0, teacher)
    resp = client.get(f"{_assignments_path(process.id)}/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["data"][0]["hour_requirement_id"] == str(slot0.id)


def test_list_assignments_unknown_process(client: TestClient) -> None:
    resp = client.get(f"{_assignments_path(uuid.uuid4())}/")
    assert resp.status_code == 404


def test_get_assignment(client: TestClient, session: Session) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    assignment = factories.make_assignment(session, process, slot0, teacher)
    resp = client.get(f"{_assignments_path(process.id)}/{assignment.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(assignment.id)


def test_get_assignment_not_found(client: TestClient, session: Session) -> None:
    process, _activity, _slot0, _slot1 = _plan_setup(session)
    resp = client.get(f"{_assignments_path(process.id)}/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_get_assignment_wrong_process(client: TestClient, session: Session) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    assignment = factories.make_assignment(session, process, slot0, teacher)
    other = factories.make_assignment_process(session)
    resp = client.get(f"{_assignments_path(other.id)}/{assignment.id}")
    assert resp.status_code == 404


# ── Update (notes only) ───────────────────────────────────────────────────────


def test_update_assignment_notes(client: TestClient, session: Session) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    assignment = factories.make_assignment(session, process, slot0, teacher)
    resp = client.patch(
        f"{_assignments_path(process.id)}/{assignment.id}",
        json={"notes": "updated"},
    )
    assert resp.status_code == 200
    assert resp.json()["notes"] == "updated"
    session.refresh(assignment)
    assert assignment.notes == "updated"


# ── Cancel (soft delete) ──────────────────────────────────────────────────────


def test_undo_assignment_cancels_and_frees_slot(
    admin_client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    assignment = factories.make_assignment(session, process, slot0, teacher)
    slot0.status = HourRequirementStatus.ASSIGNED
    session.add(slot0)
    session.commit()
    resp = admin_client.post(
        _undo_path(process.id, assignment.id), json={"reason": "Wrong selection"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == AssignmentStatus.CANCELLED.value
    session.refresh(assignment)
    session.refresh(slot0)
    assert assignment.status == AssignmentStatus.CANCELLED
    # The freed slot is available for re-assignment.
    assert slot0.status == HourRequirementStatus.AVAILABLE


def test_undo_assignment_reassignable_after_cancel(
    admin_client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    first = _make_teacher(session, process)
    assignment = factories.make_assignment(session, process, slot0, first)
    admin_client.post(
        _undo_path(process.id, assignment.id), json={"reason": "Correction"}
    )
    second = _make_teacher(session, process)
    resp = admin_client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(second.id),
        },
    )
    assert resp.status_code == 201


def test_undo_assignment_already_cancelled_is_rejected(
    admin_client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    assignment = factories.make_assignment(
        session, process, slot0, teacher, status=AssignmentStatus.CANCELLED
    )
    resp = admin_client.post(
        _undo_path(process.id, assignment.id), json={"reason": "Duplicate undo"}
    )
    assert resp.status_code == 409
    assert "active assignment" in resp.json()["detail"]


def test_undo_assignment_missing_requirement(
    admin_client: TestClient, session: Session
) -> None:
    """Cancel still succeeds if the requirement row no longer exists."""
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    assignment = factories.make_assignment(session, process, slot0, teacher)
    requirement = session.get(HourRequirement, slot0.id)
    assert requirement is not None
    session.delete(requirement)
    session.commit()
    resp = admin_client.post(
        _undo_path(process.id, assignment.id), json={"reason": "Retire old row"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == AssignmentStatus.CANCELLED.value


def test_legacy_delete_requires_reason_and_delegates_to_undo(
    admin_client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    assignment = factories.make_assignment(session, process, slot0, teacher)
    path = f"{_assignments_path(process.id)}/{assignment.id}"

    assert admin_client.delete(path).status_code == 422
    resp = admin_client.request("DELETE", path, json={"reason": "Legacy correction"})

    assert resp.status_code == 200
    assert resp.json()["status"] == AssignmentStatus.CANCELLED.value


def test_undo_requires_reason_and_rejects_reader(
    admin_client: TestClient,
    reader_client: TestClient,
    session: Session,
    reader: UserModel,
) -> None:
    """A reader is refused before the body is even looked at.

    The role gate is a route dependency now (§21.6), so it runs ahead of body
    validation: an unauthorized caller gets 403 whatever they send, and never
    learns which fields the payload wants.
    """
    process, _activity, slot0, _slot1 = _plan_setup(session)
    factories.enrol(session, process, reader)
    teacher = _make_teacher(session, process)
    assignment = factories.make_assignment(session, process, slot0, teacher)
    path = _undo_path(process.id, assignment.id)

    assert admin_client.post(path, json={}).status_code == 422
    assert admin_client.post(path, json={"reason": ""}).status_code == 422
    assert reader_client.post(path, json={}).status_code == 403
    forbidden = reader_client.post(path, json={"reason": "Not authorized"})

    assert forbidden.status_code == 403


def test_undo_and_reassign_reject_writer(
    writer_client: TestClient, session: Session, writer_user: UserModel
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    factories.enrol(session, process, writer_user)
    first = _make_teacher(session, process)
    replacement = _make_teacher(session, process)
    assignment = factories.make_assignment(session, process, slot0, first)

    undo = writer_client.post(
        _undo_path(process.id, assignment.id), json={"reason": "Writer attempt"}
    )
    reassign = writer_client.post(
        _reassign_path(process.id, assignment.id),
        json={
            "process_teacher_id": str(replacement.id),
            "reason": "Writer attempt",
        },
    )

    assert undo.status_code == 403
    assert reassign.status_code == 403


def test_undo_records_reason_and_invalidates_feasibility(
    admin_client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    assignment = factories.make_assignment(session, process, slot0, teacher)
    plan = session.exec(
        select(TeachingPlan).where(TeachingPlan.assignment_process_id == process.id)
    ).one()
    plan.feasibility_status = FeasibilityStatus.UNKNOWN
    session.add(plan)
    session.commit()

    resp = admin_client.post(
        _undo_path(process.id, assignment.id),
        json={"reason": "Teacher reported an error"},
    )

    assert resp.status_code == 200
    event = session.exec(
        select(AuditEvent).where(AuditEvent.event_type == "assignment.undone")
    ).one()
    assert event.reason == "Teacher reported an error"
    assert event.before_json is not None
    assert event.after_json is not None
    assert event.before_json["status"] == AssignmentStatus.ACTIVE.value
    assert event.after_json["status"] == AssignmentStatus.CANCELLED.value
    session.refresh(plan)
    assert plan.feasibility_status == FeasibilityStatus.NOT_EVALUATED


def test_undo_requeues_turn_and_recomputes_earliest_current(
    admin_client: TestClient, session: Session
) -> None:
    process, _activity, slot0, slot1 = _plan_setup(session)
    first = _make_teacher(session, process, selection_position=0)
    second = _make_teacher(session, process, selection_position=1)
    third = _make_teacher(session, process, selection_position=2)
    assignment = factories.make_assignment(session, process, slot0, first)
    later_assignment = factories.make_assignment(session, process, slot1, third)
    meeting = factories.make_meeting_session(
        session, process, status=MeetingSessionStatus.SELECTING
    )
    first_turn = factories.make_selection_turn(
        session, meeting, first, position=0, status=SelectionTurnStatus.COMPLETED
    )
    first_turn.completed_at = first_turn.updated_at
    first_turn.skipped_at = first_turn.updated_at
    first_turn.skip_reason = "old"
    first_turn.forced_by_user_id = uuid.uuid4()
    session.add(first_turn)
    second_turn = factories.make_selection_turn(
        session, meeting, second, position=1, status=SelectionTurnStatus.ACTIVE
    )
    third_turn = factories.make_selection_turn(
        session, meeting, third, position=2, status=SelectionTurnStatus.COMPLETED
    )
    session.commit()

    resp = admin_client.post(
        _undo_path(process.id, assignment.id), json={"reason": "Re-open first turn"}
    )

    assert resp.status_code == 200
    session.refresh(first_turn)
    session.refresh(second_turn)
    session.refresh(third_turn)
    session.refresh(later_assignment)
    assert first_turn.status == SelectionTurnStatus.ACTIVE
    assert first_turn.started_at is not None
    assert first_turn.completed_at is None
    assert first_turn.skipped_at is None
    assert first_turn.skip_reason is None
    assert first_turn.forced_by_user_id is None
    assert second_turn.status == SelectionTurnStatus.PENDING
    assert second_turn.started_at is None
    assert third_turn.status == SelectionTurnStatus.COMPLETED
    assert later_assignment.status == AssignmentStatus.ACTIVE
    event_types = session.exec(select(AuditEvent.event_type)).all()
    assert "selection_turn.reentered" in event_types
    assert event_types.count("selection_turn.recomputed") == 2


def test_undo_requeues_later_turn_without_displacing_current(
    admin_client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    first = _make_teacher(session, process, selection_position=0)
    second = _make_teacher(session, process, selection_position=1)
    assignment = factories.make_assignment(session, process, slot0, second)
    meeting = factories.make_meeting_session(
        session, process, status=MeetingSessionStatus.SELECTING
    )
    current = factories.make_selection_turn(
        session, meeting, first, position=0, status=SelectionTurnStatus.ACTIVE
    )
    reentered = factories.make_selection_turn(
        session, meeting, second, position=1, status=SelectionTurnStatus.COMPLETED
    )

    resp = admin_client.post(
        _undo_path(process.id, assignment.id), json={"reason": "Return later turn"}
    )

    assert resp.status_code == 200
    session.refresh(current)
    session.refresh(reentered)
    assert current.status == SelectionTurnStatus.ACTIVE
    assert reentered.status == SelectionTurnStatus.PENDING


def test_undo_ignores_nonmatching_live_meeting(
    admin_client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    other = _make_teacher(session, process, selection_position=1)
    assignment = factories.make_assignment(session, process, slot0, teacher)
    meeting = factories.make_meeting_session(
        session, process, status=MeetingSessionStatus.OPEN
    )
    factories.make_selection_turn(session, meeting, other)

    resp = admin_client.post(
        _undo_path(process.id, assignment.id), json={"reason": "No matching turn"}
    )

    assert resp.status_code == 200
    assert (
        session.exec(
            select(AuditEvent).where(
                AuditEvent.event_type == "selection_turn.reentered"
            )
        ).first()
        is None
    )


def test_reassign_requires_reason_and_rejects_reader(
    admin_client: TestClient,
    reader_client: TestClient,
    session: Session,
    reader: UserModel,
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    factories.enrol(session, process, reader)
    first = _make_teacher(session, process)
    replacement = _make_teacher(session, process)
    assignment = factories.make_assignment(session, process, slot0, first)
    path = _reassign_path(process.id, assignment.id)

    assert (
        admin_client.post(
            path, json={"process_teacher_id": str(replacement.id)}
        ).status_code
        == 422
    )
    forbidden = reader_client.post(
        path,
        json={
            "process_teacher_id": str(replacement.id),
            "reason": "Not authorized",
        },
    )

    assert forbidden.status_code == 403


def test_reassign_atomically_replaces_assignment_and_audits_reason(
    admin_client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    first = _make_teacher(session, process)
    replacement = _make_teacher(session, process)
    assignment = factories.make_assignment(session, process, slot0, first)
    slot0.status = HourRequirementStatus.ASSIGNED
    session.add(slot0)
    session.commit()

    resp = admin_client.post(
        _reassign_path(process.id, assignment.id),
        json={
            "process_teacher_id": str(replacement.id),
            "reason": "Correct participant",
            "notes": "Moved by department head",
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] != str(assignment.id)
    assert body["hour_requirement_id"] == str(slot0.id)
    assert body["process_teacher_id"] == str(replacement.id)
    assert body["notes"] == "Moved by department head"
    session.refresh(assignment)
    session.refresh(slot0)
    assert assignment.status == AssignmentStatus.CANCELLED
    assert slot0.status == HourRequirementStatus.ASSIGNED
    event = session.exec(
        select(AuditEvent).where(AuditEvent.event_type == "assignment.reassigned")
    ).one()
    assert event.entity_id == uuid.UUID(body["id"])
    assert event.reason == "Correct participant"
    assert event.before_json is not None
    assert event.after_json is not None
    assert event.before_json["id"] == str(assignment.id)
    assert event.after_json["process_teacher_id"] == str(replacement.id)


def test_reassign_rejects_same_or_inactive_teacher(
    admin_client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    first = _make_teacher(session, process)
    inactive = _make_teacher(session, process)
    inactive.status = ProcessTeacherStatus.INACTIVE
    session.add(inactive)
    assignment = factories.make_assignment(session, process, slot0, first)

    same = admin_client.post(
        _reassign_path(process.id, assignment.id),
        json={"process_teacher_id": str(first.id), "reason": "Same"},
    )
    inactive_resp = admin_client.post(
        _reassign_path(process.id, assignment.id),
        json={"process_teacher_id": str(inactive.id), "reason": "Inactive"},
    )

    assert same.status_code == 400
    assert "different" in same.json()["detail"]
    assert inactive_resp.status_code == 400
    assert "active process teacher" in inactive_resp.json()["detail"]
    session.refresh(assignment)
    assert assignment.status == AssignmentStatus.ACTIVE


def test_reassign_rechecks_capacity_and_distinct_teacher_before_release(
    admin_client: TestClient, session: Session
) -> None:
    process, _activity, slot0, slot1 = _plan_setup(session)
    first = _make_teacher(session, process)
    over_capacity = _teacher_with_hours(session, process, base=2.0)
    sibling_owner = _make_teacher(session, process)
    assignment = factories.make_assignment(session, process, slot0, first)
    factories.make_assignment(session, process, slot1, sibling_owner)

    capacity = admin_client.post(
        _reassign_path(process.id, assignment.id),
        json={
            "process_teacher_id": str(over_capacity.id),
            "reason": "Capacity check",
        },
    )
    distinct = admin_client.post(
        _reassign_path(process.id, assignment.id),
        json={
            "process_teacher_id": str(sibling_owner.id),
            "reason": "Distinct check",
        },
    )

    assert capacity.status_code == 400
    assert "authorize extra hours" in capacity.json()["detail"]
    assert distinct.status_code == 400
    assert "distinct teachers" in distinct.json()["detail"]
    session.refresh(assignment)
    assert assignment.status == AssignmentStatus.ACTIVE


def test_reassign_rejects_non_active_assignment_and_stale_plan(
    admin_client: TestClient, session: Session
) -> None:
    process, _activity, slot0, slot1 = _plan_setup(session)
    first = _make_teacher(session, process)
    replacement = _make_teacher(session, process)
    cancelled = factories.make_assignment(
        session, process, slot0, first, status=AssignmentStatus.CANCELLED
    )
    active = factories.make_assignment(session, process, slot1, first)
    payload = {
        "process_teacher_id": str(replacement.id),
        "reason": "Lifecycle check",
    }

    cancelled_resp = admin_client.post(
        _reassign_path(process.id, cancelled.id), json=payload
    )
    _set_plan_status(session, process.id, TeachingPlanStatus.STALE)
    stale_resp = admin_client.post(_reassign_path(process.id, active.id), json=payload)

    assert cancelled_resp.status_code == 409
    assert stale_resp.status_code == 409
    assert "stale" in stale_resp.json()["detail"]


def test_reassign_repairs_and_persists_current_feasible_witness(
    admin_client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    first = _teacher_with_hours(session, process, base=4.0)
    replacement = _teacher_with_hours(session, process, base=4.0)
    evaluation = admin_client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-plan/feasibility/evaluate"
    )
    assert evaluation.status_code == 200
    created = admin_client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(first.id),
        },
    )
    assert created.status_code == 201

    resp = admin_client.post(
        _reassign_path(process.id, uuid.UUID(created.json()["id"])),
        json={
            "process_teacher_id": str(replacement.id),
            "reason": "Safe witness swap",
        },
    )

    assert resp.status_code == 201
    plan = session.exec(
        select(TeachingPlan).where(TeachingPlan.assignment_process_id == process.id)
    ).one()
    assert plan.feasibility_status == FeasibilityStatus.FEASIBLE
    witness = admin_client.get(
        f"/reparto/assignment-processes/{process.id}/teaching-plan/feasibility/witness"
    )
    assert witness.status_code == 200


def test_reassign_fails_closed_on_missing_current_witness(
    admin_client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    first = _teacher_with_hours(session, process, base=4.0)
    replacement = _teacher_with_hours(session, process, base=4.0)
    evaluation = admin_client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-plan/feasibility/evaluate"
    )
    assert evaluation.status_code == 200
    created = admin_client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(first.id),
        },
    )
    witness_row = session.exec(select(FeasibilityWitness)).one()
    session.delete(witness_row)
    session.commit()

    resp = admin_client.post(
        _reassign_path(process.id, uuid.UUID(created.json()["id"])),
        json={
            "process_teacher_id": str(replacement.id),
            "reason": "Missing witness",
        },
    )

    assert resp.status_code == 409
    assert "missing or stale" in resp.json()["detail"]
    original = session.get(Assignment, uuid.UUID(created.json()["id"]))
    assert original is not None
    assert original.status == AssignmentStatus.ACTIVE


def test_reassign_rejects_witness_unsafe_insert_without_mutating_old_row(
    admin_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    slots = []
    for hours in (4.0, 3.0, 3.0):
        activity = factories.make_teaching_activity(session, plan, subject)
        slots.append(
            factories.make_hour_requirement(
                session, process, activity, required_teacher_hours=hours
            )
        )
    first = _teacher_with_hours(session, process, base=4.0)
    replacement = _teacher_with_hours(session, process, base=6.0)
    evaluation = admin_client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-plan/feasibility/evaluate"
    )
    assert evaluation.status_code == 200
    created = admin_client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slots[0].id),
            "process_teacher_id": str(first.id),
        },
    )

    resp = admin_client.post(
        _reassign_path(process.id, uuid.UUID(created.json()["id"])),
        json={
            "process_teacher_id": str(replacement.id),
            "reason": "Unsafe repartition",
        },
    )

    assert resp.status_code == 409
    assert "strand" in resp.json()["detail"]
    original = session.get(Assignment, uuid.UUID(created.json()["id"]))
    assert original is not None
    assert original.status == AssignmentStatus.ACTIVE


# ── Direct teacher choice (LAN) ───────────────────────────────────────────────


def _direct_setup(session: Session, user_id: uuid.UUID, *, strict: bool = False):
    process, _activity, slot0, _slot1 = _plan_setup(session)
    profile = factories.make_teacher_profile(session, user_id=user_id)
    teacher = factories.make_process_teacher(
        session, process, profile, selection_position=0
    )
    meeting = factories.make_meeting_session(
        session,
        process,
        status=MeetingSessionStatus.SELECTING,
        direct_teacher_selection_enabled=True,
        selection_mode=SelectionOrderMode.STRICT if strict else SelectionOrderMode.NONE,
    )
    path = f"{_assignments_path(process.id)}/direct-choice"
    payload = {
        "meeting_session_id": str(meeting.id),
        "hour_requirement_id": str(slot0.id),
    }
    return process, meeting, teacher, slot0, path, payload


def test_direct_choice_creates_active_assignment(
    client: TestClient, session: Session, current_user
) -> None:
    _p, _m, teacher, slot0, path, payload = _direct_setup(
        session, uuid.UUID(str(current_user.id))
    )
    resp = client.post(path, json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["process_teacher_id"] == str(teacher.id)
    assert body["source"] == AssignmentSource.TEACHER_DIRECT.value
    assert body["status"] == AssignmentStatus.ACTIVE.value
    assert body["confirmed_by_user_id"] == str(current_user.id)
    session.refresh(slot0)
    assert slot0.status == HourRequirementStatus.ASSIGNED


def test_direct_choice_requires_enabled_session(
    client: TestClient, session: Session, current_user
) -> None:
    _p, meeting, _t, _s, path, payload = _direct_setup(
        session, uuid.UUID(str(current_user.id))
    )
    meeting.direct_teacher_selection_enabled = False
    session.add(meeting)
    session.commit()
    resp = client.post(path, json=payload)
    assert resp.status_code == 400
    assert "disabled" in resp.json()["detail"]


def test_direct_choice_requires_open_session(
    client: TestClient, session: Session, current_user
) -> None:
    _p, meeting, _t, _s, path, payload = _direct_setup(
        session, uuid.UUID(str(current_user.id))
    )
    meeting.status = MeetingSessionStatus.PAUSED
    session.add(meeting)
    session.commit()
    resp = client.post(path, json=payload)
    assert resp.status_code == 400
    assert "must be open" in resp.json()["detail"]


def test_direct_choice_missing_session(
    client: TestClient, session: Session, current_user
) -> None:
    _p, _m, _t, _s, path, payload = _direct_setup(
        session, uuid.UUID(str(current_user.id))
    )
    payload["meeting_session_id"] = str(uuid.uuid4())
    resp = client.post(path, json=payload)
    assert resp.status_code == 404
    assert "MeetingSession" in resp.json()["detail"]


def test_direct_choice_requires_linked_teacher(
    client: TestClient, session: Session
) -> None:
    _p, _m, _t, _s, path, payload = _direct_setup(session, uuid.uuid4())
    resp = client.post(path, json=payload)
    assert resp.status_code == 404
    assert "linked" in resp.json()["detail"]


def test_direct_choice_strict_rejects_out_of_turn(
    client: TestClient, session: Session, current_user
) -> None:
    process, meeting, _t, _s, path, payload = _direct_setup(
        session, uuid.UUID(str(current_user.id)), strict=True
    )
    other = _make_teacher(session, process, selection_position=1)
    factories.make_selection_turn(
        session, meeting, other, status=SelectionTurnStatus.ACTIVE
    )
    resp = client.post(path, json=payload)
    assert resp.status_code == 400
    assert "outside the active strict turn" in resp.json()["detail"]


def test_direct_choice_strict_completes_active_turn(
    client: TestClient, session: Session, current_user
) -> None:
    _p, meeting, teacher, _s, path, payload = _direct_setup(
        session, uuid.UUID(str(current_user.id)), strict=True
    )
    turn = factories.make_selection_turn(
        session, meeting, teacher, status=SelectionTurnStatus.ACTIVE
    )
    resp = client.post(path, json=payload)
    assert resp.status_code == 201
    session.refresh(turn)
    assert turn.status == SelectionTurnStatus.COMPLETED
    assert turn.completed_at is not None


def test_direct_choice_no_active_turn_leaves_turns_untouched(
    client: TestClient, session: Session, current_user
) -> None:
    """Non-strict choice with only a PENDING turn: nothing is completed."""
    _p, meeting, teacher, _s, path, payload = _direct_setup(
        session, uuid.UUID(str(current_user.id))
    )
    turn = factories.make_selection_turn(
        session, meeting, teacher, status=SelectionTurnStatus.PENDING
    )
    resp = client.post(path, json=payload)
    assert resp.status_code == 201
    session.refresh(turn)
    assert turn.status == SelectionTurnStatus.PENDING


# ── Database-level invariants (plan §20.9) ────────────────────────────────────


def test_db_blocks_second_active_assignment_per_requirement(
    session: Session,
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    t0 = _make_teacher(session, process)
    t1 = _make_teacher(session, process)
    factories.make_assignment(session, process, slot0, t0)
    with pytest.raises(IntegrityError):
        factories.make_assignment(session, process, slot0, t1)
    session.rollback()


def test_db_blocks_same_teacher_two_positions(session: Session) -> None:
    process, _activity, slot0, slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    factories.make_assignment(session, process, slot0, teacher)
    with pytest.raises(IntegrityError):
        factories.make_assignment(session, process, slot1, teacher)
    session.rollback()


# ── Exact-target / no-overload-bypass guard (plan §3.8) ───────────────────────


def _teacher_with_hours(session, process, *, base=2.0, extra=0.0):
    profile = factories.make_teacher_profile(session)
    return factories.make_process_teacher(
        session, process, profile, base_weekly_hours=base, extra_weekly_hours=extra
    )


def _extra_activity_slot(session, process, *, hours=4.0):
    """A second activity with one slot, on the process's single plan."""
    plan = session.exec(
        select(TeachingPlan).where(TeachingPlan.assignment_process_id == process.id)
    ).first()
    subject = factories.make_subject(session, process, name=f"Extra {uuid.uuid4()}")
    activity = factories.make_teaching_activity(
        session, plan, subject, required_teacher_count=1
    )
    return factories.make_hour_requirement(
        session, process, activity, required_teacher_hours=hours
    )


def test_create_assignment_over_target_rejected(
    client: TestClient, session: Session
) -> None:
    """An indivisible slot that exceeds the target is refused (no bypass)."""
    process, _activity, slot0, _slot1 = _plan_setup(session)  # 4h slots
    teacher = _teacher_with_hours(session, process, base=2.0)  # target 2 < 4
    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(teacher.id),
        },
    )
    assert resp.status_code == 400
    assert "authorize extra hours" in resp.json()["detail"]
    # The slot stays available — nothing was occupied.
    session.refresh(slot0)
    assert slot0.status == HourRequirementStatus.AVAILABLE


def test_create_assignment_fits_with_authorized_extra(
    client: TestClient, session: Session
) -> None:
    """Raising extra hours lifts the target so the same slot now fits (plan §3.8)."""
    process, _activity, slot0, _slot1 = _plan_setup(session)  # 4h slots
    teacher = _teacher_with_hours(session, process, base=2.0, extra=2.0)  # target 4
    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(teacher.id),
        },
    )
    assert resp.status_code == 201


def test_feasible_plan_accepts_selection_that_preserves_fast_guards(
    admin_client: TestClient, session: Session
) -> None:
    """The shared occupancy path repairs a persisted witness without a full solve."""
    process, _activity, slot0, _slot1 = _plan_setup(session)
    first = _teacher_with_hours(session, process, base=4.0)
    _teacher_with_hours(session, process, base=4.0)
    evaluation = admin_client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-plan/feasibility/evaluate"
    )
    assert evaluation.status_code == 200

    resp = admin_client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(first.id),
        },
    )
    assert resp.status_code == 201


def test_feasible_plan_rejects_residual_total_mismatch(
    client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _teacher_with_hours(session, process, base=10.0)
    plan = session.exec(
        select(TeachingPlan).where(TeachingPlan.assignment_process_id == process.id)
    ).one()
    plan.feasibility_status = FeasibilityStatus.FEASIBLE
    session.add(plan)
    session.commit()

    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(teacher.id),
        },
    )
    assert resp.status_code == 409
    assert "residual_totals_mismatch" in resp.json()["detail"]


def test_feasible_plan_rejects_selection_that_strands_oversized_slot(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(
        session, process, feasibility_status=FeasibilityStatus.FEASIBLE
    )
    subject = factories.make_subject(session, process)
    chosen_activity = factories.make_teaching_activity(session, plan, subject)
    large_activity = factories.make_teaching_activity(session, plan, subject)
    chosen = factories.make_hour_requirement(
        session, process, chosen_activity, required_teacher_hours=4
    )
    factories.make_hour_requirement(
        session, process, large_activity, required_teacher_hours=6
    )
    teacher = _teacher_with_hours(session, process, base=5.0)
    _teacher_with_hours(session, process, base=5.0)

    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(chosen.id),
            "process_teacher_id": str(teacher.id),
        },
    )
    assert resp.status_code == 409
    assert "slot_exceeds_every_target" in resp.json()["detail"]
    assert str(chosen.id) not in resp.json()["detail"]


def test_create_assignment_accumulates_toward_target(
    client: TestClient, session: Session
) -> None:
    """A second slot that would exceed the remaining target is refused."""
    process, _activity, slot0, _slot1 = _plan_setup(session)  # 4h slots
    teacher = _teacher_with_hours(session, process, base=6.0)  # target 6
    first = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(teacher.id),
        },
    )
    assert first.status_code == 201  # 4 <= 6
    extra_slot = _extra_activity_slot(session, process, hours=4.0)
    second = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(extra_slot.id),
            "process_teacher_id": str(teacher.id),
        },
    )
    assert second.status_code == 400  # 4 + 4 = 8 > 6


# ── Assignment-stage validations endpoint (plan §6.3, §6.4, §7.7) ─────────────


def test_get_assignment_validations_reports_findings(
    client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)  # two unassigned slots
    _teacher_with_hours(session, process, base=6.0)  # below target
    resp = client.get(f"{_assignments_path(process.id)}/validations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["assignment_process_id"] == str(process.id)
    assert body["is_final_ready"] is False
    assert body["blocking_count"] >= 1
    codes = {m["code"] for m in body["messages"]}
    assert "requirement.unassigned" in codes
    assert "participant.below_target" in codes


def test_get_assignment_validations_final_ready(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    resp = client.get(f"{_assignments_path(process.id)}/validations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_final_ready"] is True
    assert body["messages"] == []


def test_get_assignment_validations_process_not_found(client: TestClient) -> None:
    resp = client.get(f"{_assignments_path(uuid.uuid4())}/validations")
    assert resp.status_code == 404


def test_get_assignment_validations_refused_to_a_participant(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    """The report is the department-head tier, so a participant is refused (`W7.1`).

    This test used to assert ``200``. Since `W5.1` every finding names the
    participant it is about and quotes their hours, which is the tier
    :mod:`reparto_service.services.sse` withholds from a teacher even on an
    event about themselves — the reader floor here was the same disagreement
    between "which processes" and "which payload" that `W5.3` closed on the
    dashboard.
    """
    process = factories.make_assignment_process(session)
    factories.enrol(session, process, reader)
    factories.make_teaching_plan(session, process)
    resp = reader_client.get(f"{_assignments_path(process.id)}/validations")
    assert resp.status_code == 403


# ── Direct-selection concurrency: lock + in-transaction recheck (plan §20.5) ───


def _direct_concurrency_setup(
    session: Session,
    user_id: uuid.UUID,
    *,
    selection_position: int = 0,
    base_weekly_hours: float = 18.0,
):
    """Process/plan with two co-teaching slots and the current user linked.

    Unlike ``_direct_setup`` this exposes both slots and lets the caller tune
    the linked participant's target hours, so the distinct-teacher and
    remaining-target rechecks can be exercised on the direct path.
    """
    process, activity, slot0, slot1 = _plan_setup(session)
    profile = factories.make_teacher_profile(session, user_id=user_id)
    teacher = factories.make_process_teacher(
        session,
        process,
        profile,
        selection_position=selection_position,
        base_weekly_hours=base_weekly_hours,
    )
    meeting = factories.make_meeting_session(
        session,
        process,
        status=MeetingSessionStatus.SELECTING,
        direct_teacher_selection_enabled=True,
    )
    path = f"{_assignments_path(process.id)}/direct-choice"
    return process, activity, slot0, slot1, teacher, meeting, path


def test_direct_choice_rechecks_distinct_teacher_under_lock(
    client: TestClient, session: Session, current_user
) -> None:
    """A teacher already holding one position is refused a sibling position.

    The activity's live occupancy is locked, then the distinct-teacher rule is
    rechecked inside the transaction (plan §3.7, §20.5) — a clean 400 before the
    DB partial-unique barrier.
    """
    _p, _a, slot0, slot1, teacher, meeting, path = _direct_concurrency_setup(
        session, uuid.UUID(str(current_user.id))
    )
    factories.make_assignment(session, _p, slot0, teacher)
    resp = client.post(
        path,
        json={
            "meeting_session_id": str(meeting.id),
            "hour_requirement_id": str(slot1.id),
        },
    )
    assert resp.status_code == 400
    assert "distinct teachers" in resp.json()["detail"]
    session.refresh(slot1)
    assert slot1.status == HourRequirementStatus.AVAILABLE


def test_direct_choice_rechecks_remaining_target_under_lock(
    client: TestClient, session: Session, current_user
) -> None:
    """A slot exceeding the locked participant's remaining target is refused."""
    _p, _a, slot0, _slot1, _t, meeting, path = _direct_concurrency_setup(
        session, uuid.UUID(str(current_user.id)), base_weekly_hours=2.0
    )  # target 2 < 4h slot
    resp = client.post(
        path,
        json={
            "meeting_session_id": str(meeting.id),
            "hour_requirement_id": str(slot0.id),
        },
    )
    assert resp.status_code == 400
    assert "authorize extra hours" in resp.json()["detail"]
    session.refresh(slot0)
    assert slot0.status == HourRequirementStatus.AVAILABLE


def test_direct_choice_with_assigned_sibling_succeeds(
    client: TestClient, session: Session, current_user
) -> None:
    """Locking a non-empty sibling set still lets a distinct teacher take the
    free position (plan §3.7)."""
    process, activity, slot0, slot1, teacher, meeting, path = _direct_concurrency_setup(
        session, uuid.UUID(str(current_user.id))
    )
    other = _make_teacher(session, process)
    factories.make_assignment(session, process, slot0, other)
    resp = client.post(
        path,
        json={
            "meeting_session_id": str(meeting.id),
            "hour_requirement_id": str(slot1.id),
        },
    )
    assert resp.status_code == 201
    rows = session.exec(
        select(Assignment)
        .where(Assignment.teaching_activity_id == activity.id)
        .where(Assignment.status == AssignmentStatus.ACTIVE)
    ).all()
    assert {row.process_teacher_id for row in rows} == {other.id, teacher.id}


# ── Plan-readiness gate on assignment operations (plan §3.11.9, §9.7) ──────────


def test_manual_assignment_blocked_when_plan_stale(
    client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    _set_plan_status(session, process.id, TeachingPlanStatus.STALE)
    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(teacher.id),
        },
    )
    assert resp.status_code == 409
    assert "stale" in resp.json()["detail"]
    session.refresh(slot0)
    assert slot0.status == HourRequirementStatus.AVAILABLE


def test_manual_assignment_blocked_when_reconciliation_required(
    client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    _set_plan_status(session, process.id, TeachingPlanStatus.RECONCILIATION_REQUIRED)
    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(teacher.id),
        },
    )
    assert resp.status_code == 409
    assert "reconciliation" in resp.json()["detail"]


def test_manual_assignment_allowed_when_requirements_generated(
    client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    _set_plan_status(session, process.id, TeachingPlanStatus.REQUIREMENTS_GENERATED)
    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(teacher.id),
        },
    )
    assert resp.status_code == 201


def test_direct_choice_blocked_when_plan_stale(
    client: TestClient, session: Session, current_user
) -> None:
    _p, _m, _teacher, slot0, path, payload = _direct_setup(
        session, uuid.UUID(str(current_user.id))
    )
    _set_plan_status(session, _p.id, TeachingPlanStatus.STALE)
    resp = client.post(path, json=payload)
    assert resp.status_code == 409
    assert "stale" in resp.json()["detail"]
    session.refresh(slot0)
    assert slot0.status == HourRequirementStatus.AVAILABLE
