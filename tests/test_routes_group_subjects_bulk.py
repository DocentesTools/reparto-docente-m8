"""API tests for the group-subject bulk preview/apply routes (plan §7.2, §8.4)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from reparto_service.db_models.audit_events import AuditEvent
from reparto_service.enums import AssignmentProcessStatus
from tests import factories


def _preview_url(process_id: uuid.UUID) -> str:
    return f"/reparto/assignment-processes/{process_id}/group-subjects/bulk-preview"


def _apply_url(process_id: uuid.UUID) -> str:
    return f"/reparto/assignment-processes/{process_id}/group-subjects/bulk-apply"


# ── Preview ──────────────────────────────────────────────────────────────────


def test_bulk_preview_create_missing(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    g1 = factories.make_teaching_group(session, process, grade=1, group_code="A")
    g2 = factories.make_teaching_group(session, process, grade=2, group_code="B")
    # g1 already has a cell; g2 does not.
    factories.make_group_subject(session, process, g1, subject)
    resp = client.post(
        _preview_url(process.id),
        json={
            "subject_id": str(subject.id),
            "mode": "create_missing",
            "group_weekly_hours": 3.0,
            "required_teacher_count": 2,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["matched_group_ids"]) == 2
    assert len(body["to_create"]) == 1
    assert body["to_create"][0]["teaching_group_id"] == str(g2.id)
    assert body["to_create"][0]["group_subject_id"] is None
    assert body["to_create"][0]["group_weekly_hours"] == 3.0
    assert body["to_create"][0]["required_teacher_count"] == 2
    assert len(body["unchanged"]) == 1
    assert body["unchanged"][0]["teaching_group_id"] == str(g1.id)
    assert body["conflicts"] == []
    assert body["validation_errors"] == []
    assert body["expected_affected_count"] == 1


def test_bulk_preview_update_existing_reports_conflicts(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    g1 = factories.make_teaching_group(session, process, grade=1, group_code="A")
    g2 = factories.make_teaching_group(session, process, grade=2, group_code="B")
    factories.make_group_subject(session, process, g1, subject, group_weekly_hours=1.0)
    resp = client.post(
        _preview_url(process.id),
        json={
            "subject_id": str(subject.id),
            "mode": "update_existing",
            "group_weekly_hours": 5.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["to_update"]) == 1
    assert body["to_update"][0]["teaching_group_id"] == str(g1.id)
    assert body["to_update"][0]["group_weekly_hours"] == 5.0
    # g2 has no row -> cannot update -> conflict.
    assert len(body["conflicts"]) == 1
    assert body["conflicts"][0]["teaching_group_id"] == str(g2.id)
    assert body["expected_affected_count"] == 1


def test_bulk_preview_update_unchanged_when_values_equal(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    factories.make_group_subject(
        session, process, group, subject, group_weekly_hours=4.0
    )
    resp = client.post(
        _preview_url(process.id),
        json={
            "subject_id": str(subject.id),
            "mode": "update_existing",
            "group_weekly_hours": 4.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["to_update"] == []
    assert len(body["unchanged"]) == 1
    assert body["expected_affected_count"] == 0


def test_bulk_preview_upsert_mix(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    g1 = factories.make_teaching_group(session, process, grade=1, group_code="A")
    factories.make_teaching_group(session, process, grade=2, group_code="B")
    factories.make_group_subject(session, process, g1, subject, group_weekly_hours=1.0)
    resp = client.post(
        _preview_url(process.id),
        json={
            "subject_id": str(subject.id),
            "mode": "upsert",
            "group_weekly_hours": 2.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["to_create"]) == 1  # g2
    assert len(body["to_update"]) == 1  # g1 (1.0 -> 2.0)
    assert body["expected_affected_count"] == 2


def test_bulk_preview_stage_filter(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    factories.make_teaching_group(
        session, process, stage="Primaria", stage_label="EP", grade=1, group_code="A"
    )
    factories.make_teaching_group(
        session, process, stage="Secundaria", stage_label="ESO", grade=1, group_code="B"
    )
    resp = client.post(
        _preview_url(process.id),
        json={
            "subject_id": str(subject.id),
            "mode": "create_missing",
            "stage": "Primaria",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["matched_group_ids"]) == 1
    assert len(body["to_create"]) == 1


def test_bulk_preview_grade_range_filter(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    factories.make_teaching_group(session, process, grade=1, group_code="A")
    factories.make_teaching_group(session, process, grade=2, group_code="B")
    factories.make_teaching_group(session, process, grade=3, group_code="C")
    resp = client.post(
        _preview_url(process.id),
        json={
            "subject_id": str(subject.id),
            "mode": "create_missing",
            "minimum_grade": 2,
            "maximum_grade": 3,
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()["matched_group_ids"]) == 2


def test_bulk_preview_invalid_grade_range(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    factories.make_teaching_group(session, process, grade=1, group_code="A")
    resp = client.post(
        _preview_url(process.id),
        json={
            "subject_id": str(subject.id),
            "mode": "create_missing",
            "minimum_grade": 5,
            "maximum_grade": 2,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_group_ids"] == []
    assert len(body["validation_errors"]) == 1
    assert body["expected_affected_count"] == 0


def test_bulk_preview_subject_not_in_process_404(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(
        _preview_url(process.id),
        json={"subject_id": str(uuid.uuid4()), "mode": "create_missing"},
    )
    assert resp.status_code == 404


def test_bulk_preview_forbidden_for_reader(
    reader_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    resp = reader_client.post(
        _preview_url(process.id),
        json={"subject_id": str(subject.id), "mode": "create_missing"},
    )
    assert resp.status_code == 403


# ── Apply ────────────────────────────────────────────────────────────────────


def test_bulk_apply_create_missing_commits(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    factories.make_teaching_group(session, process, grade=1, group_code="A")
    factories.make_teaching_group(session, process, grade=2, group_code="B")
    resp = client.post(
        _apply_url(process.id),
        json={
            "subject_id": str(subject.id),
            "mode": "create_missing",
            "group_weekly_hours": 3.0,
            "expected_affected_count": 2,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created_count"] == 2
    assert body["updated_count"] == 0
    assert body["count"] == 2
    assert all(row["group_weekly_hours"] == 3.0 for row in body["data"])
    # Rows are persisted.
    listing = client.get(f"/reparto/assignment-processes/{process.id}/group-subjects/")
    assert listing.json()["count"] == 2
    # Exactly one audit event, carrying row-level detail.
    events = session.exec(
        select(AuditEvent).where(AuditEvent.event_type == "group_subject.bulk_applied")
    ).all()
    assert len(events) == 1
    assert events[0].after_json is not None
    assert events[0].after_json["created"] == 2
    assert len(events[0].after_json["rows"]) == 2


def test_bulk_apply_create_missing_default_count(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    factories.make_teaching_group(session, process)
    resp = client.post(
        _apply_url(process.id),
        json={
            "subject_id": str(subject.id),
            "mode": "create_missing",
            "expected_affected_count": 1,
        },
    )
    assert resp.status_code == 200
    row = resp.json()["data"][0]
    # Unset hours inherit (NULL); unset count falls back to 1.
    assert row["group_weekly_hours"] is None
    assert row["required_teacher_count"] == 1


def test_bulk_apply_update_existing_commits(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    gs = factories.make_group_subject(
        session, process, group, subject, group_weekly_hours=1.0
    )
    resp = client.post(
        _apply_url(process.id),
        json={
            "subject_id": str(subject.id),
            "mode": "update_existing",
            "group_weekly_hours": 6.5,
            "expected_affected_count": 1,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated_count"] == 1
    assert body["created_count"] == 0
    detail = client.get(
        f"/reparto/assignment-processes/{process.id}/group-subjects/{gs.id}"
    )
    assert detail.json()["group_weekly_hours"] == 6.5


def test_bulk_apply_upsert_commits(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    g1 = factories.make_teaching_group(session, process, grade=1, group_code="A")
    factories.make_teaching_group(session, process, grade=2, group_code="B")
    factories.make_group_subject(session, process, g1, subject, group_weekly_hours=1.0)
    resp = client.post(
        _apply_url(process.id),
        json={
            "subject_id": str(subject.id),
            "mode": "upsert",
            "group_weekly_hours": 2.0,
            "expected_affected_count": 2,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created_count"] == 1
    assert body["updated_count"] == 1


def test_bulk_apply_stale_count_rejected(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    g1 = factories.make_teaching_group(session, process, grade=1, group_code="A")
    factories.make_teaching_group(session, process, grade=2, group_code="B")
    # Preview would report 2 creates; the selection then changes underneath.
    factories.make_group_subject(session, process, g1, subject)
    resp = client.post(
        _apply_url(process.id),
        json={
            "subject_id": str(subject.id),
            "mode": "create_missing",
            "expected_affected_count": 2,
        },
    )
    assert resp.status_code == 409


def test_bulk_apply_invalid_grade_range_rejected(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    resp = client.post(
        _apply_url(process.id),
        json={
            "subject_id": str(subject.id),
            "mode": "create_missing",
            "minimum_grade": 5,
            "maximum_grade": 2,
            "expected_affected_count": 0,
        },
    )
    assert resp.status_code == 400


def test_bulk_apply_blocked_on_final_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    subject = factories.make_subject(session, process)
    factories.make_teaching_group(session, process)
    resp = client.post(
        _apply_url(process.id),
        json={
            "subject_id": str(subject.id),
            "mode": "create_missing",
            "expected_affected_count": 1,
        },
    )
    assert resp.status_code == 400


def test_bulk_apply_subject_not_in_process_404(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(
        _apply_url(process.id),
        json={
            "subject_id": str(uuid.uuid4()),
            "mode": "create_missing",
            "expected_affected_count": 0,
        },
    )
    assert resp.status_code == 404


def test_bulk_apply_forbidden_for_reader(
    reader_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    resp = reader_client.post(
        _apply_url(process.id),
        json={
            "subject_id": str(subject.id),
            "mode": "create_missing",
            "expected_affected_count": 0,
        },
    )
    assert resp.status_code == 403
