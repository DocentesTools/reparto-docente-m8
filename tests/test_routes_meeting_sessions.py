"""API tests for Phase 2 meeting-session LAN read mode."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.enums import (
    AssignmentProcessStatus,
    MeetingSessionStatus,
    SelectionOrderMode,
    TeachingPlanStatus,
)
from tests import factories


def _ready_plan(session: Session, process: AssignmentProcess) -> None:
    """Attach a balanced/locked/generated plan so a meeting may be opened.

    Opening a meeting is gated on plan readiness (plan §3.10); tests that
    actually start a session need the plan in ``REQUIREMENTS_GENERATED``.
    """
    factories.make_teaching_plan(
        session, process, status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )


def _session_payload(
    process: AssignmentProcess,
    *,
    status: str = "prepared",
    lan_access_enabled: bool = True,
    direct_teacher_selection_enabled: bool = False,
    selection_mode: str = "none",
) -> dict[str, object]:
    return {
        "assignment_process_id": str(process.id),
        "status": status,
        "lan_access_enabled": lan_access_enabled,
        "direct_teacher_selection_enabled": direct_teacher_selection_enabled,
        "selection_mode": selection_mode,
    }


def test_create_prepared_meeting_session(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/",
        json=_session_payload(process),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["assignment_process_id"] == str(process.id)
    assert body["status"] == "prepared"
    assert body["lan_access_enabled"] is True
    assert body["started_at"] is None


def test_create_open_session_sets_start_metadata_and_process_flags(
    client: TestClient, session: Session, current_user
) -> None:
    process = factories.make_assignment_process(session)
    _ready_plan(session, process)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/",
        json=_session_payload(process, status="open", selection_mode="informative"),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["started_at"] is not None
    assert body["started_by_user_id"] == str(current_user.id)
    session.refresh(process)
    assert process.status == AssignmentProcessStatus.MEETING_OPEN
    assert process.lan_access_enabled is True
    assert process.selection_order_mode == SelectionOrderMode.INFORMATIVE


def test_create_session_rejects_payload_process_mismatch(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    other = factories.make_assignment_process(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/",
        json=_session_payload(other),
    )
    assert resp.status_code == 400
    assert "does not match" in resp.json()["detail"]


def test_create_session_rejects_second_active_session(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_meeting_session(session, process, status=MeetingSessionStatus.OPEN)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/",
        json=_session_payload(process),
    )
    assert resp.status_code == 400
    assert "active meeting session" in resp.json()["detail"]


def test_create_session_allows_new_session_after_closed_one(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_meeting_session(session, process, status=MeetingSessionStatus.CLOSED)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/",
        json=_session_payload(process),
    )
    assert resp.status_code == 201


def test_create_session_rejects_direct_selection_without_lan(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/",
        json=_session_payload(
            process,
            lan_access_enabled=False,
            direct_teacher_selection_enabled=True,
        ),
    )
    assert resp.status_code == 422


def test_create_paused_session_sets_pause_timestamp(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/",
        json=_session_payload(process, status="paused"),
    )
    assert resp.status_code == 201
    assert resp.json()["paused_at"] is not None


def test_reader_can_list_and_get_sessions(
    reader_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    meeting_session = factories.make_meeting_session(session, process)
    resp = reader_client.get(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/"
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    resp = reader_client.get(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/"
        f"{meeting_session.id}"
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == str(meeting_session.id)


def test_reader_cannot_create_session(
    reader_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = reader_client.post(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/",
        json=_session_payload(process),
    )
    assert resp.status_code == 403


def test_update_session_can_open_prepared_session(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    _ready_plan(session, process)
    meeting_session = factories.make_meeting_session(session, process)
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/"
        f"{meeting_session.id}",
        json={"status": "open"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "open"
    assert resp.json()["started_at"] is not None


def test_update_session_rejects_invalid_direct_selection_merge(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    meeting_session = factories.make_meeting_session(session, process)
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/"
        f"{meeting_session.id}",
        json={
            "lan_access_enabled": False,
            "direct_teacher_selection_enabled": True,
        },
    )
    assert resp.status_code == 422


def test_update_session_rejects_disabling_lan_when_direct_enabled(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    meeting_session = factories.make_meeting_session(
        session,
        process,
        lan_access_enabled=True,
        direct_teacher_selection_enabled=True,
    )
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/"
        f"{meeting_session.id}",
        json={"lan_access_enabled": False},
    )
    assert resp.status_code == 422


def test_update_session_to_paused_sets_pause_timestamp(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    meeting_session = factories.make_meeting_session(session, process)
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/"
        f"{meeting_session.id}",
        json={"status": "paused"},
    )
    assert resp.status_code == 200
    assert resp.json()["paused_at"] is not None


def test_update_session_to_closed_sets_close_timestamp(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    meeting_session = factories.make_meeting_session(session, process)
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/"
        f"{meeting_session.id}",
        json={"status": "closed"},
    )
    assert resp.status_code == 200
    assert resp.json()["closed_at"] is not None


def test_close_session_disables_process_lan_flags(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    meeting_session = factories.make_meeting_session(
        session,
        process,
        status=MeetingSessionStatus.OPEN,
        direct_teacher_selection_enabled=True,
    )
    process.lan_access_enabled = True
    process.direct_teacher_selection_enabled = True
    session.add(process)
    session.commit()
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/"
        f"{meeting_session.id}/close"
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"
    assert resp.json()["closed_at"] is not None
    session.refresh(process)
    assert process.lan_access_enabled is False
    assert process.direct_teacher_selection_enabled is False


def test_session_endpoints_return_404_for_missing_session(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/{uuid.uuid4()}"
    )
    assert resp.status_code == 404


def test_cannot_create_session_on_final_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/",
        json=_session_payload(process),
    )
    assert resp.status_code == 400
    assert "final" in resp.json()["detail"].lower()


# ── Plan-readiness gate on opening a meeting (plan §3.10) ─────────────────────


def test_cannot_open_session_without_teaching_plan(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/",
        json=_session_payload(process, status="open"),
    )
    assert resp.status_code == 409
    assert "no teaching plan" in resp.json()["detail"]


def test_cannot_open_session_when_plan_unbalanced(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process, status=TeachingPlanStatus.UNBALANCED)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/",
        json=_session_payload(process, status="open"),
    )
    assert resp.status_code == 409
    assert "unbalanced" in resp.json()["detail"]


def test_cannot_open_session_when_plan_stale(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process, status=TeachingPlanStatus.STALE)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/",
        json=_session_payload(process, status="open"),
    )
    assert resp.status_code == 409
    assert "stale" in resp.json()["detail"]


def test_cannot_open_prepared_session_via_update_without_ready_plan(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process, status=TeachingPlanStatus.LOCKED)
    meeting_session = factories.make_meeting_session(session, process)
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/"
        f"{meeting_session.id}",
        json={"status": "open"},
    )
    assert resp.status_code == 409
    assert "locked" in resp.json()["detail"]


def test_prepared_session_is_not_gated_on_plan(
    client: TestClient, session: Session
) -> None:
    """A prepared (not yet started) session does not open the meeting → no gate."""
    process = factories.make_assignment_process(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/meeting-sessions/",
        json=_session_payload(process, status="prepared"),
    )
    assert resp.status_code == 201
