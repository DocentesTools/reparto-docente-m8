"""Tests for the SSE stream route and the controller emit sites (plan §11).

The emit-site tests subscribe to the real process-wide broker and drive the real
HTTP endpoints, so they assert the whole path a viewer actually sees: commit →
publish → buffer → projection.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from collections.abc import AsyncGenerator
from typing import cast

import pytest
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from fastapi_m8 import DbEngine
from sqlalchemy.pool import QueuePool
from sqlmodel import Session, SQLModel, create_engine

from auth_sdk_m8.schemas.user import UserModel

import reparto_service.app.routes.process_events as process_events
from reparto_service.app.routes.process_events import stream_process_events
from reparto_service.controllers.process_teachers import ProcessTeacherController
from reparto_service.controllers.teaching_plans import TeachingPlanController
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.process_teachers import ProcessTeacherExtraHoursUpdate
from reparto_service.enums import (
    AssignmentProcessStatus,
    HourRequirementStatus,
    SseAudience,
    SseEventType,
    TeachingActivitySource,
    TeachingPlanStatus,
)
from reparto_service.schemas.events import DomainEvent
from reparto_service.services import sse
from reparto_service.services.feasibility import SOLVER_VERSION
from tests.factories import (
    enrol,
    make_assignment,
    make_assignment_process,
    make_group_subject,
    make_hour_requirement,
    make_process_teacher,
    make_subject,
    make_teacher_profile,
    make_teaching_activity,
    make_teaching_group,
    make_teaching_plan,
)

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


def _only(subscription: sse.Subscription) -> DomainEvent:
    events, dropped = subscription.drain()
    assert dropped == 0
    assert len(events) == 1, f"expected exactly one event, got {events}"
    return events[0]


def _after_feasibility(subscription: sse.Subscription) -> DomainEvent:
    """Drain an operation that runs the solver first (plan §20.23, §20.25).

    Lock, generation and reconciliation evaluate the intended state before they
    commit, so a persisted result publishes ``teaching_plan.feasibility_updated``
    ahead of the operation's own frame. Asserting the order here keeps the
    per-operation tests about the operation.
    """
    events, dropped = subscription.drain()
    assert dropped == 0
    assert len(events) == 2, f"expected a feasibility frame and one more, got {events}"
    assert events[0].event_type == SseEventType.TEACHING_PLAN_FEASIBILITY_UPDATED
    assert events[0].payload["feasibility_status"] == "feasible"
    assert events[0].payload["witness_available"] is True
    return events[1]


def _live_activity(
    session: Session,
    process,
    plan,
    *,
    hours: float = 6.0,
    positions: int = 1,
):
    """One live activity linked to one group-subject cell — a generatable plan."""
    subject = make_subject(session, process)
    group = make_teaching_group(session, process)
    cell = make_group_subject(session, process, group, subject)
    return make_teaching_activity(
        session,
        plan,
        subject,
        source=TeachingActivitySource.SECONDARY_MANUAL,
        teacher_weekly_hours_per_position=hours,
        required_teacher_count=positions,
        group_subjects=[cell],
    )


# ── Emit site: allocation.revised (plan §3.11) ────────────────────────────────


def test_creating_an_allocation_revision_publishes(
    client: TestClient, session: Session, subscribe
) -> None:
    process = make_assignment_process(session)
    subscription = subscribe(process.id)

    resp = client.post(
        f"{PREFIX}/{process.id}/allocation-revisions/",
        json={
            "assignment_process_id": str(process.id),
            "allocated_group_weekly_hours": "120.00",
            "reason": "Leadership allocation received",
            "source": "manual_transcription",
        },
    )
    assert resp.status_code == 201

    event = _only(subscription)
    assert event.event_type == SseEventType.ALLOCATION_REVISED
    assert event.payload["revision_number"] == 1
    assert event.payload["allocated_group_weekly_hours"] == "120.00"
    assert event.payload["reason"] == "Leadership allocation received"
    assert event.subject_process_teacher_id is None


def test_allocation_event_carries_the_post_change_readiness(
    client: TestClient, session: Session, subscribe
) -> None:
    process = make_assignment_process(session)
    make_teaching_plan(
        session, process, status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    subscription = subscribe(process.id)

    client.post(
        f"{PREFIX}/{process.id}/allocation-revisions/",
        json={
            "assignment_process_id": str(process.id),
            "allocated_group_weekly_hours": "100.00",
            "reason": "Revised downward",
            "source": "manual_transcription",
        },
    )

    event = _only(subscription)
    assert event.readiness.value == "ready"
    assert event.selection_blocked is False


def test_a_refused_allocation_revision_publishes_nothing(
    client: TestClient, session: Session, subscribe
) -> None:
    # A final process is immutable: the write is refused, so nothing may be
    # announced — an event must never advertise a change that did not happen.
    process = make_assignment_process(session, status=AssignmentProcessStatus.FINAL)
    subscription = subscribe(process.id)

    resp = client.post(
        f"{PREFIX}/{process.id}/allocation-revisions/",
        json={
            "assignment_process_id": str(process.id),
            "allocated_group_weekly_hours": "120.00",
            "reason": "Too late",
            "source": "manual_transcription",
        },
    )

    assert resp.status_code == 400
    assert subscription.drain()[0] == []


# ── Emit site: participant.extra_hours_updated (plan §3.8) ────────────────────


def test_updating_extra_hours_publishes_a_participant_scoped_event(
    client: TestClient, session: Session, subscribe
) -> None:
    process = make_assignment_process(session)
    profile = make_teacher_profile(session)
    participant = make_process_teacher(
        session, process, profile, base_weekly_hours=18.0
    )
    subscription = subscribe(process.id)

    resp = client.post(
        f"{PREFIX}/{process.id}/teachers/{participant.id}/extra-hours",
        json={"extra_weekly_hours": "3.00", "reason": "Covers a vacancy"},
    )
    assert resp.status_code == 200

    event = _only(subscription)
    assert event.event_type == SseEventType.PARTICIPANT_EXTRA_HOURS_UPDATED
    assert event.subject_process_teacher_id == participant.id
    assert event.payload["base_weekly_hours"] == "18.00"
    assert event.payload["extra_weekly_hours"] == "3.00"
    assert event.payload["target_weekly_hours"] == "21.00"
    assert event.payload["is_overloaded"] is True
    assert event.payload["reason"] == "Covers a vacancy"


def test_a_refused_extra_hours_change_publishes_nothing(
    client: TestClient, session: Session, subscribe
) -> None:
    # The reduction-below-assigned guard cannot be exercised here: its
    # `_assigned_hours` helper still reads the retired `Assignment.assigned_hours`
    # field and raises (a pre-existing break from the Assignment redesign, owned
    # by "Remove obsolete code paths"). The immutable-process refusal covers the
    # same invariant for this task: a refused write announces nothing.
    process = make_assignment_process(session, status=AssignmentProcessStatus.FINAL)
    profile = make_teacher_profile(session)
    participant = make_process_teacher(session, process, profile)
    subscription = subscribe(process.id)

    resp = client.post(
        f"{PREFIX}/{process.id}/teachers/{participant.id}/extra-hours",
        json={"extra_weekly_hours": "3.00", "reason": "Too late"},
    )

    assert resp.status_code == 400
    assert subscription.drain()[0] == []


# ── Emit site: teaching_plan.* (plan §9) ──────────────────────────────────────


def test_creating_a_plan_publishes_teaching_plan_updated(
    client: TestClient, session: Session, subscribe
) -> None:
    process = make_assignment_process(session)
    subscription = subscribe(process.id)

    resp = client.post(f"{PREFIX}/{process.id}/teaching-plan", json={})
    assert resp.status_code == 201

    event = _only(subscription)
    assert event.event_type == SseEventType.TEACHING_PLAN_UPDATED
    assert event.payload["status"] == "draft"
    assert event.readiness.value == "not_ready"


def test_marking_a_plan_stale_publishes_a_blocking_event(
    session: Session, current_user: UserModel, subscribe
) -> None:
    process = make_assignment_process(session)
    make_teaching_plan(
        session, process, status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    subscription = subscribe(process.id)

    TeachingPlanController.mark_stale(
        session, process.id, "Allocation cut by leadership", current_user
    )

    event = _only(subscription)
    assert event.event_type == SseEventType.TEACHING_PLAN_STALE
    assert event.payload["status"] == "stale"
    assert event.payload["reason"] == "Allocation cut by leadership"
    # The whole point of the event: teachers must stop selecting.
    assert event.readiness.value == "recalculation_required"
    assert event.selection_blocked is True


# ── Emit site: requirements.* (plan §7.5, §9) ─────────────────────────────────


def test_generating_requirements_publishes_the_generation_counts(
    client: TestClient, session: Session, subscribe
) -> None:
    process = make_assignment_process(session)
    plan = make_teaching_plan(session, process, status=TeachingPlanStatus.LOCKED)
    activity = _live_activity(session, process, plan, positions=2)
    for _ in range(2):
        profile = make_teacher_profile(session)
        make_process_teacher(
            session,
            process,
            profile,
            base_weekly_hours=activity.teacher_weekly_hours_per_position,
        )
    subscription = subscribe(process.id)

    resp = client.post(f"{PREFIX}/{process.id}/requirements/generate")
    assert resp.status_code == 200

    event = _after_feasibility(subscription)
    assert event.event_type == SseEventType.REQUIREMENTS_GENERATED
    assert event.payload["generation_number"] == 1
    assert event.payload["created_count"] == 2
    assert event.payload["live_slot_count"] == 2
    # Slots now exist: the stage is open, which is exactly what a LAN client is
    # waiting for.
    assert event.readiness.value == "ready"
    assert event.selection_blocked is False


def test_a_conflicted_generation_publishes_nothing(
    client: TestClient, session: Session, subscribe
) -> None:
    process = make_assignment_process(session)
    plan = make_teaching_plan(
        session,
        process,
        status=TeachingPlanStatus.STALE,
        current_generation_number=1,
    )
    profile = make_teacher_profile(session)
    participant = make_process_teacher(session, process, profile, base_weekly_hours=9.0)
    activity = _live_activity(session, process, plan)
    requirement = make_hour_requirement(
        session,
        process,
        activity,
        required_teacher_hours=6.0,
        status=HourRequirementStatus.ASSIGNED,
    )
    make_assignment(session, process, requirement, participant)
    activity.teacher_weekly_hours_per_position = 9.0
    session.add(activity)
    session.commit()
    subscription = subscribe(process.id)

    resp = client.post(f"{PREFIX}/{process.id}/requirements/generate")

    assert resp.status_code == 409
    assert subscription.drain()[0] == []


def test_reconciling_publishes_the_released_assignments(
    client: TestClient, session: Session, subscribe
) -> None:
    process = make_assignment_process(session)
    plan = make_teaching_plan(
        session,
        process,
        status=TeachingPlanStatus.STALE,
        current_generation_number=1,
    )
    profile = make_teacher_profile(session)
    participant = make_process_teacher(session, process, profile, base_weekly_hours=9.0)
    activity = _live_activity(session, process, plan)
    requirement = make_hour_requirement(
        session,
        process,
        activity,
        required_teacher_hours=6.0,
        status=HourRequirementStatus.ASSIGNED,
    )
    assignment = make_assignment(session, process, requirement, participant)
    activity.teacher_weekly_hours_per_position = 9.0
    session.add(activity)
    session.commit()
    subscription = subscribe(process.id)

    resp = client.post(
        f"{PREFIX}/{process.id}/requirements/reconcile",
        json={"reason": "Allocation change", "expected_conflict_count": 1},
    )
    assert resp.status_code == 200

    event = _after_feasibility(subscription)
    assert event.event_type == SseEventType.REQUIREMENTS_RECONCILED
    assert event.payload["resolved_count"] == 1
    assert event.payload["released_assignment_ids"] == [str(assignment.id)]
    assert event.payload["reason"] == "Allocation change"


# ── Publishing never breaks the request ───────────────────────────────────────


def test_a_broken_broker_does_not_fail_the_committed_write(
    client: TestClient, session: Session, monkeypatch, caplog
) -> None:
    def explode(**_kwargs):
        raise RuntimeError("broker is wedged")

    monkeypatch.setattr(sse.event_broker, "publish", explode)
    process = make_assignment_process(session)

    resp = client.post(
        f"{PREFIX}/{process.id}/allocation-revisions/",
        json={
            "assignment_process_id": str(process.id),
            "allocated_group_weekly_hours": "120.00",
            "reason": "Leadership allocation",
            "source": "manual_transcription",
        },
    )

    # The row committed; a broadcast failure must not turn that into a 500.
    assert resp.status_code == 201
    assert (
        client.get(f"{PREFIX}/{process.id}/allocation-revisions/").json()["count"] == 1
    )
    assert "sse publish failed" in caplog.text


# ── Stream route ──────────────────────────────────────────────────────────────


#
# The streaming assertions call the route function directly rather than going
# through TestClient. Neither TestClient nor httpx's ASGITransport can read an
# SSE response incrementally — both buffer the body until the ASGI app returns,
# and this endpoint deliberately never does. Driving the returned
# StreamingResponse's body_iterator exercises the same route code (tier
# resolution, subscription, framing, teardown) and lets a test disconnect on
# demand, which is what a real server does with `http.disconnect`. The guards
# below that reject before any stream opens are exercised over real HTTP.


async def _open(
    user: UserModel,
    process_id: uuid.UUID,
    audience: SseAudience | None = None,
) -> tuple[StreamingResponse, str, AsyncGenerator[str, None]]:
    """Call the route and return (response, first_frame, body_iterator)."""
    response = await stream_process_events(user, process_id, audience)
    # Starlette types body_iterator as a bare AsyncIterable; the route always
    # hands it our async generator, which is what a disconnect must aclose().
    body = cast("AsyncGenerator[str, None]", response.body_iterator)
    first = await asyncio.wait_for(body.__anext__(), 1.0)
    return response, first, body


def _data(frame: str) -> dict:
    return json.loads(frame.split("data: ", 1)[1])


@pytest.mark.anyio
async def test_open_streams_hold_no_pool_connection(
    tmp_path, monkeypatch: pytest.MonkeyPatch, current_user: UserModel
) -> None:
    """R1's whole point (plan §11, §R1): the SSE session is scoped to its three
    reads, not held for the stream's life — so N open viewers must consume 0
    pool connections once the stream has started.

    The shared in-memory ``StaticPool`` engine used by every other test in
    this module has no ``checkedout()`` to assert on, so this test points the
    route at its own file-backed SQLite engine, which defaults to a real
    ``QueuePool``. Mutation check: reverting the route to a held ``SessionDep``
    makes this fail — three streams would leave three connections checked out.
    """
    db_path = tmp_path / "pool_test.db"
    pool_engine = create_engine(f"sqlite:///{db_path}")
    pool = cast("QueuePool", pool_engine.pool)
    SQLModel.metadata.create_all(pool_engine)
    with Session(pool_engine) as setup_session:
        process = make_assignment_process(setup_session)
    monkeypatch.setattr(process_events, "engine", DbEngine(pool_engine))

    streams = [await _open(current_user, process.id) for _ in range(3)]
    try:
        assert pool.checkedout() == 0
        # The pool being free is what lets an ordinary request still succeed.
        with process_events.engine.session() as probe:
            assert probe.get(AssignmentProcess, process.id) is not None
    finally:
        for _, _, body in streams:
            await body.aclose()
    pool_engine.dispose()


@pytest.mark.anyio
async def test_stream_opens_with_the_readiness_baseline(
    session: Session, current_user: UserModel
) -> None:
    process = make_assignment_process(session)
    make_teaching_plan(
        session, process, status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )

    response, frame, body = await _open(current_user, process.id)

    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-store"
    assert frame.startswith("event: stream.opened")
    assert _data(frame)["readiness"] == "ready"
    assert _data(frame)["payload"] == {"audience": "department_head"}
    await body.aclose()


@pytest.mark.anyio
async def test_stream_baseline_is_projected_for_a_shared_screen(
    session: Session, current_user: UserModel
) -> None:
    process = make_assignment_process(session)

    _, frame, body = await _open(current_user, process.id, SseAudience.SHARED_SCREEN)

    assert _data(frame) == {"readiness": "not_ready"}
    await body.aclose()


@pytest.mark.anyio
async def test_stream_relays_a_committed_change_to_a_subscriber(
    session: Session, current_user: UserModel
) -> None:
    # The end-to-end path: a controller commits, and the connected head sees it.
    process = make_assignment_process(session)
    make_teaching_plan(
        session, process, status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    _, _, body = await _open(current_user, process.id)

    TeachingPlanController.mark_stale(
        session, process.id, "Allocation cut", current_user
    )

    frame = await asyncio.wait_for(body.__anext__(), 1.0)
    assert frame.startswith("event: teaching_plan.stale")
    assert _data(frame)["payload"]["reason"] == "Allocation cut"
    assert _data(frame)["readiness"] == "recalculation_required"
    await body.aclose()


@pytest.mark.anyio
async def test_stream_relays_only_a_teachers_own_hours(
    session: Session, current_user: UserModel, reader: UserModel
) -> None:
    # Two participants; the reader is subscribed. The head raises the *other*
    # teacher's hours — the subscriber must not learn their figures (plan §20.25).
    process = make_assignment_process(session)
    subscriber_profile = make_teacher_profile(
        session, user_id=uuid.UUID(str(reader.id))
    )
    make_process_teacher(session, process, subscriber_profile)
    other = make_process_teacher(
        session, process, make_teacher_profile(session, display_name="Other")
    )
    _, _, body = await _open(reader, process.id)

    ProcessTeacherController.update_extra_hours(
        session,
        process.id,
        other.id,
        ProcessTeacherExtraHoursUpdate(
            extra_weekly_hours=Decimal("4.00"), reason="Cover"
        ),
        current_user,
    )

    frame = await asyncio.wait_for(body.__anext__(), 1.0)
    assert frame.startswith("event: participant.extra_hours_updated")
    data = _data(frame)
    assert "payload" not in data
    assert "4.00" not in frame
    assert str(other.id) not in frame
    await body.aclose()


@pytest.mark.anyio
async def test_stream_relays_a_teachers_own_hours_without_the_reason(
    session: Session, current_user: UserModel, reader: UserModel
) -> None:
    # The mirror of the test above, and the case that actually reaches a LAN
    # client: the head raises the *subscriber's own* hours. The figures are
    # theirs and arrive; the head's written justification is department-head-only
    # (plan §17) and must not, exactly as the LAN read payload omits it.
    process = make_assignment_process(session)
    subscriber_profile = make_teacher_profile(
        session, user_id=uuid.UUID(str(reader.id))
    )
    participant = make_process_teacher(
        session, process, subscriber_profile, base_weekly_hours=18.0
    )
    _, _, body = await _open(reader, process.id)

    ProcessTeacherController.update_extra_hours(
        session,
        process.id,
        participant.id,
        ProcessTeacherExtraHoursUpdate(
            extra_weekly_hours=Decimal("3.00"), reason="Covers a vacancy"
        ),
        current_user,
    )

    frame = await asyncio.wait_for(body.__anext__(), 1.0)
    assert frame.startswith("event: participant.extra_hours_updated")
    data = _data(frame)
    assert data["payload"]["target_weekly_hours"] == "21.00"
    assert data["payload"]["extra_weekly_hours"] == "3.00"
    assert "reason" not in data["payload"]
    assert "Covers a vacancy" not in frame
    await body.aclose()


@pytest.mark.anyio
async def test_stream_detaches_the_subscription_on_disconnect(
    session: Session, current_user: UserModel
) -> None:
    process = make_assignment_process(session)
    _, _, body = await _open(current_user, process.id)
    assert sse.event_broker.subscriber_count(process.id) == 1

    await body.aclose()  # what a client disconnect triggers

    # A stream left attached would leak a buffer per reconnect for the life of
    # the process.
    assert sse.event_broker.subscriber_count(process.id) == 0


def test_stream_404s_on_a_missing_process(client: TestClient) -> None:
    resp = client.get(f"{PREFIX}/{uuid.uuid4()}/events")
    assert resp.status_code == 404


def test_stream_refuses_an_audience_upgrade(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    process = make_assignment_process(session)
    enrol(session, process, reader)
    resp = reader_client.get(f"{PREFIX}/{process.id}/events?audience=department_head")
    assert resp.status_code == 403


def test_stream_rejects_an_unknown_audience(
    client: TestClient, session: Session
) -> None:
    process = make_assignment_process(session)
    resp = client.get(f"{PREFIX}/{process.id}/events?audience=headmaster")
    assert resp.status_code == 422


# ── Emit site: teaching_plan.feasibility_* (plan §20.25) ──────────────────────


def _feasibility_setup(session: Session):
    """A two-slot plan with exactly two matching participants — solvable."""
    process = make_assignment_process(session)
    plan = make_teaching_plan(session, process)
    activity = _live_activity(session, process, plan, hours=4.0, positions=2)
    slots = [
        make_hour_requirement(
            session,
            process,
            activity,
            position_index=index,
            required_teacher_hours=4.0,
        )
        for index in range(2)
    ]
    teachers = [
        make_process_teacher(
            session,
            process,
            make_teacher_profile(session, display_name=f"Teacher {index}"),
            base_weekly_hours=4.0,
        )
        for index in range(2)
    ]
    return process, plan, slots, teachers


def test_evaluating_feasibility_publishes_the_persisted_result(
    admin_client: TestClient, session: Session, subscribe
) -> None:
    process, plan, _slots, _teachers = _feasibility_setup(session)
    subscription = subscribe(process.id)

    resp = admin_client.post(
        f"{PREFIX}/{process.id}/teaching-plan/feasibility/evaluate"
    )
    assert resp.status_code == 200

    event = _only(subscription)
    assert event.event_type == SseEventType.TEACHING_PLAN_FEASIBILITY_UPDATED
    assert event.payload["teaching_plan_id"] == str(plan.id)
    assert event.payload["feasibility_status"] == "feasible"
    assert event.payload["witness_available"] is True
    assert event.payload["solver_version"] == SOLVER_VERSION
    assert event.payload["diagnostic_codes"] == []
    assert event.payload["affected_ids"] == []
    assert event.payload["duration_ms"] >= 0
    assert event.payload["feasibility_checked_at"] is not None
    # The witness is a complete provisional reparto and never leaves its
    # restricted store (plan §20.24).
    assert "witness" not in event.payload


def test_a_reused_feasibility_result_publishes_nothing(
    admin_client: TestClient, session: Session, subscribe
) -> None:
    process, _plan, _slots, _teachers = _feasibility_setup(session)
    admin_client.post(f"{PREFIX}/{process.id}/teaching-plan/feasibility/evaluate")
    subscription = subscribe(process.id)

    resp = admin_client.post(
        f"{PREFIX}/{process.id}/teaching-plan/feasibility/evaluate"
    )

    assert resp.status_code == 200
    assert resp.json()["cache_reused"] is True
    # No status transitioned, so there is nothing for a viewer to react to.
    assert subscription.drain()[0] == []


def test_an_infeasible_result_publishes_its_diagnostic_summary(
    admin_client: TestClient, session: Session, subscribe
) -> None:
    process, _plan, _slots, teachers = _feasibility_setup(session)
    # One participant can no longer reach an exact target with 4.00-hour slots.
    teachers[0].base_weekly_hours = 5.0
    session.add(teachers[0])
    session.commit()
    subscription = subscribe(process.id)

    resp = admin_client.post(
        f"{PREFIX}/{process.id}/teaching-plan/feasibility/evaluate"
    )
    assert resp.status_code == 200

    event = _only(subscription)
    assert event.event_type == SseEventType.TEACHING_PLAN_FEASIBILITY_UPDATED
    assert event.payload["feasibility_status"] == "infeasible"
    assert event.payload["witness_available"] is False
    assert event.payload["diagnostic_codes"]
    # The head tier receives the codes; a teacher receives none of this (below).
    assert all(isinstance(code, str) for code in event.payload["diagnostic_codes"])


def test_a_feasibility_frame_carries_no_payload_to_the_lower_tiers(
    admin_client: TestClient, session: Session, subscribe
) -> None:
    process, _plan, _slots, _teachers = _feasibility_setup(session)
    subscription = subscribe(process.id)
    admin_client.post(f"{PREFIX}/{process.id}/teaching-plan/feasibility/evaluate")
    event = _only(subscription)

    teacher_view = sse.project_event(event, SseAudience.TEACHER)
    screen_view = sse.project_event(event, SseAudience.SHARED_SCREEN)

    # A feasibility event names no participant, so the teacher tier never gets
    # the "own participant" payload exception — status and diagnostics stay
    # administration-only (plan §20.24, §20.25).
    assert "payload" not in teacher_view
    assert teacher_view["readiness"] == event.readiness.value
    assert teacher_view["selection_blocked"] == event.selection_blocked
    assert screen_view == {"readiness": event.readiness.value}


def test_a_mutation_that_drops_a_stored_result_publishes_invalidated(
    admin_client: TestClient, session: Session, subscribe
) -> None:
    process, _plan, _slots, teachers = _feasibility_setup(session)
    admin_client.post(f"{PREFIX}/{process.id}/teaching-plan/feasibility/evaluate")
    subscription = subscribe(process.id)

    resp = admin_client.patch(
        f"{PREFIX}/{process.id}/teachers/{teachers[0].id}",
        json={"base_weekly_hours": "6.00"},
    )
    assert resp.status_code == 200

    event = _only(subscription)
    assert event.event_type == SseEventType.TEACHING_PLAN_FEASIBILITY_INVALIDATED
    assert event.payload == {"feasibility_status": "not_evaluated"}


def test_a_mutation_without_a_stored_result_publishes_no_invalidation(
    admin_client: TestClient, session: Session, subscribe
) -> None:
    process, _plan, _slots, teachers = _feasibility_setup(session)
    subscription = subscribe(process.id)

    resp = admin_client.patch(
        f"{PREFIX}/{process.id}/teachers/{teachers[0].id}",
        json={"base_weekly_hours": "6.00"},
    )

    assert resp.status_code == 200
    # Nothing transitioned: the plan was already NOT_EVALUATED.
    assert subscription.drain()[0] == []
