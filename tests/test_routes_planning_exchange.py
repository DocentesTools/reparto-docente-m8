"""API tests for planning import/export exchange (plan §3.10, §7.8).

Covers the three §7.8 planning-exchange endpoints:

* ``exports/planning-draft`` / ``exports/planning-provisional`` — never blocked
  by an inexact/unbalanced/stale plan, always carrying both balance states and
  the validation report;
* ``exports/planning-final`` — retains blocking validation;
* ``imports/planning`` — validates every reference and decimal string, ingests
  activities as ``IMPORTED`` and never creates an assignment.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.audit_events import AuditEvent
from reparto_service.db_models.teaching_activities import (
    TeachingActivity,
    TeachingActivityGroup,
)
from reparto_service.enums import (
    AssignmentProcessStatus,
    SubjectAllocationCategory,
    TeachingActivitySource,
    TeachingPlanStatus,
)
from tests import factories

_DRAFT = "/reparto/assignment-processes/{}/exports/planning-draft"
_PROVISIONAL = "/reparto/assignment-processes/{}/exports/planning-provisional"
_FINAL = "/reparto/assignment-processes/{}/exports/planning-final"
_IMPORT = "/reparto/assignment-processes/{}/imports/planning"


def _balanced_plan(session: Session):
    """Build a fully balanced, generated, feasible plan (no blocking findings).

    One main subject with a single group cell of 4 group / 4 teacher hours, one
    participant with a 4h target, the materialised main activity, a live
    requirement slot occupied by that participant, feasibility FEASIBLE.
    """
    from reparto_service.enums import FeasibilityStatus, HourRequirementStatus

    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(
        session,
        process,
        status=TeachingPlanStatus.REQUIREMENTS_GENERATED,
        feasibility_status=FeasibilityStatus.FEASIBLE,
    )
    factories.make_allocation_revision(
        session, process, allocated_group_weekly_hours=4.0
    )
    subject = factories.make_subject(
        session, process, allocation_category=SubjectAllocationCategory.MAIN
    )
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(
        session,
        process,
        group,
        subject,
        group_weekly_hours=4.0,
        teacher_weekly_hours_per_position=4.0,
        required_teacher_count=1,
    )
    activity = factories.make_teaching_activity(
        session,
        plan,
        subject,
        allocation_category=SubjectAllocationCategory.MAIN,
        group_weekly_hours_per_group=4.0,
        teacher_weekly_hours_per_position=4.0,
        required_teacher_count=1,
        source=TeachingActivitySource.MAIN_GENERATED,
        source_group_subject_id=cell.id,
        group_subjects=[cell],
    )
    profile = factories.make_teacher_profile(session)
    teacher = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4.0
    )
    requirement = factories.make_hour_requirement(
        session,
        process,
        activity,
        position_index=0,
        required_teacher_hours=4.0,
        status=HourRequirementStatus.ASSIGNED,
    )
    factories.make_assignment(session, process, requirement, teacher)
    return process, plan


# ── draft / provisional export ────────────────────────────────────────────────


def test_export_draft_on_empty_plan_not_blocked(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)

    resp = client.post(_DRAFT.format(process.id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "draft"
    assert body["teaching_plan_id"] == str(plan.id)
    assert body["plan_status"] == TeachingPlanStatus.DRAFT.value
    # An empty plan is not exact and has blocking findings, yet the draft is served.
    assert body["is_exact"] is False
    assert body["is_final_exportable"] is False
    assert body["validations"]["blocking_count"] >= 1
    assert body["activities"] == []
    # Both balance states are reported.
    assert "group" in body["balance"]
    assert "teacher" in body["balance"]


def test_export_provisional_reports_both_balances(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    factories.make_allocation_revision(
        session, process, allocated_group_weekly_hours=10.0
    )
    subject = factories.make_subject(
        session, process, allocation_category=SubjectAllocationCategory.SECONDARY
    )
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(session, process, group, subject)
    factories.make_teaching_activity(
        session,
        plan,
        subject,
        group_weekly_hours_per_group=3.0,
        teacher_weekly_hours_per_position=2.0,
        required_teacher_count=2,
        group_subjects=[cell],
    )

    body = client.post(_PROVISIONAL.format(process.id)).json()
    assert body["mode"] == "provisional"
    assert body["balance"]["group"]["total_group_load"] == "3.00"
    assert body["balance"]["group"]["allocated_group_weekly_hours"] == "10.00"
    assert body["balance"]["teacher"]["total_teacher_load"] == "4.00"
    (exported,) = body["activities"]
    assert exported["group_load"] == "3.00"
    assert exported["teacher_load"] == "4.00"
    assert exported["linked_group_count"] == 1
    assert exported["group_subject_ids"] == [str(cell.id)]


def test_export_draft_orders_activities_by_id(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(session, process, group, subject)
    a1 = factories.make_teaching_activity(session, plan, subject, group_subjects=[cell])
    a2 = factories.make_teaching_activity(session, plan, subject, group_subjects=[])

    body = client.post(_DRAFT.format(process.id)).json()
    ids = [a["id"] for a in body["activities"]]
    assert ids == sorted([str(a1.id), str(a2.id)])


def test_export_excludes_retired_activities(
    client: TestClient, session: Session
) -> None:
    from datetime import datetime, timezone

    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    retired = factories.make_teaching_activity(session, plan, subject)
    retired.retired_at = datetime.now(timezone.utc)
    session.add(retired)
    session.commit()

    body = client.post(_DRAFT.format(process.id)).json()
    assert body["activities"] == []


def test_export_no_plan_404(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(_DRAFT.format(process.id))
    assert resp.status_code == 404


def test_export_missing_process_404(client: TestClient, session: Session) -> None:
    resp = client.post(_DRAFT.format(uuid.uuid4()))
    assert resp.status_code == 404


# ── final export strictness ───────────────────────────────────────────────────


def test_export_final_blocked_when_unbalanced(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    resp = client.post(_FINAL.format(process.id))
    assert resp.status_code == 400
    assert "blocking validation" in resp.json()["detail"]


def test_export_final_allowed_when_ready(client: TestClient, session: Session) -> None:
    process, _ = _balanced_plan(session)
    resp = client.post(_FINAL.format(process.id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "final"
    assert body["is_final_exportable"] is True
    assert body["is_exact"] is True
    assert body["validations"]["blocking_count"] == 0


def test_export_draft_served_even_when_final_would_block(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process, status=TeachingPlanStatus.STALE)
    # Draft succeeds on a stale plan; final is refused.
    assert client.post(_DRAFT.format(process.id)).status_code == 200
    assert client.post(_FINAL.format(process.id)).status_code == 400


# ── import ────────────────────────────────────────────────────────────────────


def test_import_ingests_activity_as_imported(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(session, process, group, subject)

    payload = {
        "activities": [
            {
                "subject_id": str(subject.id),
                "group_weekly_hours_per_group": "2.50",
                "teacher_weekly_hours_per_position": "3.00",
                "required_teacher_count": 2,
                "group_subject_ids": [str(cell.id)],
            }
        ]
    }
    resp = client.post(_IMPORT.format(process.id), json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported_count"] == 1
    (created_id,) = body["imported_activity_ids"]

    activity = session.get(TeachingActivity, uuid.UUID(created_id))
    assert activity is not None
    assert activity.source == TeachingActivitySource.IMPORTED
    assert activity.teaching_plan_id == plan.id
    assert activity.group_weekly_hours_per_group == 2.5
    assert activity.teacher_weekly_hours_per_position == 3.0
    assert activity.required_teacher_count == 2
    links = session.exec(
        select(TeachingActivityGroup).where(
            TeachingActivityGroup.teaching_activity_id == activity.id
        )
    ).all()
    assert [link.group_subject_id for link in links] == [cell.id]
    # Post-import balance/validations are reported without blocking the import.
    assert body["balance"]["teacher"]["total_teacher_load"] == "6.00"


def test_import_never_creates_assignments(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process, allows_zero_groups=True)

    payload = {
        "activities": [
            {
                "subject_id": str(subject.id),
                "group_weekly_hours_per_group": "1.00",
                "teacher_weekly_hours_per_position": "1.00",
                "required_teacher_count": 1,
                "group_subject_ids": [],
            }
        ]
    }
    assert client.post(_IMPORT.format(process.id), json=payload).status_code == 200
    assert session.exec(select(Assignment)).all() == []


def test_import_records_audit_event(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process, allows_zero_groups=True)

    payload = {
        "activities": [
            {
                "subject_id": str(subject.id),
                "group_weekly_hours_per_group": "1.00",
                "teacher_weekly_hours_per_position": "1.00",
                "group_subject_ids": [],
            }
        ]
    }
    client.post(_IMPORT.format(process.id), json=payload)
    events = session.exec(
        select(AuditEvent).where(AuditEvent.event_type == "teaching_activity.imported")
    ).all()
    assert len(events) == 1
    assert events[0].entity_type == "teaching_activity"


def test_import_empty_is_noop(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    body = client.post(_IMPORT.format(process.id), json={"activities": []}).json()
    assert body["imported_count"] == 0
    assert body["imported_activity_ids"] == []


def test_import_rejects_float_hours(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process, allows_zero_groups=True)
    payload = {
        "activities": [
            {
                "subject_id": str(subject.id),
                "group_weekly_hours_per_group": 2.5,
                "teacher_weekly_hours_per_position": "1.00",
                "group_subject_ids": [],
            }
        ]
    }
    assert client.post(_IMPORT.format(process.id), json=payload).status_code == 422


def test_import_rejects_three_place_decimal(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process, allows_zero_groups=True)
    payload = {
        "activities": [
            {
                "subject_id": str(subject.id),
                "group_weekly_hours_per_group": "2.505",
                "teacher_weekly_hours_per_position": "1.00",
                "group_subject_ids": [],
            }
        ]
    }
    assert client.post(_IMPORT.format(process.id), json=payload).status_code == 422


def test_import_unknown_subject_404(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    payload = {
        "activities": [
            {
                "subject_id": str(uuid.uuid4()),
                "group_weekly_hours_per_group": "1.00",
                "teacher_weekly_hours_per_position": "1.00",
                "group_subject_ids": [],
            }
        ]
    }
    assert client.post(_IMPORT.format(process.id), json=payload).status_code == 404


def test_import_unknown_cell_404(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    payload = {
        "activities": [
            {
                "subject_id": str(subject.id),
                "group_weekly_hours_per_group": "1.00",
                "teacher_weekly_hours_per_position": "1.00",
                "group_subject_ids": [str(uuid.uuid4())],
            }
        ]
    }
    assert client.post(_IMPORT.format(process.id), json=payload).status_code == 404


def test_import_no_plan_400(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process, allows_zero_groups=True)
    payload = {
        "activities": [
            {
                "subject_id": str(subject.id),
                "group_weekly_hours_per_group": "1.00",
                "teacher_weekly_hours_per_position": "1.00",
                "group_subject_ids": [],
            }
        ]
    }
    assert client.post(_IMPORT.format(process.id), json=payload).status_code == 400


def test_import_locked_plan_400(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process, status=TeachingPlanStatus.LOCKED)
    subject = factories.make_subject(session, process, allows_zero_groups=True)
    payload = {
        "activities": [
            {
                "subject_id": str(subject.id),
                "group_weekly_hours_per_group": "1.00",
                "teacher_weekly_hours_per_position": "1.00",
                "group_subject_ids": [],
            }
        ]
    }
    assert client.post(_IMPORT.format(process.id), json=payload).status_code == 400


def test_import_final_process_400(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process, allows_zero_groups=True)
    payload = {
        "activities": [
            {
                "subject_id": str(subject.id),
                "group_weekly_hours_per_group": "1.00",
                "teacher_weekly_hours_per_position": "1.00",
                "group_subject_ids": [],
            }
        ]
    }
    assert client.post(_IMPORT.format(process.id), json=payload).status_code == 400


def test_import_forbidden_for_reader(
    reader_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    assert (
        reader_client.post(
            _IMPORT.format(process.id), json={"activities": []}
        ).status_code
        == 403
    )
