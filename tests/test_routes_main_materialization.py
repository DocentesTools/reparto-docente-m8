"""API tests for main-activity materialisation (plan §7.3, §19, §20.10).

Covers the ``POST .../teaching-plan/materialize-main`` endpoint: deterministic
one-activity-per-main-cell generation, idempotency (no duplication on re-run),
the active/MAIN/retired selection rules, hour resolution (override → default →
0), plan/process mutability gates and the writer permission.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from reparto_service.db_models.audit_events import AuditEvent
from reparto_service.db_models.teaching_activities import (
    TeachingActivity,
    TeachingActivityGroup,
)
from reparto_service.enums import (
    AssignmentProcessStatus,
    SubjectAllocationCategory,
    TeachingActivitySource,
    TeachingActivitySyncState,
    TeachingPlanStatus,
)
from tests import factories

_URL = "/reparto/assignment-processes/{}/teaching-plan/materialize-main"


def _url(process_id) -> str:
    return _URL.format(process_id)


# ── happy path ────────────────────────────────────────────────────────────────


def test_materialize_creates_activity_per_main_cell(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(
        session, process, allocation_category=SubjectAllocationCategory.MAIN
    )
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(
        session,
        process,
        group,
        subject,
        group_weekly_hours=3.0,
        teacher_weekly_hours_per_position=4.0,
        required_teacher_count=2,
    )

    resp = client.post(_url(process.id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["created_count"] == 1
    assert body["skipped_count"] == 0
    assert body["skipped_source_ids"] == []
    (activity,) = body["created"]
    assert activity["source"] == TeachingActivitySource.MAIN_GENERATED.value
    assert activity["allocation_category"] == SubjectAllocationCategory.MAIN.value
    assert activity["source_group_subject_id"] == str(cell.id)
    assert activity["teaching_plan_id"] == str(plan.id)
    assert activity["group_weekly_hours_per_group"] == 3.0
    assert activity["teacher_weekly_hours_per_position"] == 4.0
    assert activity["required_teacher_count"] == 2
    assert activity["group_subject_ids"] == [str(cell.id)]
    assert activity["linked_group_count"] == 1
    assert activity["sync_state"] == TeachingActivitySyncState.IN_SYNC.value


def test_materialize_inherits_subject_defaults(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    subject = factories.make_subject(
        session,
        process,
        allocation_category=SubjectAllocationCategory.MAIN,
        default_group_weekly_hours=5.0,
        default_teacher_weekly_hours_per_position=6.0,
    )
    group = factories.make_teaching_group(session, process)
    factories.make_group_subject(session, process, group, subject)  # NULL overrides

    body = client.post(_url(process.id)).json()
    (activity,) = body["created"]
    assert activity["group_weekly_hours_per_group"] == 5.0
    assert activity["teacher_weekly_hours_per_position"] == 6.0


def test_materialize_defaults_to_zero_when_unset(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    subject = factories.make_subject(
        session, process, allocation_category=SubjectAllocationCategory.MAIN
    )
    group = factories.make_teaching_group(session, process)
    factories.make_group_subject(session, process, group, subject)

    (activity,) = client.post(_url(process.id)).json()["created"]
    assert activity["group_weekly_hours_per_group"] == 0.0
    assert activity["teacher_weekly_hours_per_position"] == 0.0


def test_materialize_multiple_cells_ordered_by_id(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    subject = factories.make_subject(
        session, process, allocation_category=SubjectAllocationCategory.MAIN
    )
    group_a = factories.make_teaching_group(session, process, group_code="A")
    group_b = factories.make_teaching_group(session, process, group_code="B")
    cell_a = factories.make_group_subject(session, process, group_a, subject)
    cell_b = factories.make_group_subject(session, process, group_b, subject)

    body = client.post(_url(process.id)).json()
    assert body["created_count"] == 2
    got = {a["source_group_subject_id"] for a in body["created"]}
    assert got == {str(cell_a.id), str(cell_b.id)}
    expected = sorted([str(cell_a.id), str(cell_b.id)])
    assert [a["source_group_subject_id"] for a in body["created"]] == expected


# ── selection rules ───────────────────────────────────────────────────────────


def test_materialize_ignores_secondary_and_inactive(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    main = factories.make_subject(
        session,
        process,
        name="Maths",
        allocation_category=SubjectAllocationCategory.MAIN,
    )
    secondary = factories.make_subject(
        session,
        process,
        name="Support",
        allocation_category=SubjectAllocationCategory.SECONDARY,
    )
    group = factories.make_teaching_group(session, process)
    active_main = factories.make_group_subject(session, process, group, main)
    factories.make_group_subject(session, process, group, secondary)  # secondary
    inactive_group = factories.make_teaching_group(session, process, group_code="B")
    factories.make_group_subject(
        session, process, inactive_group, main, active=False
    )  # inactive

    body = client.post(_url(process.id)).json()
    assert body["created_count"] == 1
    (activity,) = body["created"]
    assert activity["source_group_subject_id"] == str(active_main.id)


# ── idempotency ───────────────────────────────────────────────────────────────


def test_materialize_is_idempotent(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    subject = factories.make_subject(
        session, process, allocation_category=SubjectAllocationCategory.MAIN
    )
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(session, process, group, subject)

    first = client.post(_url(process.id)).json()
    assert first["created_count"] == 1

    second = client.post(_url(process.id)).json()
    assert second["created_count"] == 0
    assert second["skipped_count"] == 1
    assert second["skipped_source_ids"] == [str(cell.id)]

    # Only one activity exists in the database (no duplication).
    activities = session.exec(
        select(TeachingActivity).where(
            TeachingActivity.source == TeachingActivitySource.MAIN_GENERATED
        )
    ).all()
    assert len(activities) == 1


def test_materialize_skips_manually_present_main_activity(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(
        session, process, allocation_category=SubjectAllocationCategory.MAIN
    )
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(session, process, group, subject)
    factories.make_teaching_activity(
        session,
        plan,
        subject,
        allocation_category=SubjectAllocationCategory.MAIN,
        source=TeachingActivitySource.MAIN_GENERATED,
        source_group_subject_id=cell.id,
    )

    body = client.post(_url(process.id)).json()
    assert body["created_count"] == 0
    assert body["skipped_source_ids"] == [str(cell.id)]


def test_materialize_regenerates_after_retirement(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(
        session, process, allocation_category=SubjectAllocationCategory.MAIN
    )
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(session, process, group, subject)
    retired = factories.make_teaching_activity(
        session,
        plan,
        subject,
        allocation_category=SubjectAllocationCategory.MAIN,
        source=TeachingActivitySource.MAIN_GENERATED,
        source_group_subject_id=cell.id,
    )
    retired.retired_at = datetime.now(timezone.utc)
    session.add(retired)
    session.commit()

    body = client.post(_url(process.id)).json()
    assert body["created_count"] == 1
    (activity,) = body["created"]
    assert activity["source_group_subject_id"] == str(cell.id)


def test_materialize_no_main_cells(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    body = client.post(_url(process.id)).json()
    assert body == {
        "created": [],
        "created_count": 0,
        "skipped_source_ids": [],
        "skipped_count": 0,
    }


# ── audit ─────────────────────────────────────────────────────────────────────


def test_materialize_records_audit_event(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    subject = factories.make_subject(
        session, process, allocation_category=SubjectAllocationCategory.MAIN
    )
    group = factories.make_teaching_group(session, process)
    factories.make_group_subject(session, process, group, subject)

    client.post(_url(process.id))
    events = session.exec(
        select(AuditEvent).where(
            AuditEvent.event_type == "teaching_activity.materialized"
        )
    ).all()
    assert len(events) == 1
    assert events[0].entity_type == "teaching_activity"

    # A no-op re-run records no further events.
    client.post(_url(process.id))
    events = session.exec(
        select(AuditEvent).where(
            AuditEvent.event_type == "teaching_activity.materialized"
        )
    ).all()
    assert len(events) == 1


def test_materialize_creates_single_link_row(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    subject = factories.make_subject(
        session, process, allocation_category=SubjectAllocationCategory.MAIN
    )
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(session, process, group, subject)

    client.post(_url(process.id))
    links = session.exec(select(TeachingActivityGroup)).all()
    assert len(links) == 1
    assert links[0].group_subject_id == cell.id


# ── gates ─────────────────────────────────────────────────────────────────────


def test_materialize_no_plan_400(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(_url(process.id))
    assert resp.status_code == 400


def test_materialize_locked_plan_400(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process, status=TeachingPlanStatus.LOCKED)
    resp = client.post(_url(process.id))
    assert resp.status_code == 400


def test_materialize_final_process_400(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    factories.make_teaching_plan(session, process)
    resp = client.post(_url(process.id))
    assert resp.status_code == 400


def test_materialize_missing_process_404(client: TestClient, session: Session) -> None:
    import uuid

    resp = client.post(_url(uuid.uuid4()))
    assert resp.status_code == 404


def test_materialize_forbidden_for_reader(
    reader_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    resp = reader_client.post(_url(process.id))
    assert resp.status_code == 403
