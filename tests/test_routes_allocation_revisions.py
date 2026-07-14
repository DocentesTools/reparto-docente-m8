"""API + controller tests for department hour-allocation revisions (plan §5.1)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from reparto_service.db_models.department_hour_allocation_revisions import (
    DepartmentHourAllocationRevision,
)
from reparto_service.enums import AssignmentProcessStatus
from tests import factories

_BASE = "/reparto/assignment-processes"


# ── Create ────────────────────────────────────────────────────────────────────


def test_create_first_revision(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(
        f"{_BASE}/{process.id}/allocation-revisions/",
        json={"allocated_group_weekly_hours": 120, "reason": "First allocation"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["revision_number"] == 1
    assert body["allocated_group_weekly_hours"] == 120
    assert body["reason"] == "First allocation"
    assert body["source"] == "manual_transcription"
    assert body["superseded_at"] is None
    assert body["assignment_process_id"] == str(process.id)


def test_create_revision_supersedes_previous(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    client.post(
        f"{_BASE}/{process.id}/allocation-revisions/",
        json={"allocated_group_weekly_hours": 120, "reason": "First"},
    )
    resp = client.post(
        f"{_BASE}/{process.id}/allocation-revisions/",
        json={"allocated_group_weekly_hours": 124, "reason": "Leadership raised it"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["revision_number"] == 2
    assert body["superseded_at"] is None

    rows = list(
        session.exec(
            select(DepartmentHourAllocationRevision)
            .where(DepartmentHourAllocationRevision.assignment_process_id == process.id)
            .order_by(col(DepartmentHourAllocationRevision.revision_number))
        ).all()
    )
    assert len(rows) == 2
    # Exactly one current (non-superseded) revision remains.
    assert rows[0].superseded_at is not None
    assert rows[1].superseded_at is None


def test_create_revision_with_full_source_metadata(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(
        f"{_BASE}/{process.id}/allocation-revisions/",
        json={
            "allocated_group_weekly_hours": 96.5,
            "reason": "Imported from leadership sheet",
            "source": "file_import",
            "source_reference": "allocation_2026.xlsx",
            "received_at": "2026-07-14T09:00:00Z",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "file_import"
    assert body["source_reference"] == "allocation_2026.xlsx"
    assert body["received_at"] is not None


def test_create_revision_records_audit_event(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    client.post(
        f"{_BASE}/{process.id}/allocation-revisions/",
        json={"allocated_group_weekly_hours": 120, "reason": "Audited reason"},
    )
    resp = client.get(f"{_BASE}/{process.id}/audit-events/")
    assert resp.status_code == 200
    events = resp.json()["data"]
    assert any(
        e["event_type"] == "allocation.revised"
        and e["entity_type"] == "department_hour_allocation_revision"
        and e["reason"] == "Audited reason"
        for e in events
    )


def test_create_revision_superadmin_allowed(
    superuser_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = superuser_client.post(
        f"{_BASE}/{process.id}/allocation-revisions/",
        json={"allocated_group_weekly_hours": 100, "reason": "By superadmin"},
    )
    assert resp.status_code == 201


# ── Create — validation & guards ──────────────────────────────────────────────


def test_create_revision_rejects_zero_hours(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(
        f"{_BASE}/{process.id}/allocation-revisions/",
        json={"allocated_group_weekly_hours": 0, "reason": "Zero"},
    )
    assert resp.status_code == 422


def test_create_revision_rejects_negative_hours(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(
        f"{_BASE}/{process.id}/allocation-revisions/",
        json={"allocated_group_weekly_hours": -5, "reason": "Negative"},
    )
    assert resp.status_code == 422


def test_create_revision_rejects_empty_reason(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(
        f"{_BASE}/{process.id}/allocation-revisions/",
        json={"allocated_group_weekly_hours": 120, "reason": ""},
    )
    assert resp.status_code == 422


def test_create_revision_process_not_found(client: TestClient) -> None:
    resp = client.post(
        f"{_BASE}/{uuid.uuid4()}/allocation-revisions/",
        json={"allocated_group_weekly_hours": 120, "reason": "No process"},
    )
    assert resp.status_code == 404


def test_create_revision_blocked_on_final_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    resp = client.post(
        f"{_BASE}/{process.id}/allocation-revisions/",
        json={"allocated_group_weekly_hours": 120, "reason": "On final"},
    )
    assert resp.status_code == 400


def test_create_revision_forbidden_for_reader(
    reader_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = reader_client.post(
        f"{_BASE}/{process.id}/allocation-revisions/",
        json={"allocated_group_weekly_hours": 120, "reason": "Reader"},
    )
    assert resp.status_code == 403


# ── List ──────────────────────────────────────────────────────────────────────


def test_list_revisions_ordered_oldest_first(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_allocation_revision(
        session,
        process,
        revision_number=1,
        superseded_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
    )
    factories.make_allocation_revision(session, process, revision_number=2)
    resp = client.get(f"{_BASE}/{process.id}/allocation-revisions/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert [r["revision_number"] for r in body["data"]] == [1, 2]


def test_list_revisions_empty(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.get(f"{_BASE}/{process.id}/allocation-revisions/")
    assert resp.status_code == 200
    assert resp.json() == {"data": [], "count": 0}


def test_list_revisions_process_not_found(client: TestClient) -> None:
    resp = client.get(f"{_BASE}/{uuid.uuid4()}/allocation-revisions/")
    assert resp.status_code == 404


# ── Current ───────────────────────────────────────────────────────────────────


def test_get_current_revision(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    client.post(
        f"{_BASE}/{process.id}/allocation-revisions/",
        json={"allocated_group_weekly_hours": 120, "reason": "First"},
    )
    client.post(
        f"{_BASE}/{process.id}/allocation-revisions/",
        json={"allocated_group_weekly_hours": 124, "reason": "Second"},
    )
    resp = client.get(f"{_BASE}/{process.id}/allocation-revisions/current")
    assert resp.status_code == 200
    body = resp.json()
    assert body["revision_number"] == 2
    assert body["allocated_group_weekly_hours"] == 124
    assert body["superseded_at"] is None


def test_get_current_revision_none(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.get(f"{_BASE}/{process.id}/allocation-revisions/current")
    assert resp.status_code == 404


def test_get_current_revision_process_not_found(client: TestClient) -> None:
    resp = client.get(f"{_BASE}/{uuid.uuid4()}/allocation-revisions/current")
    assert resp.status_code == 404
