"""API tests for the group-subject matrix routes (plan §5.5, §7.2)."""

from __future__ import annotations

import uuid

from auth_sdk_m8.schemas.user import UserModel
from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.enums import AssignmentProcessStatus, TeachingPlanStatus
from tests import factories


def _base_payload(process_id: uuid.UUID, group_id: uuid.UUID, subject_id: uuid.UUID):
    return {
        "assignment_process_id": str(process_id),
        "teaching_group_id": str(group_id),
        "subject_id": str(subject_id),
    }


def test_create_group_subject_defaults(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    group = factories.make_teaching_group(session, process)
    subject = factories.make_subject(session, process)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/group-subjects/",
        json=_base_payload(process.id, group.id, subject.id),
    )
    assert resp.status_code == 201
    body = resp.json()
    # Hours inherit (NULL) until overridden; count defaults to 1; active by default.
    assert body["group_weekly_hours"] is None
    assert body["teacher_weekly_hours_per_position"] is None
    assert body["required_teacher_count"] == 1
    assert body["active"] is True


def test_create_group_subject_with_overrides(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    group = factories.make_teaching_group(session, process)
    subject = factories.make_subject(session, process)
    payload = _base_payload(process.id, group.id, subject.id)
    payload.update(
        {
            "group_weekly_hours": 3.0,
            "teacher_weekly_hours_per_position": 2.0,
            "required_teacher_count": 2,
            "active": False,
            "notes": "Co-teaching cell",
        }
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/group-subjects/",
        json=payload,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["group_weekly_hours"] == 3.0
    assert body["teacher_weekly_hours_per_position"] == 2.0
    assert body["required_teacher_count"] == 2
    assert body["active"] is False
    assert body["notes"] == "Co-teaching cell"


def test_create_group_subject_duplicate_rejected(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    group = factories.make_teaching_group(session, process)
    subject = factories.make_subject(session, process)
    payload = _base_payload(process.id, group.id, subject.id)
    first = client.post(
        f"/reparto/assignment-processes/{process.id}/group-subjects/", json=payload
    )
    assert first.status_code == 201
    dup = client.post(
        f"/reparto/assignment-processes/{process.id}/group-subjects/", json=payload
    )
    assert dup.status_code == 400


def test_create_group_subject_wrong_process_id_payload(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    group = factories.make_teaching_group(session, process)
    subject = factories.make_subject(session, process)
    payload = _base_payload(uuid.uuid4(), group.id, subject.id)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/group-subjects/", json=payload
    )
    assert resp.status_code == 400


def test_create_group_subject_rejects_group_from_other_process(
    client: TestClient, session: Session
) -> None:
    process_a = factories.make_assignment_process(session)
    process_b = factories.make_assignment_process(session)
    group_b = factories.make_teaching_group(session, process_b)
    subject_a = factories.make_subject(session, process_a)
    payload = _base_payload(process_a.id, group_b.id, subject_a.id)
    resp = client.post(
        f"/reparto/assignment-processes/{process_a.id}/group-subjects/", json=payload
    )
    assert resp.status_code == 404


def test_create_group_subject_rejects_subject_from_other_process(
    client: TestClient, session: Session
) -> None:
    process_a = factories.make_assignment_process(session)
    process_b = factories.make_assignment_process(session)
    group_a = factories.make_teaching_group(session, process_a)
    subject_b = factories.make_subject(session, process_b)
    payload = _base_payload(process_a.id, group_a.id, subject_b.id)
    resp = client.post(
        f"/reparto/assignment-processes/{process_a.id}/group-subjects/", json=payload
    )
    assert resp.status_code == 404


def test_create_group_subject_rejects_zero_teacher_count(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    group = factories.make_teaching_group(session, process)
    subject = factories.make_subject(session, process)
    payload = _base_payload(process.id, group.id, subject.id)
    payload["required_teacher_count"] = 0
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/group-subjects/", json=payload
    )
    assert resp.status_code == 422


def test_create_group_subject_blocked_on_final_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    group = factories.make_teaching_group(session, process)
    subject = factories.make_subject(session, process)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/group-subjects/",
        json=_base_payload(process.id, group.id, subject.id),
    )
    assert resp.status_code == 400


def test_list_group_subjects(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    group = factories.make_teaching_group(session, process)
    s1 = factories.make_subject(session, process, name="Math")
    s2 = factories.make_subject(session, process, name="Physics")
    factories.make_group_subject(session, process, group, s1)
    factories.make_group_subject(session, process, group, s2)
    resp = client.get(f"/reparto/assignment-processes/{process.id}/group-subjects/")
    assert resp.status_code == 200
    assert resp.json()["count"] == 2


def test_get_group_subject(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    group = factories.make_teaching_group(session, process)
    subject = factories.make_subject(session, process)
    gs = factories.make_group_subject(session, process, group, subject)
    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/group-subjects/{gs.id}"
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == str(gs.id)


def test_get_group_subject_not_found(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/group-subjects/{uuid.uuid4()}"
    )
    assert resp.status_code == 404


def test_get_group_subject_from_other_process_404(
    client: TestClient, session: Session
) -> None:
    process_a = factories.make_assignment_process(session)
    process_b = factories.make_assignment_process(session)
    group_b = factories.make_teaching_group(session, process_b)
    subject_b = factories.make_subject(session, process_b)
    gs_b = factories.make_group_subject(session, process_b, group_b, subject_b)
    resp = client.get(
        f"/reparto/assignment-processes/{process_a.id}/group-subjects/{gs_b.id}"
    )
    assert resp.status_code == 404


def test_update_group_subject(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    group = factories.make_teaching_group(session, process)
    subject = factories.make_subject(session, process)
    gs = factories.make_group_subject(session, process, group, subject)
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/group-subjects/{gs.id}",
        json={"group_weekly_hours": 4.5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["group_weekly_hours"] == 4.5
    assert body["active"] is True


def test_update_cannot_bypass_guarded_retirement(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    group = factories.make_teaching_group(session, process)
    subject = factories.make_subject(session, process)
    cell = factories.make_group_subject(session, process, group, subject)
    response = client.patch(
        f"/reparto/assignment-processes/{process.id}/group-subjects/{cell.id}",
        json={"active": False},
    )
    assert response.status_code == 409


def test_update_group_subject_rejects_zero_teacher_count(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    group = factories.make_teaching_group(session, process)
    subject = factories.make_subject(session, process)
    gs = factories.make_group_subject(session, process, group, subject)
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/group-subjects/{gs.id}",
        json={"required_teacher_count": 0},
    )
    assert resp.status_code == 422


def test_retire_group_subject_preserves_row_and_removes_delete_path(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    group = factories.make_teaching_group(session, process)
    subject = factories.make_subject(session, process)
    gs = factories.make_group_subject(session, process, group, subject)
    path = f"/reparto/assignment-processes/{process.id}/group-subjects/{gs.id}"
    resp = client.post(f"{path}/retire")
    assert resp.status_code == 200
    assert resp.json()["active"] is False
    assert client.get(path).status_code == 200
    assert client.delete(path).status_code == 405
    assert client.post(f"{path}/retire").status_code == 409


def test_retire_group_subject_blocks_live_downstream_activity(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    group = factories.make_teaching_group(session, process)
    subject = factories.make_subject(session, process)
    cell = factories.make_group_subject(session, process, group, subject)
    activity = factories.make_teaching_activity(
        session, plan, subject, group_subjects=[cell]
    )
    path = f"/reparto/assignment-processes/{process.id}/group-subjects/{cell.id}/retire"
    assert client.post(path).status_code == 409

    activity.retired_at = activity.created_at
    session.add(activity)
    session.commit()
    assert client.post(path).status_code == 200


def test_retire_group_subject_keeps_an_already_unbalanced_plan(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(
        session, process, status=TeachingPlanStatus.UNBALANCED
    )
    group = factories.make_teaching_group(session, process)
    subject = factories.make_subject(session, process)
    cell = factories.make_group_subject(session, process, group, subject)
    path = f"/reparto/assignment-processes/{process.id}/group-subjects/{cell.id}/retire"

    assert client.post(path).status_code == 200
    session.refresh(plan)
    assert plan.status == TeachingPlanStatus.UNBALANCED


def test_retire_group_subject_requires_draft_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.READY_FOR_MEETING
    )
    group = factories.make_teaching_group(session, process)
    subject = factories.make_subject(session, process)
    cell = factories.make_group_subject(session, process, group, subject)
    path = f"/reparto/assignment-processes/{process.id}/group-subjects/{cell.id}/retire"
    assert client.post(path).status_code == 409


def test_retire_group_subject_requires_writer(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    process = factories.make_assignment_process(session)
    factories.enrol(session, process, reader)
    group = factories.make_teaching_group(session, process)
    subject = factories.make_subject(session, process)
    cell = factories.make_group_subject(session, process, group, subject)
    path = f"/reparto/assignment-processes/{process.id}/group-subjects/{cell.id}/retire"
    assert reader_client.post(path).status_code == 403


def test_create_group_subject_forbidden_for_reader(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    process = factories.make_assignment_process(session)
    factories.enrol(session, process, reader)
    group = factories.make_teaching_group(session, process)
    subject = factories.make_subject(session, process)
    resp = reader_client.post(
        f"/reparto/assignment-processes/{process.id}/group-subjects/",
        json=_base_payload(process.id, group.id, subject.id),
    )
    assert resp.status_code == 403
