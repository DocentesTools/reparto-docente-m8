"""API tests for requirement generation preview and apply (plan §7.5, §20.8).

Covers ``POST .../requirements/generation-preview`` and ``.../requirements/generate``:
one slot per teacher position, stable generation numbering, deterministic output,
the §20.8 identity model (preserve unchanged, create new, retire removed
unassigned, retire+recreate value-changed unassigned) and the assignment-safety
guard (a change to an *assigned* slot forces reconciliation and blocks generate,
never silently dropping an assignment).
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
    HourRequirementStatus,
    SubjectAllocationCategory,
    TeachingPlanStatus,
)
from tests import factories

_PREVIEW = "/reparto/assignment-processes/{}/requirements/generation-preview"
_GENERATE = "/reparto/assignment-processes/{}/requirements/generate"


def _preview_url(process_id) -> str:
    return _PREVIEW.format(process_id)


def _generate_url(process_id) -> str:
    return _GENERATE.format(process_id)


def _setup(
    session: Session,
    *,
    process_status: AssignmentProcessStatus = AssignmentProcessStatus.DRAFT,
    plan_status: TeachingPlanStatus = TeachingPlanStatus.LOCKED,
    required_teacher_count: int = 2,
    teacher_hours: float = 2.0,
):
    """Locked plan with one co-teaching activity ready to generate slots."""
    process = factories.make_assignment_process(session, status=process_status)
    plan = factories.make_teaching_plan(session, process, status=plan_status)
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
            .order_by(col(HourRequirement.position_index))
        ).all()
    )


# ── preview ───────────────────────────────────────────────────────────────────


def test_preview_missing_process_404(client: TestClient, session: Session) -> None:
    resp = client.post(_preview_url(uuid.uuid4()))
    assert resp.status_code == 404


def test_preview_no_plan_400(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(_preview_url(process.id))
    assert resp.status_code == 400


def test_preview_plan_not_generatable_400(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process, status=TeachingPlanStatus.DRAFT)
    resp = client.post(_preview_url(process.id))
    assert resp.status_code == 400


def test_preview_fresh_generation(client: TestClient, session: Session) -> None:
    process, _plan, _subject, activity = _setup(session)
    body = client.post(_preview_url(process.id)).json()
    assert body["next_generation_number"] == 1
    assert body["create_count"] == 2
    assert body["preserve_count"] == 0
    assert body["retire_count"] == 0
    assert body["conflict_count"] == 0
    assert body["requires_reconciliation"] is False
    assert body["is_noop"] is False
    positions = [slot["position_index"] for slot in body["to_create"]]
    assert positions == [0, 1]
    assert all(
        slot["teaching_activity_id"] == str(activity.id) for slot in body["to_create"]
    )
    assert all(slot["required_teacher_hours"] == 2.0 for slot in body["to_create"])


def test_preview_does_not_mutate(client: TestClient, session: Session) -> None:
    process, _plan, _subject, _activity = _setup(session)
    client.post(_preview_url(process.id))
    assert _live_requirements(session, process) == []


def test_preview_forbidden_for_reader(
    reader_client: TestClient, session: Session
) -> None:
    process, _plan, _subject, _activity = _setup(session)
    resp = reader_client.post(_preview_url(process.id))
    assert resp.status_code == 403


# ── generate: happy paths ──────────────────────────────────────────────────────


def test_generate_one_slot_per_position(client: TestClient, session: Session) -> None:
    process, plan, _subject, activity = _setup(
        session, required_teacher_count=2, teacher_hours=2.0
    )
    body = client.post(_generate_url(process.id)).json()

    assert body["generation_number"] == 1
    assert body["created_count"] == 2
    assert body["preserved_count"] == 0
    assert body["retired_count"] == 0
    assert body["count"] == 2
    assert [s["position_index"] for s in body["data"]] == [0, 1]
    assert all(s["required_teacher_hours"] == 2.0 for s in body["data"])
    assert all(s["created_generation"] == 1 for s in body["data"])
    assert all(s["last_validated_generation"] == 1 for s in body["data"])
    assert all(
        s["status"] == HourRequirementStatus.AVAILABLE.value for s in body["data"]
    )
    assert all(s["teaching_activity_id"] == str(activity.id) for s in body["data"])

    session.refresh(plan)
    assert plan.status == TeachingPlanStatus.REQUIREMENTS_GENERATED
    assert plan.current_generation_number == 1
    assert plan.requirements_generated_at is not None


def test_generate_tutoring_slot_hours(client: TestClient, session: Session) -> None:
    # One tutoring position: 1 group hour, 2 teacher hours -> one 2.00h slot.
    process, _plan, _subject, _activity = _setup(
        session, required_teacher_count=1, teacher_hours=2.0
    )
    body = client.post(_generate_url(process.id)).json()
    assert body["created_count"] == 1
    assert body["data"][0]["required_teacher_hours"] == 2.0


def test_generate_multiple_activities_deterministic(
    client: TestClient, session: Session
) -> None:
    process, plan, subject, activity_a = _setup(
        session, required_teacher_count=1, teacher_hours=3.0
    )
    activity_b = factories.make_teaching_activity(
        session,
        plan,
        subject,
        teacher_weekly_hours_per_position=4.0,
        required_teacher_count=2,
    )
    body = client.post(_generate_url(process.id)).json()
    assert body["created_count"] == 3
    by_activity: dict[str, list[int]] = {}
    for slot in body["data"]:
        by_activity.setdefault(slot["teaching_activity_id"], []).append(
            slot["position_index"]
        )
    assert sorted(by_activity[str(activity_a.id)]) == [0]
    assert sorted(by_activity[str(activity_b.id)]) == [0, 1]

    # Ordering in ``data`` is by (activity id, position) — deterministic.
    expected = sorted(
        [(s["teaching_activity_id"], s["position_index"]) for s in body["data"]]
    )
    got = [(s["teaching_activity_id"], s["position_index"]) for s in body["data"]]
    assert got == expected


def test_generate_zero_activities(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(
        session, process, status=TeachingPlanStatus.LOCKED
    )
    body = client.post(_generate_url(process.id)).json()
    assert body["created_count"] == 0
    assert body["count"] == 0
    session.refresh(plan)
    assert plan.status == TeachingPlanStatus.REQUIREMENTS_GENERATED


def test_generate_records_audit_event(client: TestClient, session: Session) -> None:
    process, _plan, _subject, _activity = _setup(session)
    client.post(_generate_url(process.id))
    events = session.exec(
        select(AuditEvent).where(AuditEvent.event_type == "requirements.generated")
    ).all()
    assert len(events) == 1
    assert events[0].entity_type == "teaching_plan"


# ── generate: regeneration diff (plan §20.8) ───────────────────────────────────


def _mark_stale(session: Session, plan: TeachingPlan) -> None:
    plan.status = TeachingPlanStatus.STALE
    session.add(plan)
    session.commit()


def test_generate_preserves_unchanged_slot_and_assignment(
    client: TestClient, session: Session
) -> None:
    process, plan, _subject, _activity = _setup(
        session, required_teacher_count=1, teacher_hours=2.0
    )
    client.post(_generate_url(process.id))
    slot = _live_requirements(session, process)[0]
    slot_id = slot.id

    profile = factories.make_teacher_profile(session)
    teacher = factories.make_process_teacher(session, process, profile)
    factories.make_assignment(session, process, slot, teacher)

    _mark_stale(session, plan)
    body = client.post(_generate_url(process.id)).json()

    assert body["generation_number"] == 2
    assert body["created_count"] == 0
    assert body["preserved_count"] == 1
    assert body["retired_count"] == 0
    # Same slot id survived and keeps its assignment.
    (live,) = _live_requirements(session, process)
    assert live.id == slot_id
    assert live.last_validated_generation == 2
    active = session.exec(
        select(Assignment).where(Assignment.hour_requirement_id == slot_id)
    ).first()
    assert active is not None
    session.refresh(plan)
    assert plan.status == TeachingPlanStatus.REQUIREMENTS_GENERATED


def test_generate_noop_regeneration(client: TestClient, session: Session) -> None:
    process, plan, _subject, _activity = _setup(
        session, required_teacher_count=1, teacher_hours=2.0
    )
    client.post(_generate_url(process.id))
    _mark_stale(session, plan)

    preview = client.post(_preview_url(process.id)).json()
    assert preview["is_noop"] is True
    assert preview["preserve_count"] == 1

    body = client.post(_generate_url(process.id)).json()
    assert body["created_count"] == 0
    assert body["retired_count"] == 0
    assert body["preserved_count"] == 1
    assert body["generation_number"] == 2


def test_generate_retires_removed_unassigned_slot(
    client: TestClient, session: Session
) -> None:
    process, plan, _subject, activity = _setup(
        session, required_teacher_count=2, teacher_hours=2.0
    )
    client.post(_generate_url(process.id))
    # Reduce the activity to a single position.
    activity.required_teacher_count = 1
    session.add(activity)
    session.commit()
    _mark_stale(session, plan)

    body = client.post(_generate_url(process.id)).json()
    assert body["created_count"] == 0
    assert body["preserved_count"] == 1
    assert body["retired_count"] == 1
    live = _live_requirements(session, process)
    assert [r.position_index for r in live] == [0]

    retired = session.exec(
        select(HourRequirement)
        .where(HourRequirement.assignment_process_id == process.id)
        .where(HourRequirement.position_index == 1)
    ).first()
    assert retired is not None
    assert retired.retired_generation == 2
    assert retired.status == HourRequirementStatus.STALE


def test_generate_value_change_unassigned_retire_and_recreate(
    client: TestClient, session: Session
) -> None:
    process, plan, _subject, activity = _setup(
        session, required_teacher_count=1, teacher_hours=2.0
    )
    client.post(_generate_url(process.id))
    old = _live_requirements(session, process)[0]
    old_id = old.id

    activity.teacher_weekly_hours_per_position = 3.0
    session.add(activity)
    session.commit()
    _mark_stale(session, plan)

    body = client.post(_generate_url(process.id)).json()
    assert body["created_count"] == 1
    assert body["retired_count"] == 1
    assert body["preserved_count"] == 0
    (live,) = _live_requirements(session, process)
    assert live.id != old_id
    assert live.required_teacher_hours == 3.0
    assert live.created_generation == 2


# ── generate: assignment-safety conflicts (plan §7.5, §9) ─────────────────────


def _generate_and_assign(client: TestClient, session: Session, process, position=0):
    client.post(_generate_url(process.id))
    slot = session.exec(
        select(HourRequirement)
        .where(HourRequirement.assignment_process_id == process.id)
        .where(HourRequirement.position_index == position)
    ).first()
    assert slot is not None
    profile = factories.make_teacher_profile(session)
    teacher = factories.make_process_teacher(session, process, profile)
    factories.make_assignment(session, process, slot, teacher)
    return slot


def test_generate_conflict_value_changed_assigned_409(
    client: TestClient, session: Session
) -> None:
    process, plan, _subject, activity = _setup(
        session, required_teacher_count=1, teacher_hours=2.0
    )
    slot = _generate_and_assign(client, session, process)

    activity.teacher_weekly_hours_per_position = 5.0
    session.add(activity)
    session.commit()
    _mark_stale(session, plan)

    resp = client.post(_generate_url(process.id))
    assert resp.status_code == 409
    # Nothing changed: the slot stays live, the assignment survives, plan STALE.
    session.refresh(slot)
    assert slot.retired_generation is None
    assert slot.required_teacher_hours == 2.0
    assert (
        session.exec(
            select(Assignment).where(Assignment.hour_requirement_id == slot.id)
        ).first()
        is not None
    )
    session.refresh(plan)
    assert plan.status == TeachingPlanStatus.STALE


def test_generate_conflict_removed_assigned_409(
    client: TestClient, session: Session
) -> None:
    process, plan, _subject, activity = _setup(
        session, required_teacher_count=2, teacher_hours=2.0
    )
    _generate_and_assign(client, session, process, position=1)

    activity.required_teacher_count = 1
    session.add(activity)
    session.commit()
    _mark_stale(session, plan)

    resp = client.post(_generate_url(process.id))
    assert resp.status_code == 409


def test_preview_reports_conflict(client: TestClient, session: Session) -> None:
    process, plan, _subject, activity = _setup(
        session, required_teacher_count=1, teacher_hours=2.0
    )
    slot = _generate_and_assign(client, session, process)
    activity.teacher_weekly_hours_per_position = 5.0
    session.add(activity)
    session.commit()
    _mark_stale(session, plan)

    body = client.post(_preview_url(process.id)).json()
    assert body["requires_reconciliation"] is True
    assert body["conflict_count"] == 1
    assert body["conflict_ids"] == [str(slot.id)]


def test_preview_reports_retire_and_create(
    client: TestClient, session: Session
) -> None:
    process, plan, _subject, activity = _setup(
        session, required_teacher_count=1, teacher_hours=2.0
    )
    client.post(_generate_url(process.id))
    old = _live_requirements(session, process)[0]
    activity.teacher_weekly_hours_per_position = 3.0
    session.add(activity)
    session.commit()
    _mark_stale(session, plan)

    body = client.post(_preview_url(process.id)).json()
    assert body["create_count"] == 1
    assert body["retire_count"] == 1
    assert body["retire_ids"] == [str(old.id)]
    assert body["is_noop"] is False


# ── generate: gates ────────────────────────────────────────────────────────────


def test_generate_missing_process_404(client: TestClient, session: Session) -> None:
    resp = client.post(_generate_url(uuid.uuid4()))
    assert resp.status_code == 404


def test_generate_no_plan_400(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(_generate_url(process.id))
    assert resp.status_code == 400


def test_generate_plan_not_generatable_400(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process, status=TeachingPlanStatus.BALANCED)
    resp = client.post(_generate_url(process.id))
    assert resp.status_code == 400


def test_generate_final_process_400(client: TestClient, session: Session) -> None:
    process, _plan, _subject, _activity = _setup(
        session, process_status=AssignmentProcessStatus.FINAL
    )
    resp = client.post(_generate_url(process.id))
    assert resp.status_code == 400


def test_generate_forbidden_for_reader(
    reader_client: TestClient, session: Session
) -> None:
    process, _plan, _subject, _activity = _setup(session)
    resp = reader_client.post(_generate_url(process.id))
    assert resp.status_code == 403
