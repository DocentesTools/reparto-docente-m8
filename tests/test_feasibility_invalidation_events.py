"""Every committed feasibility invalidation announces itself (plan §11, §20.25).

``teaching_plan.feasibility_invalidated`` exists because a viewer's feasibility
display must fall back to "not evaluated" even when the change that caused it is
invisible at their tier. That only holds if *every* mutating path that drops a
stored result publishes the frame, so this module drives each one through its
real HTTP endpoint with a stored result in place, and asserts the frame is on the
stream.

The stored result is planted directly rather than solved for: what is under test
is the emit site, and a real evaluation would make each case depend on a solvable
instance it does not otherwise need.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.controllers.teaching_plans import TeachingPlanController
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    AssignmentStatus,
    FeasibilityStatus,
    HourRequirementStatus,
    SseEventType,
    SubjectAllocationCategory,
    TeachingActivitySource,
    TeachingPlanStatus,
)
from reparto_service.services import sse
from tests import factories

PREFIX = "/reparto/assignment-processes"


@pytest.fixture
def subscribe():
    """Attach a real subscription to the process-wide broker, detached on teardown."""
    created: list[sse.Subscription] = []

    def _subscribe(process_id: uuid.UUID) -> sse.Subscription:
        subscription = sse.event_broker.subscribe(process_id)
        created.append(subscription)
        return subscription

    yield _subscribe
    for subscription in created:
        subscription.close()


def _store_result(session: Session, plan: TeachingPlan) -> None:
    """Give the plan a stored evaluation, so an invalidation is a real transition."""
    plan.feasibility_status = FeasibilityStatus.FEASIBLE
    plan.feasibility_generation = plan.current_generation_number
    plan.feasibility_input_fingerprint = "f" * 64
    plan.feasibility_solver_version = "bounded-dfs-v1"
    session.add(plan)
    session.commit()


def _assert_invalidated(subscription: sse.Subscription) -> None:
    events, dropped = subscription.drain()
    assert dropped == 0
    invalidations = [
        event
        for event in events
        if event.event_type == SseEventType.TEACHING_PLAN_FEASIBILITY_INVALIDATED
    ]
    assert len(invalidations) == 1, f"expected one invalidation, got {events}"
    assert invalidations[0].payload == {"feasibility_status": "not_evaluated"}


def _main_setup(session: Session):
    """A materialized main activity with its source cell — the §20.10 shape."""
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(
        session,
        process,
        allocation_category=SubjectAllocationCategory.MAIN,
        default_group_weekly_hours=4.0,
        default_teacher_weekly_hours_per_position=4.0,
    )
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(
        session,
        process,
        group,
        subject,
        group_weekly_hours=4.0,
        teacher_weekly_hours_per_position=4.0,
        required_teacher_count=2,
    )
    activity = factories.make_teaching_activity(
        session,
        plan,
        subject,
        allocation_category=SubjectAllocationCategory.MAIN,
        source=TeachingActivitySource.MAIN_GENERATED,
        source_group_subject_id=cell.id,
        group_weekly_hours_per_group=4.0,
        teacher_weekly_hours_per_position=4.0,
        required_teacher_count=2,
        group_subjects=[cell],
    )
    return process, plan, subject, group, cell, activity


# ── Group-subject matrix (plan §7.2, §20.10) ──────────────────────────────────


def test_creating_a_cell_invalidates(
    client: TestClient, session: Session, subscribe
) -> None:
    process, plan, subject, _group, _cell, _activity = _main_setup(session)
    other_group = factories.make_teaching_group(session, process, group_code="1B")
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.post(
        f"{PREFIX}/{process.id}/group-subjects/",
        json={
            "assignment_process_id": str(process.id),
            "teaching_group_id": str(other_group.id),
            "subject_id": str(subject.id),
            "required_teacher_count": 1,
        },
    )

    assert resp.status_code == 201
    _assert_invalidated(subscription)


def test_editing_a_cell_invalidates(
    client: TestClient, session: Session, subscribe
) -> None:
    process, plan, _subject, _group, cell, _activity = _main_setup(session)
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.patch(
        f"{PREFIX}/{process.id}/group-subjects/{cell.id}",
        json={"group_weekly_hours": 5.0},
    )

    assert resp.status_code == 200
    _assert_invalidated(subscription)


def test_retiring_a_cell_invalidates(
    client: TestClient, session: Session, subscribe
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(session, process, group, subject)
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.post(f"{PREFIX}/{process.id}/group-subjects/{cell.id}/retire")

    assert resp.status_code == 200
    _assert_invalidated(subscription)


def test_a_bulk_apply_invalidates(
    client: TestClient, session: Session, subscribe
) -> None:
    process, plan, subject, _group, _cell, _activity = _main_setup(session)
    factories.make_teaching_group(session, process, group_code="1B")
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.post(
        f"{PREFIX}/{process.id}/group-subjects/bulk-apply",
        json={
            "subject_id": str(subject.id),
            "mode": "upsert",
            "required_teacher_count": 1,
            "expected_affected_count": 2,
        },
    )

    assert resp.status_code == 200
    _assert_invalidated(subscription)


def test_applying_a_source_sync_invalidates(
    client: TestClient, session: Session, subscribe
) -> None:
    process, plan, _subject, _group, cell, _activity = _main_setup(session)
    edit = client.patch(
        f"{PREFIX}/{process.id}/group-subjects/{cell.id}",
        json={"group_weekly_hours": 5.0},
    )
    assert edit.status_code == 200
    _store_result(session, plan)
    subscription = subscribe(process.id)

    preview = client.post(
        f"{PREFIX}/{process.id}/group-subjects/{cell.id}/sync-preview"
    )
    assert preview.status_code == 200
    resp = client.post(
        f"{PREFIX}/{process.id}/group-subjects/{cell.id}/sync-apply",
        json={"expected_preview_fingerprint": preview.json()["preview_fingerprint"]},
    )

    assert resp.status_code == 200
    _assert_invalidated(subscription)


# ── Teaching activities (plan §7.3) ───────────────────────────────────────────


def test_creating_an_activity_invalidates(
    client: TestClient, session: Session, subscribe
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(
        session,
        process,
        allocation_category=SubjectAllocationCategory.SECONDARY,
        allows_zero_groups=True,
    )
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.post(
        f"{PREFIX}/{process.id}/teaching-activities/",
        json={
            "subject_id": str(subject.id),
            "group_weekly_hours_per_group": 0.0,
            "teacher_weekly_hours_per_position": 3.0,
            "required_teacher_count": 1,
            "group_subject_ids": [],
        },
    )

    assert resp.status_code == 201
    _assert_invalidated(subscription)


def test_editing_an_activity_invalidates(
    client: TestClient, session: Session, subscribe
) -> None:
    process, plan, _subject, _group, _cell, activity = _main_setup(session)
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.patch(
        f"{PREFIX}/{process.id}/teaching-activities/{activity.id}",
        json={"required_teacher_count": 3},
    )

    assert resp.status_code == 200
    _assert_invalidated(subscription)


def test_retiring_an_activity_invalidates(
    client: TestClient, session: Session, subscribe
) -> None:
    process, plan, _subject, _group, _cell, activity = _main_setup(session)
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.post(
        f"{PREFIX}/{process.id}/teaching-activities/{activity.id}/retire"
    )

    assert resp.status_code == 200
    _assert_invalidated(subscription)


def test_materializing_main_activities_invalidates(
    client: TestClient, session: Session, subscribe
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(
        session,
        process,
        allocation_category=SubjectAllocationCategory.MAIN,
        default_group_weekly_hours=4.0,
        default_teacher_weekly_hours_per_position=4.0,
    )
    group = factories.make_teaching_group(session, process)
    factories.make_group_subject(session, process, group, subject)
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.post(f"{PREFIX}/{process.id}/teaching-plan/materialize-main")

    assert resp.status_code == 200
    assert resp.json()["created_count"] == 1
    _assert_invalidated(subscription)


# ── Participants and allocation (plan §3.8, §9) ───────────────────────────────


def test_adding_a_participant_invalidates(
    client: TestClient, session: Session, subscribe
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    profile = factories.make_teacher_profile(session)
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.post(
        f"{PREFIX}/{process.id}/teachers/",
        json={
            "assignment_process_id": str(process.id),
            "teacher_profile_id": str(profile.id),
            "base_weekly_hours": 18.0,
        },
    )

    assert resp.status_code == 201
    _assert_invalidated(subscription)


def test_updating_extra_hours_invalidates(
    client: TestClient, session: Session, subscribe
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    profile = factories.make_teacher_profile(session)
    participant = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=18.0
    )
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.post(
        f"{PREFIX}/{process.id}/teachers/{participant.id}/extra-hours",
        json={"extra_weekly_hours": 2.0, "reason": "Leadership authorization"},
    )

    assert resp.status_code == 200
    _assert_invalidated(subscription)


def test_removing_a_participant_invalidates(
    client: TestClient, session: Session, subscribe
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    profile = factories.make_teacher_profile(session)
    participant = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=18.0
    )
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.delete(f"{PREFIX}/{process.id}/teachers/{participant.id}")

    assert resp.status_code == 200
    _assert_invalidated(subscription)


def test_recording_an_allocation_revision_invalidates(
    client: TestClient, session: Session, subscribe
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.post(
        f"{PREFIX}/{process.id}/allocation-revisions/",
        json={
            "allocated_group_weekly_hours": 40.0,
            "reason": "Leadership reduced the department allocation",
        },
    )

    assert resp.status_code == 201
    _assert_invalidated(subscription)


# ── Plan lifecycle and assignments (plan §9, §20.13) ──────────────────────────


def test_marking_the_plan_stale_invalidates(
    session: Session, current_user, subscribe
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(
        session, process, status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    _store_result(session, plan)
    subscription = subscribe(process.id)

    TeachingPlanController.mark_stale(
        session, process.id, "Allocation cut by leadership", current_user
    )

    _assert_invalidated(subscription)


def test_undoing_an_assignment_invalidates(
    client: TestClient, session: Session, subscribe
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(
        session, process, status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    subject = factories.make_subject(session, process)
    activity = factories.make_teaching_activity(session, plan, subject)
    requirement = factories.make_hour_requirement(
        session,
        process,
        activity,
        required_teacher_hours=4.0,
        status=HourRequirementStatus.ASSIGNED,
    )
    profile = factories.make_teacher_profile(session)
    participant = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4.0
    )
    assignment = factories.make_assignment(
        session, process, requirement, participant, status=AssignmentStatus.ACTIVE
    )
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.post(
        f"{PREFIX}/{process.id}/assignments/{assignment.id}/undo",
        json={"reason": "Wrong slot chosen in the meeting"},
    )

    assert resp.status_code == 200
    _assert_invalidated(subscription)
