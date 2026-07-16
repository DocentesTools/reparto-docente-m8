"""Tests for the SSE broker and role-safe projection (plan §11, §20.25).

The projection tests are the security-relevant ones: they assert what a tier
*cannot* see, not just what it can.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.enums import (
    PlanReadiness,
    SseAudience,
    SseEventType,
    TeachingPlanStatus,
)
from reparto_service.schemas.events import DomainEvent
from reparto_service.services import sse
from tests.factories import (
    make_assignment_process,
    make_department,
    make_process_teacher,
    make_school,
    make_teacher_profile,
    make_teaching_plan,
)


def _event(
    *,
    event_type: SseEventType = SseEventType.PARTICIPANT_EXTRA_HOURS_UPDATED,
    process_id: uuid.UUID | None = None,
    readiness: PlanReadiness = PlanReadiness.READY,
    selection_blocked: bool = False,
    payload: dict | None = None,
    subject: uuid.UUID | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        process_id=process_id or uuid.uuid4(),
        sequence=7,
        occurred_at=datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc),
        readiness=readiness,
        selection_blocked=selection_blocked,
        payload=payload if payload is not None else {"target_weekly_hours": "21.00"},
        subject_process_teacher_id=subject,
    )


# ── hours_string (plan §3.9) ──────────────────────────────────────────────────


def test_hours_string_renders_canonical_two_places() -> None:
    assert sse.hours_string(18.0) == "18.00"
    assert sse.hours_string(Decimal("2.5")) == "2.50"
    assert sse.hours_string(0) == "0.00"


def test_hours_string_does_not_leak_binary_float_noise() -> None:
    # 17.4 has no exact binary representation; a naive str() would emit
    # 17.399999999999999 for an accumulated value.
    assert sse.hours_string(0.1 + 0.2) == "0.30"
    assert sse.hours_string(17.4) == "17.40"


# ── current_readiness ─────────────────────────────────────────────────────────


def test_readiness_without_a_plan_is_not_ready(session: Session) -> None:
    process = make_assignment_process(session)
    assert sse.current_readiness(session, process.id) == (
        PlanReadiness.NOT_READY,
        False,
    )


@pytest.mark.parametrize(
    ("status", "expected", "blocked"),
    [
        (TeachingPlanStatus.DRAFT, PlanReadiness.NOT_READY, False),
        (TeachingPlanStatus.UNBALANCED, PlanReadiness.NOT_READY, False),
        (TeachingPlanStatus.BALANCED, PlanReadiness.NOT_READY, False),
        (TeachingPlanStatus.LOCKED, PlanReadiness.NOT_READY, False),
        (TeachingPlanStatus.REQUIREMENTS_GENERATED, PlanReadiness.READY, False),
        (TeachingPlanStatus.STALE, PlanReadiness.RECALCULATION_REQUIRED, True),
        (
            TeachingPlanStatus.RECONCILIATION_REQUIRED,
            PlanReadiness.RECALCULATION_REQUIRED,
            True,
        ),
    ],
)
def test_readiness_projects_every_plan_status(
    session: Session,
    status: TeachingPlanStatus,
    expected: PlanReadiness,
    blocked: bool,
) -> None:
    process = make_assignment_process(session)
    make_teaching_plan(session, process, status=status)
    assert sse.current_readiness(session, process.id) == (expected, blocked)


# ── Audience resolution ───────────────────────────────────────────────────────


def test_writer_role_is_granted_the_department_head_tier(
    session: Session, current_user: UserModel
) -> None:
    process = make_assignment_process(session)
    assert (
        sse.granted_audience(session, process.id, current_user)
        == SseAudience.DEPARTMENT_HEAD
    )


def test_reader_role_is_granted_only_the_teacher_tier(
    session: Session, reader: UserModel
) -> None:
    process = make_assignment_process(session)
    assert sse.granted_audience(session, process.id, reader) == SseAudience.TEACHER


def test_bound_department_head_user_is_granted_the_head_tier(
    session: Session, reader: UserModel
) -> None:
    # A plain (reader-role) auth user bound as the department head still gets the
    # full payload — the same rule require_process_writer applies to mutations.
    school = make_school(session)
    department = make_department(session, school)
    department.department_head_user_id = uuid.UUID(str(reader.id))
    session.add(department)
    session.commit()
    process = make_assignment_process(session, school=school, department=department)
    assert (
        sse.granted_audience(session, process.id, reader) == SseAudience.DEPARTMENT_HEAD
    )


def test_resolve_audience_defaults_to_the_granted_tier(
    session: Session, current_user: UserModel
) -> None:
    process = make_assignment_process(session)
    assert (
        sse.resolve_audience(session, process.id, current_user)
        == SseAudience.DEPARTMENT_HEAD
    )


def test_resolve_audience_allows_a_downgrade(
    session: Session, current_user: UserModel
) -> None:
    process = make_assignment_process(session)
    for requested in (SseAudience.TEACHER, SseAudience.SHARED_SCREEN):
        assert (
            sse.resolve_audience(session, process.id, current_user, requested)
            == requested
        )


def test_resolve_audience_refuses_an_upgrade(
    session: Session, reader: UserModel
) -> None:
    process = make_assignment_process(session)
    with pytest.raises(HTTPException) as exc:
        sse.resolve_audience(session, process.id, reader, SseAudience.DEPARTMENT_HEAD)
    assert exc.value.status_code == 403
    assert "grants at most teacher" in exc.value.detail


def test_resolve_audience_allows_the_same_tier(
    session: Session, reader: UserModel
) -> None:
    process = make_assignment_process(session)
    assert (
        sse.resolve_audience(session, process.id, reader, SseAudience.TEACHER)
        == SseAudience.TEACHER
    )


# ── viewer_participant_id ─────────────────────────────────────────────────────


def test_viewer_participant_id_finds_the_callers_own_row(
    session: Session, current_user: UserModel
) -> None:
    process = make_assignment_process(session)
    profile = make_teacher_profile(session, user_id=uuid.UUID(str(current_user.id)))
    participant = make_process_teacher(session, process, profile)
    assert (
        sse.viewer_participant_id(session, process.id, current_user) == participant.id
    )


def test_viewer_participant_id_is_none_without_a_linked_participant(
    session: Session, current_user: UserModel
) -> None:
    process = make_assignment_process(session)
    assert sse.viewer_participant_id(session, process.id, current_user) is None


def test_viewer_participant_id_ignores_another_processes_participation(
    session: Session, current_user: UserModel
) -> None:
    profile = make_teacher_profile(session, user_id=uuid.UUID(str(current_user.id)))
    other_process = make_assignment_process(session)
    make_process_teacher(session, other_process, profile)
    process = make_assignment_process(session)
    assert sse.viewer_participant_id(session, process.id, current_user) is None


# ── Projection (plan §20.25) ──────────────────────────────────────────────────


def test_head_tier_receives_the_full_event() -> None:
    event = _event(payload={"target_weekly_hours": "21.00", "reason": "Cover"})
    projected = sse.project_event(event, SseAudience.DEPARTMENT_HEAD)
    assert projected["payload"] == {"target_weekly_hours": "21.00", "reason": "Cover"}
    assert projected["sequence"] == 7
    assert projected["readiness"] == "ready"


def test_shared_screen_tier_receives_readiness_and_nothing_else() -> None:
    event = _event(
        readiness=PlanReadiness.RECALCULATION_REQUIRED,
        payload={"target_weekly_hours": "21.00"},
        subject=uuid.uuid4(),
    )
    projected = sse.project_event(event, SseAudience.SHARED_SCREEN)
    assert projected == {"readiness": "recalculation_required"}


def test_shared_screen_tier_never_leaks_identifiers_or_hours() -> None:
    subject = uuid.uuid4()
    event = _event(payload={"target_weekly_hours": "21.00"}, subject=subject)
    # Project for the *subject themselves* — even then the shared screen sees
    # readiness only; the tier, not the viewer identity, decides.
    rendered = json.dumps(sse.project_event(event, SseAudience.SHARED_SCREEN, subject))
    assert str(subject) not in rendered
    assert "21.00" not in rendered
    assert str(event.process_id) not in rendered


def test_teacher_tier_hides_another_participants_payload() -> None:
    event = _event(payload={"target_weekly_hours": "21.00"}, subject=uuid.uuid4())
    projected = sse.project_event(event, SseAudience.TEACHER, uuid.uuid4())
    assert "payload" not in projected
    assert "process_teacher_id" not in projected
    assert "21.00" not in json.dumps(projected)
    # The teacher still learns that *something* changed, so their client refetches.
    assert projected["event_type"] == "participant.extra_hours_updated"
    assert projected["readiness"] == "ready"


def test_teacher_tier_receives_their_own_participant_payload() -> None:
    subject = uuid.uuid4()
    event = _event(payload={"target_weekly_hours": "21.00"}, subject=subject)
    projected = sse.project_event(event, SseAudience.TEACHER, subject)
    assert projected["payload"] == {"target_weekly_hours": "21.00"}
    assert projected["process_teacher_id"] == str(subject)


def test_teacher_tier_hides_the_payload_of_an_unscoped_event() -> None:
    # A plan-wide event has no subject, so it is nobody's "own" event even for a
    # viewer who is a participant — the plan status stays head-only.
    event = _event(
        event_type=SseEventType.TEACHING_PLAN_STALE,
        payload={"status": "stale", "reason": "Allocation cut"},
        subject=None,
    )
    projected = sse.project_event(event, SseAudience.TEACHER, uuid.uuid4())
    assert "payload" not in projected
    assert "stale" not in json.dumps(projected).replace("teaching_plan.stale", "")


def test_teacher_tier_carries_the_selection_blocked_signal() -> None:
    event = _event(
        event_type=SseEventType.TEACHING_PLAN_STALE,
        readiness=PlanReadiness.RECALCULATION_REQUIRED,
        selection_blocked=True,
        subject=None,
    )
    projected = sse.project_event(event, SseAudience.TEACHER)
    assert projected["selection_blocked"] is True
    assert projected["readiness"] == "recalculation_required"


def test_teacher_tier_without_a_participant_id_sees_no_payload() -> None:
    event = _event(payload={"target_weekly_hours": "21.00"}, subject=uuid.uuid4())
    projected = sse.project_event(event, SseAudience.TEACHER, None)
    assert "payload" not in projected


# ── Framing ───────────────────────────────────────────────────────────────────


def test_format_frame_renders_one_data_line() -> None:
    frame = sse.format_frame("allocation.revised", {"revision_number": 2})
    assert frame == 'event: allocation.revised\ndata: {"revision_number": 2}\n\n'


def test_format_frame_serialises_non_json_natives() -> None:
    item = uuid.uuid4()
    frame = sse.format_frame("x", {"id": item})
    assert str(item) in frame
    assert frame.count("\ndata: ") == 1


def test_format_comment_renders_a_comment_line() -> None:
    assert sse.format_comment("keep-alive") == ": keep-alive\n\n"


# ── Broker ────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_publish_fans_out_to_every_subscriber_of_the_topic() -> None:
    broker = sse.EventBroker()
    process_id = uuid.uuid4()
    first = broker.subscribe(process_id)
    second = broker.subscribe(process_id)
    other = broker.subscribe(uuid.uuid4())

    broker.publish(
        process_id=process_id,
        event_type=SseEventType.ALLOCATION_REVISED,
        readiness=PlanReadiness.NOT_READY,
    )

    assert len(first.drain()[0]) == 1
    assert len(second.drain()[0]) == 1
    assert other.drain()[0] == []


@pytest.mark.anyio
async def test_publish_stamps_a_monotonic_sequence() -> None:
    broker = sse.EventBroker()
    process_id = uuid.uuid4()
    first = broker.publish(
        process_id=process_id,
        event_type=SseEventType.ALLOCATION_REVISED,
        readiness=PlanReadiness.NOT_READY,
    )
    second = broker.publish(
        process_id=process_id,
        event_type=SseEventType.TEACHING_PLAN_STALE,
        readiness=PlanReadiness.RECALCULATION_REQUIRED,
    )
    assert second.sequence == first.sequence + 1
    assert first.occurred_at.tzinfo is timezone.utc


@pytest.mark.anyio
async def test_publish_without_subscribers_still_returns_the_event() -> None:
    broker = sse.EventBroker()
    event = broker.publish(
        process_id=uuid.uuid4(),
        event_type=SseEventType.REQUIREMENTS_GENERATED,
        readiness=PlanReadiness.READY,
        payload={"generation_number": 1},
    )
    assert event.payload == {"generation_number": 1}
    assert event.selection_blocked is False


@pytest.mark.anyio
async def test_unsubscribe_stops_delivery_and_drops_the_topic() -> None:
    broker = sse.EventBroker()
    process_id = uuid.uuid4()
    subscription = broker.subscribe(process_id)
    assert broker.subscriber_count(process_id) == 1

    subscription.close()

    assert broker.subscriber_count(process_id) == 0
    broker.publish(
        process_id=process_id,
        event_type=SseEventType.ALLOCATION_REVISED,
        readiness=PlanReadiness.NOT_READY,
    )
    assert subscription.drain()[0] == []


@pytest.mark.anyio
async def test_closing_twice_is_idempotent() -> None:
    broker = sse.EventBroker()
    process_id = uuid.uuid4()
    first = broker.subscribe(process_id)
    second = broker.subscribe(process_id)

    first.close()
    first.close()  # topic still exists (second is attached)
    second.close()
    second.close()  # topic already deleted — must not raise

    assert broker.subscriber_count(process_id) == 0


@pytest.mark.anyio
async def test_overflow_drops_the_oldest_and_reports_a_gap() -> None:
    broker = sse.EventBroker(buffer_size=2)
    process_id = uuid.uuid4()
    subscription = broker.subscribe(process_id)

    for _ in range(4):
        broker.publish(
            process_id=process_id,
            event_type=SseEventType.ALLOCATION_REVISED,
            readiness=PlanReadiness.NOT_READY,
        )

    events, dropped = subscription.drain()
    assert dropped == 2
    # The two survivors are the newest, not the oldest.
    assert [e.sequence for e in events] == [3, 4]
    # The drop count resets once reported, so a gap is announced exactly once.
    assert subscription.drain() == ([], 0)


@pytest.mark.anyio
async def test_wait_returns_true_when_an_event_arrives() -> None:
    broker = sse.EventBroker()
    process_id = uuid.uuid4()
    subscription = broker.subscribe(process_id)
    broker.publish(
        process_id=process_id,
        event_type=SseEventType.ALLOCATION_REVISED,
        readiness=PlanReadiness.NOT_READY,
    )
    await asyncio.sleep(0)  # let the threadsafe wakeup callback run
    assert await subscription.wait(1.0) is True


@pytest.mark.anyio
async def test_wait_returns_false_on_the_heartbeat_timeout() -> None:
    broker = sse.EventBroker()
    subscription = broker.subscribe(uuid.uuid4())
    assert await subscription.wait(0.01) is False


@pytest.mark.anyio
async def test_publish_from_a_worker_thread_reaches_the_loop() -> None:
    # The real emit path: sync controllers run in FastAPI's threadpool while the
    # stream lives on the event loop.
    broker = sse.EventBroker()
    process_id = uuid.uuid4()
    subscription = broker.subscribe(process_id)

    await asyncio.to_thread(
        lambda: broker.publish(
            process_id=process_id,
            event_type=SseEventType.ALLOCATION_REVISED,
            readiness=PlanReadiness.NOT_READY,
        )
    )

    assert await subscription.wait(1.0) is True
    assert len(subscription.drain()[0]) == 1


@pytest.mark.anyio
async def test_a_publish_wakes_a_reader_that_is_already_asleep() -> None:
    # The other half of the race: here the reader suspends *first*, so delivery
    # depends on the cross-thread wakeup rather than on the buffer pre-check.
    broker = sse.EventBroker()
    process_id = uuid.uuid4()
    subscription = broker.subscribe(process_id)

    waiter = asyncio.create_task(subscription.wait(5.0))
    await asyncio.sleep(0.05)  # let it actually suspend
    assert not waiter.done()

    await asyncio.to_thread(
        lambda: broker.publish(
            process_id=process_id,
            event_type=SseEventType.ALLOCATION_REVISED,
            readiness=PlanReadiness.NOT_READY,
        )
    )

    assert await asyncio.wait_for(waiter, 1.0) is True
    assert len(subscription.drain()[0]) == 1
    # The flag was consumed, so the next wait suspends again rather than
    # returning instantly on a stale wakeup.
    assert await subscription.wait(0.01) is False


# ── event_stream ──────────────────────────────────────────────────────────────


async def _next(stream) -> str:
    return await asyncio.wait_for(stream.__anext__(), 1.0)


@pytest.mark.anyio
async def test_stream_opens_with_the_readiness_baseline() -> None:
    broker = sse.EventBroker()
    process_id = uuid.uuid4()
    subscription = broker.subscribe(process_id)
    opening = _event(
        event_type=SseEventType.STREAM_OPENED,
        process_id=process_id,
        readiness=PlanReadiness.NOT_READY,
        payload={"audience": "department_head"},
    )
    stream = sse.event_stream(
        subscription, audience=SseAudience.DEPARTMENT_HEAD, opening=opening
    )

    frame = await _next(stream)

    assert frame.startswith("event: stream.opened\n")
    assert json.loads(frame.split("data: ", 1)[1].strip())["readiness"] == "not_ready"
    await stream.aclose()


@pytest.mark.anyio
async def test_stream_relays_published_events_projected_to_the_tier() -> None:
    broker = sse.EventBroker()
    process_id = uuid.uuid4()
    subscription = broker.subscribe(process_id)
    stream = sse.event_stream(
        subscription,
        audience=SseAudience.SHARED_SCREEN,
        opening=_event(
            event_type=SseEventType.STREAM_OPENED, process_id=process_id, payload={}
        ),
    )
    await _next(stream)  # opening

    broker.publish(
        process_id=process_id,
        event_type=SseEventType.TEACHING_PLAN_STALE,
        readiness=PlanReadiness.RECALCULATION_REQUIRED,
        selection_blocked=True,
        payload={"status": "stale"},
    )

    frame = await _next(stream)

    assert frame.startswith("event: teaching_plan.stale\n")
    assert json.loads(frame.split("data: ", 1)[1].strip()) == {
        "readiness": "recalculation_required"
    }

    # A second event on the same open stream: the loop must come back around for
    # more rather than relaying one batch and stalling.
    broker.publish(
        process_id=process_id,
        event_type=SseEventType.ALLOCATION_REVISED,
        readiness=PlanReadiness.NOT_READY,
    )
    assert (await _next(stream)).startswith("event: allocation.revised\n")
    await stream.aclose()


@pytest.mark.anyio
async def test_stream_emits_a_gap_frame_before_the_surviving_events() -> None:
    broker = sse.EventBroker(buffer_size=1)
    process_id = uuid.uuid4()
    subscription = broker.subscribe(process_id)
    stream = sse.event_stream(
        subscription,
        audience=SseAudience.DEPARTMENT_HEAD,
        opening=_event(
            event_type=SseEventType.STREAM_OPENED, process_id=process_id, payload={}
        ),
    )
    await _next(stream)  # opening

    for _ in range(3):
        broker.publish(
            process_id=process_id,
            event_type=SseEventType.ALLOCATION_REVISED,
            readiness=PlanReadiness.NOT_READY,
        )

    gap = await _next(stream)
    assert gap.startswith("event: stream.gap\n")
    assert json.loads(gap.split("data: ", 1)[1].strip())["dropped"] == 2

    survivor = await _next(stream)
    assert survivor.startswith("event: allocation.revised\n")
    await stream.aclose()


@pytest.mark.anyio
async def test_stream_writes_a_keep_alive_when_idle() -> None:
    broker = sse.EventBroker()
    subscription = broker.subscribe(uuid.uuid4())
    stream = sse.event_stream(
        subscription,
        audience=SseAudience.DEPARTMENT_HEAD,
        opening=_event(event_type=SseEventType.STREAM_OPENED, payload={}),
        heartbeat_seconds=0.01,
    )
    await _next(stream)  # opening

    # Twice: an idle stream must keep beating, not fall out after one.
    assert await _next(stream) == ": keep-alive\n\n"
    assert await _next(stream) == ": keep-alive\n\n"

    # And it still delivers once traffic resumes.
    broker.publish(
        process_id=subscription.process_id,
        event_type=SseEventType.ALLOCATION_REVISED,
        readiness=PlanReadiness.NOT_READY,
    )
    assert (await _next(stream)).startswith("event: allocation.revised\n")
    await stream.aclose()


@pytest.mark.anyio
async def test_stream_unsubscribes_when_the_client_disconnects() -> None:
    broker = sse.EventBroker()
    process_id = uuid.uuid4()
    subscription = broker.subscribe(process_id)
    stream = sse.event_stream(
        subscription,
        audience=SseAudience.DEPARTMENT_HEAD,
        opening=_event(
            event_type=SseEventType.STREAM_OPENED, process_id=process_id, payload={}
        ),
    )
    await _next(stream)
    assert broker.subscriber_count(process_id) == 1

    await stream.aclose()  # what a disconnect triggers

    assert broker.subscriber_count(process_id) == 0
