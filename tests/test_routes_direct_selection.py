"""API tests for Phase 4 direct teacher selection."""

from __future__ import annotations

import uuid
from typing import TypedDict

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.meeting_sessions import MeetingSession
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.enums import (
    AssignmentSource,
    AssignmentStatus,
    HourRequirementStatus,
    MeetingSessionStatus,
    SelectionOrderMode,
    SelectionTurnStatus,
    TeachingPlanStatus,
)
from tests import factories


class DirectChoicePayload(TypedDict):
    """Direct-choice request payload used by tests.

    A slot is indivisible, so the teacher only names the slot — there is no
    ``assigned_hours`` to send (plan §5.10).
    """

    meeting_session_id: str
    hour_requirement_id: str


def _direct_selection_data(
    session: Session, user_id: uuid.UUID, *, strict: bool = False
) -> tuple[AssignmentProcess, MeetingSession, ProcessTeacher, str, DirectChoicePayload]:
    process = factories.make_assignment_process(session)
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
    plan = factories.make_teaching_plan(
        session, process, status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    subject = factories.make_subject(session, process)
    activity = factories.make_teaching_activity(session, plan, subject)
    requirement = factories.make_hour_requirement(session, process, activity)
    path = f"/reparto/assignment-processes/{process.id}/assignments/direct-choice"
    payload: DirectChoicePayload = {
        "meeting_session_id": str(meeting.id),
        "hour_requirement_id": str(requirement.id),
    }
    return process, meeting, teacher, path, payload


def test_direct_teacher_choice_occupies_slot_in_full(
    client: TestClient, session: Session, current_user
) -> None:
    """A teacher's own choice lands ACTIVE and self-confirmed (plan §7.7)."""
    _, _, teacher, path, payload = _direct_selection_data(
        session, uuid.UUID(str(current_user.id))
    )

    resp = client.post(path, json=payload)

    assert resp.status_code == 201
    body = resp.json()
    assert body["process_teacher_id"] == str(teacher.id)
    assert body["source"] == "teacher_direct"
    assert body["status"] == AssignmentStatus.ACTIVE.value
    # The teacher chose for themselves, so they are both chooser and confirmer.
    assert body["chosen_by_user_id"] == str(current_user.id)
    assert body["confirmed_by_user_id"] == str(current_user.id)
    assignment = session.exec(select(Assignment)).one()
    assert assignment.source == AssignmentSource.TEACHER_DIRECT
    # The slot is occupied in full — no hours are carried on the assignment.
    slot = session.get(HourRequirement, uuid.UUID(payload["hour_requirement_id"]))
    assert slot is not None
    assert slot.status == HourRequirementStatus.ASSIGNED


def test_direct_teacher_choice_requires_enabled_session(
    client: TestClient, session: Session, current_user
) -> None:
    _, meeting, _, path, payload = _direct_selection_data(
        session, uuid.UUID(str(current_user.id))
    )
    meeting.direct_teacher_selection_enabled = False
    session.add(meeting)
    session.commit()

    resp = client.post(path, json=payload)

    assert resp.status_code == 400
    assert "disabled" in resp.json()["detail"]


def test_direct_teacher_choice_requires_open_session(
    client: TestClient, session: Session, current_user
) -> None:
    _, meeting, _, path, payload = _direct_selection_data(
        session, uuid.UUID(str(current_user.id))
    )
    meeting.status = MeetingSessionStatus.PAUSED
    session.add(meeting)
    session.commit()

    resp = client.post(path, json=payload)

    assert resp.status_code == 400
    assert "must be open" in resp.json()["detail"]


def test_direct_teacher_choice_requires_linked_teacher(
    client: TestClient, session: Session
) -> None:
    _, _, _, path, payload = _direct_selection_data(session, uuid.uuid4())

    resp = client.post(path, json=payload)

    assert resp.status_code == 404
    assert "linked" in resp.json()["detail"]


def test_direct_teacher_choice_returns_404_for_missing_session(
    client: TestClient, session: Session, current_user
) -> None:
    _, _, _, path, payload = _direct_selection_data(
        session, uuid.UUID(str(current_user.id))
    )
    payload["meeting_session_id"] = str(uuid.uuid4())

    resp = client.post(path, json=payload)

    assert resp.status_code == 404
    assert "MeetingSession" in resp.json()["detail"]


def test_strict_direct_choice_rejects_out_of_turn(
    client: TestClient, session: Session, current_user
) -> None:
    process, meeting, _, path, payload = _direct_selection_data(
        session, uuid.UUID(str(current_user.id)), strict=True
    )
    other_profile = factories.make_teacher_profile(session)
    other = factories.make_process_teacher(
        session, process, other_profile, selection_position=1
    )
    factories.make_selection_turn(
        session, meeting, other, status=SelectionTurnStatus.ACTIVE
    )

    resp = client.post(path, json=payload)

    assert resp.status_code == 400
    assert "outside the active strict turn" in resp.json()["detail"]


def test_strict_direct_choice_completes_active_turn(
    client: TestClient, session: Session, current_user
) -> None:
    _, meeting, teacher, path, payload = _direct_selection_data(
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


def test_direct_choice_rejects_already_assigned_slot(
    client: TestClient, session: Session, current_user
) -> None:
    """A taken slot cannot be shared or split, so the choice is refused (§5.10)."""
    process, _, _, path, payload = _direct_selection_data(
        session, uuid.UUID(str(current_user.id))
    )
    requirement_id = uuid.UUID(payload["hour_requirement_id"])
    taken = session.get(HourRequirement, requirement_id)
    assert taken is not None
    # Another teacher already occupies the slot in full.
    other_profile = factories.make_teacher_profile(session, display_name="Other")
    other = factories.make_process_teacher(session, process, other_profile)
    factories.make_assignment(session, process, taken, other)

    resp = client.post(path, json=payload)

    assert resp.status_code == 400
    assert "already assigned" in resp.json()["detail"]
