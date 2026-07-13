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
    MeetingSessionStatus,
    SelectionOrderMode,
    SelectionTurnStatus,
)
from tests import factories


class DirectChoicePayload(TypedDict):
    """Direct-choice request payload used by tests."""

    meeting_session_id: str
    hour_requirement_id: str
    assigned_hours: int


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
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(session, process, group, subject)
    path = f"/reparto/assignment-processes/{process.id}/assignments/direct-choice"
    payload: DirectChoicePayload = {
        "meeting_session_id": str(meeting.id),
        "hour_requirement_id": str(requirement.id),
        "assigned_hours": 4,
    }
    return process, meeting, teacher, path, payload


def test_direct_teacher_choice_creates_confirmed_assignment(
    client: TestClient, session: Session, current_user
) -> None:
    _, _, teacher, path, payload = _direct_selection_data(
        session, uuid.UUID(str(current_user.id))
    )

    resp = client.post(path, json=payload)

    assert resp.status_code == 201
    body = resp.json()
    assert body["process_teacher_id"] == str(teacher.id)
    assert body["source"] == "teacher_direct"
    assert body["status"] == "confirmed"
    assignment = session.exec(select(Assignment)).one()
    assert assignment.source == AssignmentSource.TEACHER_DIRECT


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


def test_direct_choice_rejects_covered_requirement(
    client: TestClient, session: Session, current_user
) -> None:
    process, _, teacher, path, payload = _direct_selection_data(
        session, uuid.UUID(str(current_user.id))
    )
    requirement_id = uuid.UUID(payload["hour_requirement_id"])
    covered = session.get(HourRequirement, requirement_id)
    assert covered is not None
    factories.make_assignment(session, process, covered, teacher, assigned_hours=4)

    resp = client.post(path, json=payload)

    assert resp.status_code == 400
    assert "above its required hours" in resp.json()["detail"]
