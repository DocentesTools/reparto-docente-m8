"""In-process SSE domain event broker and role-safe projection (plan §11, §20.25).

This module is the whole outbound event path. It has three parts:

* :class:`EventBroker` — a per-process (topic = assignment process) publish /
  subscribe fan-out. ``reparto-docente-m8`` is a local-first single-instance LAN
  service, so an in-memory broker is the whole transport: there is no second
  replica to fan out to, and an event outlives its usefulness in seconds. The
  stream is therefore **best effort** — exactly the contract
  :mod:`reparto_service.core.events` documents for the inbound auth stream. A
  subscriber that cannot keep up drops events and is told so with a
  ``stream.gap`` frame; the authoritative state always remains the database, and
  every payload is reproducible from a plain GET. Nothing in the domain depends
  on an event being delivered.

* :func:`resolve_audience` / :func:`project_event` — the plan §11 rule that
  "every event payload must use LAN-safe response schemas appropriate to the
  viewer role", sharpened by §20.25 into three tiers. A
  :class:`~reparto_service.schemas.events.DomainEvent` is built once, in its full
  department-head form, and redacted at the edge per subscriber. Projection is
  the *only* place a payload is narrowed, so a leak is impossible to introduce
  from an emit site: a controller cannot publish "to teachers", it just
  publishes, and the tier decides.

* :func:`event_stream` — the async generator that turns a subscription into SSE
  frames, with a heartbeat so a LAN proxy does not reap an idle connection.

Feasibility events (``teaching_plan.feasibility_updated`` /
``feasibility_invalidated``, plan §20.25) are deliberately absent: they belong to
the §20.20 feasibility solver task, which has no driver yet. They will publish
through this same broker and tier projection when it lands.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import threading
import uuid
from collections import deque
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlmodel import Session, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.core.decimals import quantize_hours
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import PlanReadiness, SseAudience, SseEventType
from reparto_service.schemas.events import DomainEvent
from reparto_service.services.lifecycle_gates import (
    ASSIGNMENT_BLOCKING_PLAN_STATUSES,
    ASSIGNMENT_READY_PLAN_STATUSES,
)

logger = logging.getLogger(__name__)

#: Events buffered per subscriber before the oldest is dropped and the client is
#: told to refetch. Sized for a slow LAN reader over a burst (a bulk apply, a
#: generation), not for durability — the database is the source of truth.
DEFAULT_BUFFER_SIZE = 64

#: Seconds of silence before a keep-alive comment is written, so an idle stream
#: is not reaped by an intermediary.
DEFAULT_HEARTBEAT_SECONDS = 15.0

#: Audience privilege ranking (lower = more privileged). Used to enforce that a
#: caller may only ever ask for *less* than their role grants.
_AUDIENCE_RANK: dict[SseAudience, int] = {
    SseAudience.DEPARTMENT_HEAD: 0,
    SseAudience.TEACHER: 1,
    SseAudience.SHARED_SCREEN: 2,
}


# ── Payload helpers ───────────────────────────────────────────────────────────


def hours_string(value: float | Decimal) -> str:
    """Render a stored hour figure as its canonical two-place string (plan §3.9).

    The hour columns are still ``float`` today (the ``Decimal`` sweep is its own
    later task), so an emit site must not drop a raw binary float into a payload:
    a client would receive ``17.399999999999999``. This is the output-side
    counterpart of the calculation schemas' hour field — lenient and rounding,
    because it renders a value the domain already accepted.
    """
    return str(quantize_hours(Decimal(str(value))))


# ── Readiness projection (plan §20.25) ────────────────────────────────────────


def current_readiness(
    session: Session, process_id: uuid.UUID
) -> tuple[PlanReadiness, bool]:
    """Return the process's coarse plan readiness and whether selection is blocked.

    Reuses the :mod:`reparto_service.services.lifecycle_gates` status sets so the
    readiness a viewer is *shown* can never disagree with the gate that decides
    what the viewer may actually *do*. A process with no plan yet is
    ``NOT_READY``.
    """
    plan = session.exec(
        select(TeachingPlan).where(TeachingPlan.assignment_process_id == process_id)
    ).first()
    if plan is None:
        return PlanReadiness.NOT_READY, False
    if plan.status in ASSIGNMENT_BLOCKING_PLAN_STATUSES:
        return PlanReadiness.RECALCULATION_REQUIRED, True
    if plan.status in ASSIGNMENT_READY_PLAN_STATUSES:
        return PlanReadiness.READY, False
    return PlanReadiness.NOT_READY, False


# ── Audience resolution (plan §11, §20.25) ────────────────────────────────────


def granted_audience(
    session: Session, process_id: uuid.UUID, current_user: UserModel
) -> SseAudience:
    """Return the highest tier ``current_user``'s role grants on this process.

    Anyone who may mutate the process — a platform writer/admin/superuser, or
    the auth user bound as the department head — sees the full payload. Every
    other authenticated caller is a teacher.
    """
    from reparto_service.controllers.base import DomainController  # noqa: PLC0415

    try:
        DomainController.require_process_writer(session, current_user, process_id)
    except HTTPException:
        return SseAudience.TEACHER
    return SseAudience.DEPARTMENT_HEAD


def resolve_audience(
    session: Session,
    process_id: uuid.UUID,
    current_user: UserModel,
    requested: Optional[SseAudience] = None,
) -> SseAudience:
    """Resolve the tier for this subscriber, refusing an upgrade.

    A caller may explicitly ask for a *less* privileged tier than their role
    grants — a shared projection screen authenticates as an ordinary user and
    asks for ``shared_screen`` so it never receives identifiers it would display
    to a room. Asking for a *more* privileged tier is a 403: silently clamping a
    privilege request hides a misconfigured client.
    """
    granted = granted_audience(session, process_id, current_user)
    if requested is None:
        return granted
    if _AUDIENCE_RANK[requested] < _AUDIENCE_RANK[granted]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Cannot subscribe as {requested.value}: this role grants at most "
                f"{granted.value}."
            ),
        )
    return requested


def viewer_participant_id(
    session: Session, process_id: uuid.UUID, current_user: UserModel
) -> uuid.UUID | None:
    """Return the caller's own ``ProcessTeacher`` id in this process, if any.

    Used by :func:`project_event` to decide whether a participant-scoped event is
    about the viewer themselves — the one case where the teacher tier may see
    hour figures (plan §20.25: a teacher never receives *another* teacher's
    target). Returns ``None`` for a caller with no linked participant row (a
    department head, an observer, a shared screen).
    """
    row = session.exec(
        select(ProcessTeacher.id)
        .where(ProcessTeacher.assignment_process_id == process_id)
        .where(ProcessTeacher.teacher_profile_id == TeacherProfile.id)
        .where(TeacherProfile.user_id == uuid.UUID(str(current_user.id)))
    ).first()
    return row


# ── Payload projection (plan §11, §20.25) ─────────────────────────────────────


def project_event(
    event: DomainEvent,
    audience: SseAudience,
    viewer_process_teacher_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Redact ``event`` down to what ``audience`` may see.

    * ``department_head`` — the event verbatim.
    * ``teacher`` — readiness, whether selection is blocked, and the event's own
      identity; the payload only when the event is about *this* teacher.
    * ``shared_screen`` — readiness alone: ready / not ready / recalculation
      required, with no identifier, hour figure or plan-stage detail. The frame's
      ``event:`` name is retained (it names a kind of change, never a subject),
      which is what lets a screen animate an update without learning anything
      about it.
    """
    if audience == SseAudience.DEPARTMENT_HEAD:
        return event.model_dump(mode="json")

    if audience == SseAudience.SHARED_SCREEN:
        return {"readiness": event.readiness.value}

    projected: dict[str, Any] = {
        "event_type": event.event_type.value,
        "process_id": str(event.process_id),
        "sequence": event.sequence,
        "occurred_at": event.occurred_at.isoformat(),
        "readiness": event.readiness.value,
        "selection_blocked": event.selection_blocked,
    }
    is_own = (
        event.subject_process_teacher_id is not None
        and event.subject_process_teacher_id == viewer_process_teacher_id
    )
    if is_own:
        projected["process_teacher_id"] = str(event.subject_process_teacher_id)
        projected["payload"] = event.payload
    return projected


# ── SSE framing ───────────────────────────────────────────────────────────────


def format_frame(event_type: str, data: dict[str, Any]) -> str:
    """Render one SSE frame. ``data`` is emitted as a single-line JSON object."""
    return f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"


def format_comment(text: str) -> str:
    """Render an SSE comment line (a heartbeat; ignored by every client)."""
    return f": {text}\n\n"


# ── Broker ────────────────────────────────────────────────────────────────────


class Subscription:
    """One connected viewer's buffered feed of a single process's events.

    Read on the event loop, but fed by :meth:`EventBroker.publish` running in a
    worker thread (the sync route handlers execute in FastAPI's threadpool), so
    every cross-thread touch point is explicit: the buffer is a ``deque``, whose
    append and ``maxlen`` eviction are atomic, and the wakeup is delivered with
    ``call_soon_threadsafe``.

    The reader's loop is captured on the first :meth:`wait`, not at construction,
    so a subscription can be created off the loop entirely — the publisher only
    ever needs a loop to *wake* a reader that is already asleep.
    """

    def __init__(
        self,
        broker: "EventBroker",
        process_id: uuid.UUID,
        *,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
    ) -> None:
        self._broker = broker
        self.process_id = process_id
        self._buffer: deque[DomainEvent] = deque(maxlen=buffer_size)
        self._wakeup = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dropped = 0

    def offer(self, event: DomainEvent) -> None:
        """Buffer ``event`` and wake the reader. Never raises, never blocks."""
        if len(self._buffer) == self._buffer.maxlen:
            # The deque drops the oldest for us; record that continuity broke so
            # the reader can emit a gap frame instead of silently skipping.
            self._dropped += 1
        self._buffer.append(event)
        loop = self._loop
        if loop is None:
            # Nobody is asleep on this subscription yet; the buffer is enough,
            # because wait() checks it before it ever suspends.
            return
        try:
            loop.call_soon_threadsafe(self._wakeup.set)
        except RuntimeError:  # pragma: no cover - loop closed under a shutdown race
            logger.debug("sse subscription wakeup on a closed loop; dropping signal")

    def drain(self) -> tuple[list[DomainEvent], int]:
        """Return and clear the buffered events plus the drop count since the last drain."""
        dropped, self._dropped = self._dropped, 0
        events = list(self._buffer)
        self._buffer.clear()
        return events, dropped

    async def wait(self, timeout: float) -> bool:
        """Block until an event arrives. ``False`` when ``timeout`` elapsed first.

        Checking the buffer before suspending is what closes the publish/subscribe
        race: an event offered before the loop was captured, or between the check
        and the suspend, is either already visible here or delivers its wakeup
        through ``call_soon_threadsafe``. It cannot fall between the two.
        """
        self._loop = asyncio.get_running_loop()
        if self._buffer:
            self._wakeup.clear()
            return True
        try:
            await asyncio.wait_for(self._wakeup.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        self._wakeup.clear()
        return True

    def close(self) -> None:
        """Detach from the broker. Idempotent."""
        self._broker.unsubscribe(self)


class EventBroker:
    """Thread-safe in-memory fan-out of domain events, keyed by process."""

    def __init__(self, *, buffer_size: int = DEFAULT_BUFFER_SIZE) -> None:
        self._buffer_size = buffer_size
        self._subscribers: dict[uuid.UUID, set[Subscription]] = {}
        self._lock = threading.Lock()
        self._sequence = itertools.count(1)

    def subscribe(self, process_id: uuid.UUID) -> Subscription:
        """Register a subscription for ``process_id``."""
        subscription = Subscription(self, process_id, buffer_size=self._buffer_size)
        with self._lock:
            self._subscribers.setdefault(process_id, set()).add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        """Remove ``subscription``; drop the topic once its last reader leaves."""
        with self._lock:
            readers = self._subscribers.get(subscription.process_id)
            if readers is None:
                return
            readers.discard(subscription)
            if not readers:
                del self._subscribers[subscription.process_id]

    def subscriber_count(self, process_id: uuid.UUID) -> int:
        """Return how many readers are attached to ``process_id`` (diagnostics/tests)."""
        with self._lock:
            return len(self._subscribers.get(process_id, ()))

    def publish(
        self,
        *,
        process_id: uuid.UUID,
        event_type: SseEventType,
        readiness: PlanReadiness,
        selection_blocked: bool = False,
        payload: dict[str, Any] | None = None,
        subject_process_teacher_id: uuid.UUID | None = None,
    ) -> DomainEvent:
        """Stamp and fan out one event. Safe to call from any thread.

        Returns the built event (whether or not anyone was listening) so an emit
        site can be asserted on without a subscriber.
        """
        event = DomainEvent(
            event_type=event_type,
            process_id=process_id,
            sequence=next(self._sequence),
            occurred_at=datetime.now(tz=timezone.utc),
            readiness=readiness,
            selection_blocked=selection_blocked,
            payload=payload or {},
            subject_process_teacher_id=subject_process_teacher_id,
        )
        with self._lock:
            readers = list(self._subscribers.get(process_id, ()))
        for subscription in readers:
            subscription.offer(event)
        return event


#: Process-wide broker. Built once, like the auth/db deps in
#: :mod:`reparto_service.core.deps` — the emit sites and the stream route must
#: share one instance or events would fan out into nothing.
event_broker = EventBroker()


# ── Stream generator ──────────────────────────────────────────────────────────


async def event_stream(
    subscription: Subscription,
    *,
    audience: SseAudience,
    opening: DomainEvent,
    viewer_process_teacher_id: uuid.UUID | None = None,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
) -> AsyncGenerator[str, None]:
    """Yield SSE frames for ``subscription`` until the client disconnects.

    Opens with the ``stream.opened`` baseline so a client knows the current
    readiness without a second request, then relays projected events, a
    ``stream.gap`` frame whenever the buffer overflowed, and a keep-alive comment
    on an idle heartbeat. The subscription is always detached on the way out,
    including on the cancellation a disconnect raises.
    """
    try:
        yield format_frame(
            SseEventType.STREAM_OPENED.value,
            project_event(opening, audience, viewer_process_teacher_id),
        )
        while True:
            if not await subscription.wait(heartbeat_seconds):
                yield format_comment("keep-alive")
                continue
            events, dropped = subscription.drain()
            if dropped:
                yield format_frame(
                    SseEventType.STREAM_GAP.value,
                    {
                        "dropped": dropped,
                        "detail": "refetch; buffered events were lost",
                    },
                )
            for event in events:
                yield format_frame(
                    event.event_type.value,
                    project_event(event, audience, viewer_process_teacher_id),
                )
    finally:
        subscription.close()


__all__ = [
    "DEFAULT_BUFFER_SIZE",
    "DEFAULT_HEARTBEAT_SECONDS",
    "EventBroker",
    "Subscription",
    "current_readiness",
    "event_broker",
    "event_stream",
    "format_comment",
    "format_frame",
    "granted_audience",
    "hours_string",
    "project_event",
    "resolve_audience",
    "viewer_participant_id",
]
