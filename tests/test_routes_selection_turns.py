"""API tests for Phase 3 selection-turn meeting control."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.meeting_sessions import MeetingSession
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.enums import (
    AssignmentSource,
    AssignmentStatus,
    MeetingSessionStatus,
    SelectionTurnStatus,
)
from tests import factories


def _open_meeting(session: Session) -> tuple[AssignmentProcess, MeetingSession]:
    process = factories.make_assignment_process(session)
    meeting = factories.make_meeting_session(
        session, process, status=MeetingSessionStatus.OPEN
    )
    return process, meeting


def _teacher(
    session: Session, process: AssignmentProcess, name: str, position: int | None
) -> ProcessTeacher:
    profile = factories.make_teacher_profile(session, display_name=name)
    return factories.make_process_teacher(
        session, process, profile, selection_position=position
    )


def _turns_path(process: AssignmentProcess, meeting: MeetingSession) -> str:
    return (
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/"
        f"{meeting.id}/turns"
    )


def test_initialize_turns_from_selection_order(
    client: TestClient, session: Session
) -> None:
    process, meeting = _open_meeting(session)
    second = _teacher(session, process, "B", 1)
    first = _teacher(session, process, "A", 0)

    resp = client.post(f"{_turns_path(process, meeting)}/initialize")

    assert resp.status_code == 201
    body = resp.json()
    assert body["count"] == 2
    assert body["data"][0]["process_teacher_id"] == str(first.id)
    assert body["data"][1]["process_teacher_id"] == str(second.id)


def test_list_turns_endpoint(client: TestClient, session: Session) -> None:
    process, meeting = _open_meeting(session)
    teacher = _teacher(session, process, "A", 0)
    turn = factories.make_selection_turn(session, meeting, teacher)

    resp = client.get(f"{_turns_path(process, meeting)}/")

    assert resp.status_code == 200
    assert resp.json()["data"][0]["id"] == str(turn.id)


def test_initialize_rejects_existing_turns(
    client: TestClient, session: Session
) -> None:
    process, meeting = _open_meeting(session)
    teacher = _teacher(session, process, "A", 0)
    factories.make_selection_turn(session, meeting, teacher)

    resp = client.post(f"{_turns_path(process, meeting)}/initialize")

    assert resp.status_code == 400
    assert "already exist" in resp.json()["detail"]


def test_initialize_rejects_duplicate_selection_positions(
    client: TestClient, session: Session
) -> None:
    process, meeting = _open_meeting(session)
    _teacher(session, process, "A", 0)
    _teacher(session, process, "B", 0)

    resp = client.post(f"{_turns_path(process, meeting)}/initialize")

    assert resp.status_code == 400
    assert "Duplicate selection positions" in resp.json()["detail"]


def test_initialize_rejects_missing_selection_position(
    client: TestClient, session: Session
) -> None:
    process, meeting = _open_meeting(session)
    _teacher(session, process, "A", None)

    resp = client.post(f"{_turns_path(process, meeting)}/initialize")

    assert resp.status_code == 400
    assert "selection_position" in resp.json()["detail"]


def test_start_turn_enforces_one_active_turn(
    client: TestClient, session: Session
) -> None:
    process, meeting = _open_meeting(session)
    first = _teacher(session, process, "A", 0)
    second = _teacher(session, process, "B", 1)
    active = factories.make_selection_turn(
        session, meeting, first, status=SelectionTurnStatus.ACTIVE
    )
    pending = factories.make_selection_turn(session, meeting, second, position=1)

    resp = client.post(f"{_turns_path(process, meeting)}/{pending.id}/start")

    assert resp.status_code == 400
    assert "already active" in resp.json()["detail"]
    session.refresh(active)
    assert active.status == SelectionTurnStatus.ACTIVE


def test_start_turn_writes_audit_event(
    client: TestClient, session: Session, current_user
) -> None:
    process, meeting = _open_meeting(session)
    teacher = _teacher(session, process, "A", 0)
    turn = factories.make_selection_turn(session, meeting, teacher)

    resp = client.post(f"{_turns_path(process, meeting)}/{turn.id}/start")

    assert resp.status_code == 200
    audit_resp = client.get(f"/reparto/assignment-processes/{process.id}/audit-events/")
    assert audit_resp.status_code == 200
    event = audit_resp.json()["data"][0]
    assert event["event_type"] == "selection_turn.started"
    assert event["entity_type"] == "selection_turn"
    assert event["entity_id"] == str(turn.id)
    assert event["actor_user_id"] == str(current_user.id)
    assert event["before_json"]["status"] == "pending"
    assert event["after_json"]["status"] == "active"


def test_start_turn_rejects_non_pending_turn(
    client: TestClient, session: Session
) -> None:
    process, meeting = _open_meeting(session)
    teacher = _teacher(session, process, "A", 0)
    turn = factories.make_selection_turn(
        session, meeting, teacher, status=SelectionTurnStatus.COMPLETED
    )

    resp = client.post(f"{_turns_path(process, meeting)}/{turn.id}/start")

    assert resp.status_code == 400
    assert "pending turns" in resp.json()["detail"]


def test_turn_actions_require_open_meeting(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    meeting = factories.make_meeting_session(
        session, process, status=MeetingSessionStatus.PREPARED
    )
    teacher = _teacher(session, process, "A", 0)
    turn = factories.make_selection_turn(session, meeting, teacher)

    resp = client.post(f"{_turns_path(process, meeting)}/{turn.id}/start")

    assert resp.status_code == 400
    assert "must be open" in resp.json()["detail"]


def test_turn_actions_return_404_for_missing_turn(
    client: TestClient, session: Session
) -> None:
    process, meeting = _open_meeting(session)

    resp = client.post(f"{_turns_path(process, meeting)}/{uuid.uuid4()}/start")

    assert resp.status_code == 404


def test_skip_requires_reason(client: TestClient, session: Session) -> None:
    process, meeting = _open_meeting(session)
    teacher = _teacher(session, process, "A", 0)
    turn = factories.make_selection_turn(session, meeting, teacher)

    resp = client.post(f"{_turns_path(process, meeting)}/{turn.id}/skip", json={})

    assert resp.status_code == 422


def test_skip_turn_records_reason(client: TestClient, session: Session) -> None:
    process, meeting = _open_meeting(session)
    teacher = _teacher(session, process, "A", 0)
    turn = factories.make_selection_turn(
        session, meeting, teacher, status=SelectionTurnStatus.ACTIVE
    )

    resp = client.post(
        f"{_turns_path(process, meeting)}/{turn.id}/skip",
        json={"reason": "Absent"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"
    assert resp.json()["skip_reason"] == "Absent"
    assert resp.json()["skipped_at"] is not None
    audit_resp = client.get(f"/reparto/assignment-processes/{process.id}/audit-events/")
    event = audit_resp.json()["data"][0]
    assert event["event_type"] == "selection_turn.skipped"
    assert event["before_json"]["status"] == "active"
    assert event["after_json"]["status"] == "skipped"
    assert event["reason"] == "Absent"


def test_skip_rejects_finished_turn(client: TestClient, session: Session) -> None:
    process, meeting = _open_meeting(session)
    teacher = _teacher(session, process, "A", 0)
    turn = factories.make_selection_turn(
        session, meeting, teacher, status=SelectionTurnStatus.COMPLETED
    )

    resp = client.post(
        f"{_turns_path(process, meeting)}/{turn.id}/skip",
        json={"reason": "Too late"},
    )

    assert resp.status_code == 400
    assert "pending or active" in resp.json()["detail"]


def test_override_requires_writer(reader_client: TestClient, session: Session) -> None:
    process, meeting = _open_meeting(session)
    teacher = _teacher(session, process, "A", 0)
    turn = factories.make_selection_turn(session, meeting, teacher)

    resp = reader_client.post(
        f"{_turns_path(process, meeting)}/{turn.id}/override",
        json={"reason": "Department head decision"},
    )

    assert resp.status_code == 403


def test_override_records_actor(
    client: TestClient, session: Session, current_user
) -> None:
    process, meeting = _open_meeting(session)
    teacher = _teacher(session, process, "A", 0)
    turn = factories.make_selection_turn(session, meeting, teacher)

    resp = client.post(
        f"{_turns_path(process, meeting)}/{turn.id}/override",
        json={"reason": "Department head decision"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "overridden"
    assert body["skip_reason"] == "Department head decision"
    assert body["forced_by_user_id"] == str(current_user.id)
    audit_resp = client.get(f"/reparto/assignment-processes/{process.id}/audit-events/")
    event = audit_resp.json()["data"][0]
    assert event["event_type"] == "selection_turn.overridden"
    assert event["after_json"]["forced_by_user_id"] == str(current_user.id)
    assert event["reason"] == "Department head decision"


def test_override_preserves_existing_skipped_timestamp(
    client: TestClient, session: Session
) -> None:
    process, meeting = _open_meeting(session)
    teacher = _teacher(session, process, "A", 0)
    turn = factories.make_selection_turn(
        session, meeting, teacher, status=SelectionTurnStatus.ACTIVE
    )
    skipped = client.post(
        f"{_turns_path(process, meeting)}/{turn.id}/skip",
        json={"reason": "Absent"},
    ).json()["skipped_at"]

    resp = client.post(
        f"{_turns_path(process, meeting)}/{turn.id}/override",
        json={"reason": "Forced decision"},
    )

    assert resp.status_code == 200
    assert resp.json()["skipped_at"] == skipped


def test_complete_turn_without_assignment(client: TestClient, session: Session) -> None:
    process, meeting = _open_meeting(session)
    teacher = _teacher(session, process, "A", 0)
    turn = factories.make_selection_turn(
        session, meeting, teacher, status=SelectionTurnStatus.ACTIVE
    )

    resp = client.post(
        f"{_turns_path(process, meeting)}/{turn.id}/complete",
        json={"notes": "No available choice"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert resp.json()["notes"] == "No available choice"


def test_complete_turn_rejects_inactive_turn(
    client: TestClient, session: Session
) -> None:
    process, meeting = _open_meeting(session)
    teacher = _teacher(session, process, "A", 0)
    turn = factories.make_selection_turn(session, meeting, teacher)

    resp = client.post(
        f"{_turns_path(process, meeting)}/{turn.id}/complete",
        json={},
    )

    assert resp.status_code == 400
    assert "active turn" in resp.json()["detail"]


def test_complete_turn_records_assignment_and_actor_metadata(
    client: TestClient, session: Session, current_user
) -> None:
    process, meeting = _open_meeting(session)
    teacher = _teacher(session, process, "A", 0)
    turn = factories.make_selection_turn(
        session, meeting, teacher, status=SelectionTurnStatus.ACTIVE
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(session, process, group, subject)

    resp = client.post(
        f"{_turns_path(process, meeting)}/{turn.id}/complete",
        json={
            "assignment": {
                "assignment_process_id": str(process.id),
                "hour_requirement_id": str(requirement.id),
                "process_teacher_id": str(teacher.id),
                "assigned_hours": 4,
            },
            "notes": "Chose first group",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assignment = session.exec(select(Assignment)).one()
    assert assignment.source == AssignmentSource.DEPARTMENT_HEAD
    assert assignment.status == AssignmentStatus.CONFIRMED
    assert assignment.chosen_by_user_id == uuid.UUID(str(current_user.id))
    assert assignment.confirmed_by_user_id == uuid.UUID(str(current_user.id))
    audit_resp = client.get(f"/reparto/assignment-processes/{process.id}/audit-events/")
    assert [event["event_type"] for event in audit_resp.json()["data"]] == [
        "assignment.created",
        "selection_turn.completed",
    ]


def test_complete_turn_rejects_assignment_for_other_teacher(
    client: TestClient, session: Session
) -> None:
    process, meeting = _open_meeting(session)
    first = _teacher(session, process, "A", 0)
    second = _teacher(session, process, "B", 1)
    turn = factories.make_selection_turn(
        session, meeting, first, status=SelectionTurnStatus.ACTIVE
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(session, process, group, subject)

    resp = client.post(
        f"{_turns_path(process, meeting)}/{turn.id}/complete",
        json={
            "assignment": {
                "assignment_process_id": str(process.id),
                "hour_requirement_id": str(requirement.id),
                "process_teacher_id": str(second.id),
                "assigned_hours": 4,
            }
        },
    )

    assert resp.status_code == 400
    assert "active turn teacher" in resp.json()["detail"]


def test_summary_exposes_current_turn(client: TestClient, session: Session) -> None:
    process, meeting = _open_meeting(session)
    teacher = _teacher(session, process, "A", 0)
    turn = factories.make_selection_turn(
        session, meeting, teacher, status=SelectionTurnStatus.PENDING
    )
    started = client.post(f"{_turns_path(process, meeting)}/{turn.id}/start")
    assert started.status_code == 200

    resp = client.get(f"/reparto/assignment-processes/{process.id}/summary")

    assert resp.status_code == 200
    assert resp.json()["current_turn"]["selection_turn_id"] == str(turn.id)
