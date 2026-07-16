"""API tests for requirement reconciliation preview and apply (plan §7.5, §9).

Covers ``POST .../requirements/reconciliation-preview`` and
``.../requirements/reconcile``: the explicit, reasoned resolution of the
assigned-slot conflicts a plain generation refuses. Reconciliation releases the
active assignment (soft-cancel, audited — never a silent delete), retires the old
slot, and for a value change creates a fresh replacement slot linked via
``superseded_by_requirement_id`` (plan §20.8), then advances the plan back to
``REQUIREMENTS_GENERATED`` at a new generation number.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.audit_events import AuditEvent
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    AssignmentProcessStatus,
    AssignmentStatus,
    HourRequirementStatus,
    SubjectAllocationCategory,
    TeachingPlanStatus,
)
from tests import factories

_PREVIEW = "/reparto/assignment-processes/{}/requirements/reconciliation-preview"
_RECONCILE = "/reparto/assignment-processes/{}/requirements/reconcile"
_GENERATE = "/reparto/assignment-processes/{}/requirements/generate"


def _preview_url(process_id) -> str:
    return _PREVIEW.format(process_id)


def _reconcile_url(process_id) -> str:
    return _RECONCILE.format(process_id)


def _generate_url(process_id) -> str:
    return _GENERATE.format(process_id)


def _setup(
    session: Session,
    *,
    process_status: AssignmentProcessStatus = AssignmentProcessStatus.DRAFT,
    required_teacher_count: int = 1,
    teacher_hours: float = 2.0,
):
    """Locked plan with one activity, ready for the generate → assign flow."""
    process = factories.make_assignment_process(session, status=process_status)
    plan = factories.make_teaching_plan(
        session, process, status=TeachingPlanStatus.LOCKED
    )
    subject = factories.make_subject(
        session, process, allocation_category=SubjectAllocationCategory.MAIN
    )
    activity = factories.make_teaching_activity(
        session,
        plan,
        subject,
        teacher_weekly_hours_per_position=teacher_hours,
        required_teacher_count=required_teacher_count,
    )
    return process, plan, subject, activity


def _live_requirements(session: Session, process) -> list[HourRequirement]:
    return list(
        session.exec(
            select(HourRequirement)
            .where(HourRequirement.assignment_process_id == process.id)
            .where(HourRequirement.retired_generation == None)  # noqa: E711
            .order_by(
                col(HourRequirement.teaching_activity_id),
                col(HourRequirement.position_index),
            )
        ).all()
    )


def _slot_at(session: Session, process, position: int) -> HourRequirement:
    return session.exec(
        select(HourRequirement)
        .where(HourRequirement.assignment_process_id == process.id)
        .where(HourRequirement.position_index == position)
        .where(HourRequirement.retired_generation == None)  # noqa: E711
    ).one()


def _assign(session: Session, process, slot: HourRequirement) -> Assignment:
    profile = factories.make_teacher_profile(session)
    teacher = factories.make_process_teacher(session, process, profile)
    return factories.make_assignment(session, process, slot, teacher)


def _set_plan_status(
    session: Session, plan: TeachingPlan, status: TeachingPlanStatus
) -> None:
    plan.status = status
    session.add(plan)
    session.commit()


def _stage_value_conflict(
    client: TestClient,
    session: Session,
    *,
    new_hours: float = 5.0,
    plan_status: TeachingPlanStatus = TeachingPlanStatus.STALE,
):
    """Generate one slot, assign it, then change the activity hours (a conflict)."""
    process, plan, _subject, activity = _setup(
        session, required_teacher_count=1, teacher_hours=2.0
    )
    client.post(_generate_url(process.id))
    slot = _slot_at(session, process, 0)
    assignment = _assign(session, process, slot)
    activity.teacher_weekly_hours_per_position = new_hours
    session.add(activity)
    session.commit()
    _set_plan_status(session, plan, plan_status)
    return process, plan, activity, slot, assignment


def _stage_removed_conflict(client: TestClient, session: Session):
    """Generate two slots, assign position 1, then drop it to one position."""
    process, plan, _subject, activity = _setup(
        session, required_teacher_count=2, teacher_hours=2.0
    )
    client.post(_generate_url(process.id))
    slot1 = _slot_at(session, process, 1)
    assignment = _assign(session, process, slot1)
    activity.required_teacher_count = 1
    session.add(activity)
    session.commit()
    _set_plan_status(session, plan, TeachingPlanStatus.STALE)
    return process, plan, activity, slot1, assignment


# ── preview ───────────────────────────────────────────────────────────────────


def test_preview_missing_process_404(client: TestClient, session: Session) -> None:
    resp = client.post(_preview_url(uuid.uuid4()))
    assert resp.status_code == 404


def test_preview_no_plan_400(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(_preview_url(process.id))
    assert resp.status_code == 400


def test_preview_plan_not_reconcilable_400(
    client: TestClient, session: Session
) -> None:
    # A LOCKED plan generates; it is not a reconciliation target.
    process, _plan, _subject, _activity = _setup(session)
    resp = client.post(_preview_url(process.id))
    assert resp.status_code == 400


def test_preview_forbidden_for_reader(
    reader_client: TestClient, session: Session
) -> None:
    process, plan, _subject, _activity = _setup(session)
    _set_plan_status(session, plan, TeachingPlanStatus.STALE)
    resp = reader_client.post(_preview_url(process.id))
    assert resp.status_code == 403


def test_preview_reports_value_conflict(client: TestClient, session: Session) -> None:
    process, _plan, activity, slot, assignment = _stage_value_conflict(client, session)
    body = client.post(_preview_url(process.id)).json()

    assert body["next_generation_number"] == 2
    assert body["conflict_count"] == 1
    assert body["requires_reconciliation"] is True
    assert body["is_noop"] is False
    (conflict,) = body["conflicts"]
    assert conflict["requirement_id"] == str(slot.id)
    assert conflict["teaching_activity_id"] == str(activity.id)
    assert conflict["position_index"] == 0
    assert conflict["resolution"] == "value_changed"
    assert conflict["current_required_teacher_hours"] == 2.0
    assert conflict["new_required_teacher_hours"] == 5.0
    assert conflict["assignment_id"] == str(assignment.id)
    assert conflict["process_teacher_id"] == str(assignment.process_teacher_id)
    assert conflict["superseded_by_requirement_id"] is None


def test_preview_reports_removed_conflict(client: TestClient, session: Session) -> None:
    process, _plan, _activity, slot1, _assignment = _stage_removed_conflict(
        client, session
    )
    body = client.post(_preview_url(process.id)).json()
    assert body["conflict_count"] == 1
    assert body["preserve_count"] == 1
    (conflict,) = body["conflicts"]
    assert conflict["requirement_id"] == str(slot1.id)
    assert conflict["resolution"] == "removed"
    assert conflict["new_required_teacher_hours"] is None


def test_preview_noop_no_conflicts(client: TestClient, session: Session) -> None:
    # A stale plan whose live slots still match the activities: nothing to do.
    process, plan, _subject, _activity = _setup(
        session, required_teacher_count=1, teacher_hours=2.0
    )
    client.post(_generate_url(process.id))
    _set_plan_status(session, plan, TeachingPlanStatus.STALE)
    body = client.post(_preview_url(process.id)).json()
    assert body["conflict_count"] == 0
    assert body["requires_reconciliation"] is False
    assert body["is_noop"] is True
    assert body["preserve_count"] == 1


def test_preview_does_not_mutate(client: TestClient, session: Session) -> None:
    process, _plan, _activity, slot, assignment = _stage_value_conflict(client, session)
    client.post(_preview_url(process.id))
    session.refresh(slot)
    session.refresh(assignment)
    assert slot.retired_generation is None
    assert slot.required_teacher_hours == 2.0
    assert assignment.status == AssignmentStatus.ACTIVE


# ── reconcile: gates ───────────────────────────────────────────────────────────


def _body(count: int, reason: str = "Leadership cut two hours."):
    return {"reason": reason, "expected_conflict_count": count}


def test_reconcile_missing_process_404(client: TestClient, session: Session) -> None:
    resp = client.post(_reconcile_url(uuid.uuid4()), json=_body(0))
    assert resp.status_code == 404


def test_reconcile_no_plan_400(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(_reconcile_url(process.id), json=_body(0))
    assert resp.status_code == 400


def test_reconcile_plan_not_reconcilable_400(
    client: TestClient, session: Session
) -> None:
    process, _plan, _subject, _activity = _setup(session)  # LOCKED
    resp = client.post(_reconcile_url(process.id), json=_body(0))
    assert resp.status_code == 400


def test_reconcile_final_process_400(client: TestClient, session: Session) -> None:
    process, plan, _subject, _activity = _setup(session)
    _set_plan_status(session, plan, TeachingPlanStatus.STALE)
    process.status = AssignmentProcessStatus.FINAL
    session.add(process)
    session.commit()
    resp = client.post(_reconcile_url(process.id), json=_body(0))
    assert resp.status_code == 400


def test_reconcile_forbidden_for_reader(
    reader_client: TestClient, session: Session
) -> None:
    process, plan, _subject, _activity = _setup(session)
    _set_plan_status(session, plan, TeachingPlanStatus.STALE)
    resp = reader_client.post(_reconcile_url(process.id), json=_body(0))
    assert resp.status_code == 403


def test_reconcile_reason_required_422(client: TestClient, session: Session) -> None:
    process, plan, _subject, _activity = _setup(session)
    _set_plan_status(session, plan, TeachingPlanStatus.STALE)
    resp = client.post(
        _reconcile_url(process.id),
        json={"reason": "", "expected_conflict_count": 0},
    )
    assert resp.status_code == 422


def test_reconcile_conflict_count_mismatch_409(
    client: TestClient, session: Session
) -> None:
    process, _plan, _activity, slot, assignment = _stage_value_conflict(client, session)
    resp = client.post(_reconcile_url(process.id), json=_body(0))
    assert resp.status_code == 409
    # Nothing changed: the slot and assignment survive untouched.
    session.refresh(slot)
    session.refresh(assignment)
    assert slot.retired_generation is None
    assert assignment.status == AssignmentStatus.ACTIVE


# ── reconcile: resolution paths ────────────────────────────────────────────────


def test_reconcile_value_change_supersedes_and_releases(
    client: TestClient, session: Session
) -> None:
    process, plan, _activity, slot, assignment = _stage_value_conflict(
        client, session, new_hours=5.0
    )
    old_id = slot.id
    body = client.post(_reconcile_url(process.id), json=_body(1)).json()

    assert body["generation_number"] == 2
    assert body["resolved_count"] == 1
    assert body["created_count"] == 1
    assert body["preserved_count"] == 0
    assert body["retired_count"] == 0
    assert body["released_assignment_ids"] == [str(assignment.id)]

    (resolved,) = body["resolved"]
    assert resolved["requirement_id"] == str(old_id)
    assert resolved["resolution"] == "value_changed"
    new_id = resolved["superseded_by_requirement_id"]
    assert new_id is not None

    # Old slot retired + superseded; the assignment is cancelled, not deleted.
    session.refresh(slot)
    assert slot.retired_generation == 2
    assert slot.status == HourRequirementStatus.STALE
    assert str(slot.superseded_by_requirement_id) == new_id
    session.refresh(assignment)
    assert assignment.status == AssignmentStatus.CANCELLED

    # A fresh AVAILABLE slot carries the new hours at the same position.
    live = _live_requirements(session, process)
    assert [r.id for r in live] == [uuid.UUID(new_id)]
    assert live[0].required_teacher_hours == 5.0
    assert live[0].created_generation == 2
    assert live[0].status == HourRequirementStatus.AVAILABLE

    session.refresh(plan)
    assert plan.status == TeachingPlanStatus.REQUIREMENTS_GENERATED
    assert plan.current_generation_number == 2
    assert plan.requirements_generated_at is not None


def test_reconcile_removed_position_no_replacement(
    client: TestClient, session: Session
) -> None:
    process, plan, _activity, slot1, assignment = _stage_removed_conflict(
        client, session
    )
    body = client.post(_reconcile_url(process.id), json=_body(1)).json()

    assert body["resolved_count"] == 1
    assert body["created_count"] == 0
    assert body["preserved_count"] == 1
    (resolved,) = body["resolved"]
    assert resolved["resolution"] == "removed"
    assert resolved["superseded_by_requirement_id"] is None

    session.refresh(slot1)
    assert slot1.retired_generation == 2
    assert slot1.superseded_by_requirement_id is None
    session.refresh(assignment)
    assert assignment.status == AssignmentStatus.CANCELLED

    # Only the surviving position-0 slot remains live.
    live = _live_requirements(session, process)
    assert [r.position_index for r in live] == [0]
    session.refresh(plan)
    assert plan.status == TeachingPlanStatus.REQUIREMENTS_GENERATED


def test_reconcile_from_reconciliation_required(
    client: TestClient, session: Session
) -> None:
    process, plan, _activity, _slot, _assignment = _stage_value_conflict(
        client, session, plan_status=TeachingPlanStatus.RECONCILIATION_REQUIRED
    )
    resp = client.post(_reconcile_url(process.id), json=_body(1))
    assert resp.status_code == 200
    session.refresh(plan)
    assert plan.status == TeachingPlanStatus.REQUIREMENTS_GENERATED


def test_reconcile_noop_from_stale_regenerates(
    client: TestClient, session: Session
) -> None:
    # No conflicts pending: reconcile still advances the generation and status.
    process, plan, _subject, _activity = _setup(
        session, required_teacher_count=1, teacher_hours=2.0
    )
    client.post(_generate_url(process.id))
    _set_plan_status(session, plan, TeachingPlanStatus.STALE)
    body = client.post(_reconcile_url(process.id), json=_body(0)).json()
    assert body["resolved_count"] == 0
    assert body["created_count"] == 0
    assert body["preserved_count"] == 1
    assert body["generation_number"] == 2
    session.refresh(plan)
    assert plan.status == TeachingPlanStatus.REQUIREMENTS_GENERATED


def test_reconcile_mixed_conflict_retire_and_create(
    client: TestClient, session: Session
) -> None:
    # One activity's hours change (pos0 assigned -> conflict, pos1 unassigned ->
    # retire+recreate) and a brand-new activity adds a fresh slot to create.
    process, plan, subject, activity_a = _setup(
        session, required_teacher_count=2, teacher_hours=2.0
    )
    client.post(_generate_url(process.id))
    slot0 = _slot_at(session, process, 0)
    assignment = _assign(session, process, slot0)

    activity_a.teacher_weekly_hours_per_position = 3.0
    session.add(activity_a)
    activity_b = factories.make_teaching_activity(
        session,
        plan,
        subject,
        teacher_weekly_hours_per_position=4.0,
        required_teacher_count=1,
    )
    session.commit()
    _set_plan_status(session, plan, TeachingPlanStatus.STALE)

    body = client.post(_reconcile_url(process.id), json=_body(1)).json()
    assert body["resolved_count"] == 1
    assert body["retired_count"] == 1  # activity_a pos1 (unassigned)
    assert body["created_count"] == 3  # a-pos1 recreate, b-pos0, a-pos0 replacement
    assert body["released_assignment_ids"] == [str(assignment.id)]

    live = _live_requirements(session, process)
    by_activity: dict[str, list[float]] = {}
    for r in live:
        by_activity.setdefault(str(r.teaching_activity_id), []).append(
            r.required_teacher_hours
        )
    assert sorted(by_activity[str(activity_a.id)]) == [3.0, 3.0]
    assert by_activity[str(activity_b.id)] == [4.0]
    session.refresh(assignment)
    assert assignment.status == AssignmentStatus.CANCELLED


def test_reconcile_records_audit_events(client: TestClient, session: Session) -> None:
    process, _plan, _activity, _slot, assignment = _stage_value_conflict(
        client, session
    )
    client.post(_reconcile_url(process.id), json=_body(1))

    reconciled = session.exec(
        select(AuditEvent).where(AuditEvent.event_type == "requirements.reconciled")
    ).all()
    assert len(reconciled) == 1
    assert reconciled[0].entity_type == "teaching_plan"
    assert reconciled[0].reason == "Leadership cut two hours."

    cancelled = session.exec(
        select(AuditEvent)
        .where(AuditEvent.event_type == "assignment.cancelled")
        .where(AuditEvent.entity_id == assignment.id)
    ).all()
    assert len(cancelled) == 1
    assert cancelled[0].reason == "Leadership cut two hours."
