"""API tests for the teaching-activity routes (plan §5.6, §5.7, §7.4)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.enums import (
    AssignmentProcessStatus,
    TeachingActivitySource,
    TeachingPlanStatus,
)
from tests import factories


def _setup(session: Session, *, plan_status=TeachingPlanStatus.DRAFT, **subject_kwargs):
    """Create a process + plan + subject + one group-subject cell."""
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process, status=plan_status)
    subject = factories.make_subject(session, process, **subject_kwargs)
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(session, process, group, subject)
    return process, plan, subject, group, cell


def _payload(subject_id: uuid.UUID, cell_ids: list[uuid.UUID], **extra):
    body = {
        "subject_id": str(subject_id),
        "group_weekly_hours_per_group": 2.0,
        "teacher_weekly_hours_per_position": 2.0,
        "group_subject_ids": [str(c) for c in cell_ids],
    }
    body.update(extra)
    return body


# ── create ───────────────────────────────────────────────────────────────────


def test_create_activity_single_link(client: TestClient, session: Session) -> None:
    process, _plan, subject, _group, cell = _setup(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json=_payload(
            subject.id, [cell.id], required_teacher_count=2, notes="Co-teach"
        ),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["group_subject_ids"] == [str(cell.id)]
    assert body["linked_group_count"] == 1
    assert body["required_teacher_count"] == 2
    assert body["source"] == TeachingActivitySource.SECONDARY_MANUAL.value
    assert body["sync_state"] == "in_sync"
    assert body["retired_at"] is None
    assert body["notes"] == "Co-teach"


def test_create_activity_zero_group_allowed(
    client: TestClient, session: Session
) -> None:
    process, _plan, subject, _group, _cell = _setup(session, allows_zero_groups=True)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json=_payload(subject.id, []),
    )
    assert resp.status_code == 201
    assert resp.json()["linked_group_count"] == 0


def test_create_activity_zero_group_rejected(
    client: TestClient, session: Session
) -> None:
    process, _plan, subject, _group, _cell = _setup(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json=_payload(subject.id, []),
    )
    assert resp.status_code == 400


def test_create_activity_multi_group_allowed(
    client: TestClient, session: Session
) -> None:
    process, _plan, subject, _group, cell1 = _setup(
        session, allows_multiple_groups=True
    )
    group2 = factories.make_teaching_group(session, process, group_code="B")
    cell2 = factories.make_group_subject(session, process, group2, subject)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json=_payload(subject.id, [cell1.id, cell2.id]),
    )
    assert resp.status_code == 201
    assert resp.json()["linked_group_count"] == 2


def test_create_activity_multi_group_rejected(
    client: TestClient, session: Session
) -> None:
    process, _plan, subject, _group, cell1 = _setup(session)
    group2 = factories.make_teaching_group(session, process, group_code="B")
    cell2 = factories.make_group_subject(session, process, group2, subject)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json=_payload(subject.id, [cell1.id, cell2.id]),
    )
    assert resp.status_code == 400


def test_create_activity_duplicate_link_rejected(
    client: TestClient, session: Session
) -> None:
    process, _plan, subject, _group, cell = _setup(session, allows_multiple_groups=True)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json=_payload(subject.id, [cell.id, cell.id]),
    )
    assert resp.status_code == 400


def test_create_activity_link_from_other_process_404(
    client: TestClient, session: Session
) -> None:
    process, _plan, subject, _group, _cell = _setup(session)
    other = factories.make_assignment_process(session)
    other_subject = factories.make_subject(session, other)
    other_group = factories.make_teaching_group(session, other)
    other_cell = factories.make_group_subject(
        session, other, other_group, other_subject
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json=_payload(subject.id, [other_cell.id]),
    )
    assert resp.status_code == 404


def test_create_activity_link_subject_mismatch_rejected(
    client: TestClient, session: Session
) -> None:
    process, _plan, subject, _group, _cell = _setup(session)
    other_subject = factories.make_subject(session, process, name="Physics")
    group2 = factories.make_teaching_group(session, process, group_code="B")
    other_cell = factories.make_group_subject(session, process, group2, other_subject)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json=_payload(subject.id, [other_cell.id]),
    )
    assert resp.status_code == 400


def test_create_activity_rejects_main_generated_source(
    client: TestClient, session: Session
) -> None:
    process, _plan, subject, _group, cell = _setup(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json=_payload(
            subject.id,
            [cell.id],
            source=TeachingActivitySource.MAIN_GENERATED.value,
        ),
    )
    assert resp.status_code == 400


def test_create_activity_subject_from_other_process_404(
    client: TestClient, session: Session
) -> None:
    process, _plan, _subject, _group, _cell = _setup(session)
    other = factories.make_assignment_process(session)
    other_subject = factories.make_subject(session, other)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json=_payload(other_subject.id, []),
    )
    assert resp.status_code == 404


def test_create_activity_without_plan_400(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json=_payload(subject.id, []),
    )
    assert resp.status_code == 400


def test_create_activity_locked_plan_400(client: TestClient, session: Session) -> None:
    process, _plan, subject, _group, cell = _setup(
        session, plan_status=TeachingPlanStatus.LOCKED
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json=_payload(subject.id, [cell.id]),
    )
    assert resp.status_code == 400


def test_create_activity_final_process_400(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json=_payload(subject.id, []),
    )
    assert resp.status_code == 400


def test_create_activity_rejects_zero_teacher_count(
    client: TestClient, session: Session
) -> None:
    process, _plan, subject, _group, cell = _setup(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json=_payload(subject.id, [cell.id], required_teacher_count=0),
    )
    assert resp.status_code == 422


def test_create_activity_forbidden_for_reader(
    reader_client: TestClient, session: Session
) -> None:
    process, _plan, subject, _group, cell = _setup(session)
    resp = reader_client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json=_payload(subject.id, [cell.id]),
    )
    assert resp.status_code == 403


# ── list / get ───────────────────────────────────────────────────────────────


def test_list_activities_empty_without_plan(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/"
    )
    assert resp.status_code == 200
    assert resp.json() == {"data": [], "count": 0}


def test_list_activities(client: TestClient, session: Session) -> None:
    process, plan, subject, _group, cell = _setup(session)
    factories.make_teaching_activity(session, plan, subject, group_subjects=[cell])
    factories.make_teaching_activity(session, plan, subject)
    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/"
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 2


def test_get_activity(client: TestClient, session: Session) -> None:
    process, plan, subject, _group, cell = _setup(session)
    activity = factories.make_teaching_activity(
        session, plan, subject, group_subjects=[cell]
    )
    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/{activity.id}"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(activity.id)
    assert body["group_subject_ids"] == [str(cell.id)]


def test_get_activity_not_found(client: TestClient, session: Session) -> None:
    process, _plan, _subject, _group, _cell = _setup(session)
    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/{uuid.uuid4()}"
    )
    assert resp.status_code == 404


def test_get_activity_process_without_plan_404(
    client: TestClient, session: Session
) -> None:
    process_a = factories.make_assignment_process(session)
    _p, plan_b, subject_b, _g, cell_b = _setup(session)
    activity_b = factories.make_teaching_activity(
        session, plan_b, subject_b, group_subjects=[cell_b]
    )
    resp = client.get(
        f"/reparto/assignment-processes/{process_a.id}/teaching-activities/"
        f"{activity_b.id}"
    )
    assert resp.status_code == 404


def test_get_activity_from_other_process_404(
    client: TestClient, session: Session
) -> None:
    process_a = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process_a)
    _p, plan_b, subject_b, _g, cell_b = _setup(session)
    activity_b = factories.make_teaching_activity(
        session, plan_b, subject_b, group_subjects=[cell_b]
    )
    resp = client.get(
        f"/reparto/assignment-processes/{process_a.id}/teaching-activities/"
        f"{activity_b.id}"
    )
    assert resp.status_code == 404


# ── update ───────────────────────────────────────────────────────────────────


def test_update_activity_fields(client: TestClient, session: Session) -> None:
    process, plan, subject, _group, cell = _setup(session)
    activity = factories.make_teaching_activity(
        session, plan, subject, group_subjects=[cell]
    )
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/{activity.id}",
        json={"group_weekly_hours_per_group": 3.5, "required_teacher_count": 3},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["group_weekly_hours_per_group"] == 3.5
    assert body["required_teacher_count"] == 3
    # Links untouched when group_subject_ids is omitted.
    assert body["group_subject_ids"] == [str(cell.id)]


def test_update_activity_replace_links(client: TestClient, session: Session) -> None:
    process, plan, subject, _group, cell1 = _setup(session, allows_multiple_groups=True)
    group2 = factories.make_teaching_group(session, process, group_code="B")
    cell2 = factories.make_group_subject(session, process, group2, subject)
    activity = factories.make_teaching_activity(
        session, plan, subject, group_subjects=[cell1]
    )
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/{activity.id}",
        json={"group_subject_ids": [str(cell1.id), str(cell2.id)]},
    )
    assert resp.status_code == 200
    assert resp.json()["linked_group_count"] == 2


def test_update_activity_replace_links_validates(
    client: TestClient, session: Session
) -> None:
    process, plan, subject, _group, cell = _setup(session)
    other_subject = factories.make_subject(session, process, name="Physics")
    group2 = factories.make_teaching_group(session, process, group_code="B")
    other_cell = factories.make_group_subject(session, process, group2, other_subject)
    activity = factories.make_teaching_activity(
        session, plan, subject, group_subjects=[cell]
    )
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/{activity.id}",
        json={"group_subject_ids": [str(other_cell.id)]},
    )
    assert resp.status_code == 400


def test_update_activity_not_found(client: TestClient, session: Session) -> None:
    process, _plan, _subject, _group, _cell = _setup(session)
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/{uuid.uuid4()}",
        json={"notes": "x"},
    )
    assert resp.status_code == 404


def test_update_activity_locked_plan_400(client: TestClient, session: Session) -> None:
    process, plan, subject, _group, cell = _setup(session)
    activity = factories.make_teaching_activity(
        session, plan, subject, group_subjects=[cell]
    )
    plan.status = TeachingPlanStatus.LOCKED
    session.add(plan)
    session.commit()
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/{activity.id}",
        json={"notes": "x"},
    )
    assert resp.status_code == 400


def test_update_activity_forbidden_for_reader(
    reader_client: TestClient, session: Session
) -> None:
    process, plan, subject, _group, cell = _setup(session)
    activity = factories.make_teaching_activity(
        session, plan, subject, group_subjects=[cell]
    )
    resp = reader_client.patch(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/{activity.id}",
        json={"notes": "x"},
    )
    assert resp.status_code == 403


# ── delete ───────────────────────────────────────────────────────────────────


def test_delete_activity(client: TestClient, session: Session) -> None:
    process, plan, subject, _group, cell = _setup(session)
    activity = factories.make_teaching_activity(
        session, plan, subject, group_subjects=[cell]
    )
    resp = client.delete(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/{activity.id}"
    )
    assert resp.status_code == 200
    follow = client.get(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/{activity.id}"
    )
    assert follow.status_code == 404
    # The link rows are gone too.
    from reparto_service.db_models.teaching_activities import TeachingActivityGroup
    from sqlmodel import select

    remaining = session.exec(
        select(TeachingActivityGroup).where(
            TeachingActivityGroup.teaching_activity_id == activity.id
        )
    ).all()
    assert remaining == []


def test_delete_zero_group_activity(client: TestClient, session: Session) -> None:
    process, plan, subject, _group, _cell = _setup(session, allows_zero_groups=True)
    activity = factories.make_teaching_activity(session, plan, subject)
    resp = client.delete(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/{activity.id}"
    )
    assert resp.status_code == 200


def test_delete_activity_not_found(client: TestClient, session: Session) -> None:
    process, _plan, _subject, _group, _cell = _setup(session)
    resp = client.delete(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/{uuid.uuid4()}"
    )
    assert resp.status_code == 404


def test_delete_activity_locked_plan_400(client: TestClient, session: Session) -> None:
    process, plan, subject, _group, cell = _setup(session)
    activity = factories.make_teaching_activity(
        session, plan, subject, group_subjects=[cell]
    )
    plan.status = TeachingPlanStatus.LOCKED
    session.add(plan)
    session.commit()
    resp = client.delete(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/{activity.id}"
    )
    assert resp.status_code == 400


def test_delete_activity_forbidden_for_reader(
    reader_client: TestClient, session: Session
) -> None:
    process, plan, subject, _group, cell = _setup(session)
    activity = factories.make_teaching_activity(
        session, plan, subject, group_subjects=[cell]
    )
    resp = reader_client.delete(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/{activity.id}"
    )
    assert resp.status_code == 403
