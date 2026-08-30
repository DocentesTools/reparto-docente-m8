"""Per-process SSE stream route (plan §11, §20.25).

Mounted under the ``/assignment-processes/{process_id}/...`` namespace like every
other per-process resource, but kept in its own module: the stream is the only
long-lived, non-JSON endpoint in the service, and the only ``async def`` route —
it holds a connection open for the length of a meeting instead of returning.

The transport contract is in :mod:`reparto_service.services.sse`; this module is
only the HTTP edge: resolve the viewer's tier, subscribe, stream.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from reparto_service.app.deps import CurrentReader
from reparto_service.core.deps import engine
from reparto_service.enums import SseAudience, SseEventType
from reparto_service.schemas.events import DomainEvent
from reparto_service.services import sse
from reparto_service.services.read_scope import ensure_process_visible

router = APIRouter(prefix="/assignment-processes", tags=["assignment-processes"])


@router.get("/{process_id}/events", response_class=StreamingResponse)
async def stream_process_events(
    current_user: CurrentReader,
    process_id: uuid.UUID,
    audience: Optional[SseAudience] = Query(
        default=None,
        description=(
            "Request a less privileged payload tier than the caller's role "
            "grants — a shared projection screen asks for `shared_screen` so it "
            "never receives identifiers. Asking for a more privileged tier is a "
            "403. Defaults to the tier the role grants."
        ),
    ),
) -> StreamingResponse:
    """Stream this process's domain events as SSE, projected to the viewer's tier.

    Any caller holding at least ``READER`` may subscribe; what they *receive* is
    decided by :func:`~reparto_service.services.sse.resolve_audience`, not by
    the request.
    The stream opens with a ``stream.opened`` frame carrying the current plan
    readiness, so a client needs no separate fetch to render its initial state.
    """
    # A StreamingResponse only "completes" when the meeting ends, so a
    # yield-dependency session would stay checked out for the stream's whole
    # life — one pool slot per subscriber. Scope it to the reads instead: the
    # generator below takes no session and relays in-memory broker events only.
    with engine.session() as session:
        ensure_process_visible(session, current_user, process_id)
        resolved = sse.resolve_audience(current_user, audience)
        participant_id = sse.viewer_participant_id(session, process_id, current_user)
        readiness, selection_blocked = sse.current_readiness(session, process_id)

        # Subscribe *before* rendering the baseline so a change committing
        # between the two is buffered rather than lost: a client may see it
        # twice (the baseline already reflects it), never zero times. Keep
        # this inside the session block — moving it out widens that window
        # for no benefit.
        subscription = sse.event_broker.subscribe(process_id)

    opening = DomainEvent(
        event_type=SseEventType.STREAM_OPENED,
        process_id=process_id,
        sequence=0,
        occurred_at=datetime.now(tz=timezone.utc),
        readiness=readiness,
        selection_blocked=selection_blocked,
        payload={"audience": resolved.value},
    )
    return StreamingResponse(
        sse.event_stream(
            subscription,
            audience=resolved,
            opening=opening,
            viewer_process_teacher_id=participant_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
