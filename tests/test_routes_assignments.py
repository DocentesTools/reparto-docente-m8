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
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.enums import (
    AssignmentProcessStatus,
    AssignmentSource,
    AssignmentStatus,
    HourRequirementStatus,
    MeetingSessionStatus,
    SelectionOrderMode,
    SelectionTurnStatus,
)
from tests import factories


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


def test_delete_assignment_cancels_and_frees_slot(
    client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    assignment = factories.make_assignment(session, process, slot0, teacher)
    slot0.status = HourRequirementStatus.ASSIGNED
    session.add(slot0)
    session.commit()
    resp = client.delete(f"{_assignments_path(process.id)}/{assignment.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == AssignmentStatus.CANCELLED.value
    session.refresh(assignment)
    session.refresh(slot0)
    assert assignment.status == AssignmentStatus.CANCELLED
    # The freed slot is available for re-assignment.
    assert slot0.status == HourRequirementStatus.AVAILABLE


def test_delete_assignment_reassignable_after_cancel(
    client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    first = _make_teacher(session, process)
    assignment = factories.make_assignment(session, process, slot0, first)
    client.delete(f"{_assignments_path(process.id)}/{assignment.id}")
    second = _make_teacher(session, process)
    resp = client.post(
        f"{_assignments_path(process.id)}/",
        json={
            "hour_requirement_id": str(slot0.id),
            "process_teacher_id": str(second.id),
        },
    )
    assert resp.status_code == 201


def test_delete_assignment_already_cancelled_is_noop(
    client: TestClient, session: Session
) -> None:
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    assignment = factories.make_assignment(
        session, process, slot0, teacher, status=AssignmentStatus.CANCELLED
    )
    resp = client.delete(f"{_assignments_path(process.id)}/{assignment.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == AssignmentStatus.CANCELLED.value


def test_delete_assignment_missing_requirement(
    client: TestClient, session: Session
) -> None:
    """Cancel still succeeds if the requirement row no longer exists."""
    process, _activity, slot0, _slot1 = _plan_setup(session)
    teacher = _make_teacher(session, process)
    assignment = factories.make_assignment(session, process, slot0, teacher)
    requirement = session.get(HourRequirement, slot0.id)
    assert requirement is not None
    session.delete(requirement)
    session.commit()
    resp = client.delete(f"{_assignments_path(process.id)}/{assignment.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == AssignmentStatus.CANCELLED.value


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
