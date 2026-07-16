"""Schemas for the per-process SSE domain event stream (plan §11, §20.25).

A :class:`DomainEvent` is the *internal, unprojected* record of one streamable
change: it always carries the full department-head view. Nothing in this module
is a response schema — an event only reaches the wire through
:func:`reparto_service.services.sse.project_event`, which redacts it down to the
viewer's :class:`~reparto_service.enums.SseAudience` tier. Keeping the full
payload internal and projecting at the edge means a new subscriber tier is a
projection change, never a re-plumbing of every emit site.

Hour figures inside :attr:`DomainEvent.payload` follow the plan §3.9 canonical
two-place decimal-string convention, exactly like the calculation schemas: the
emit sites quantize before publishing, so a payload never carries a raw binary
float onto the wire.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from reparto_service.enums import PlanReadiness, SseEventType


class DomainEvent(BaseModel):
    """One streamable domain change, in its full (department-head) form."""

    model_config = ConfigDict(frozen=True)

    event_type: SseEventType = Field(description="Registry event type (plan §11).")
    process_id: uuid.UUID = Field(
        description="Assignment process this event belongs to; the stream topic."
    )
    sequence: int = Field(
        description=(
            "Monotonic per-broker sequence number. Lets a client detect that it "
            "missed events even if a gap frame was itself dropped."
        )
    )
    occurred_at: datetime = Field(description="UTC instant the change committed.")
    readiness: PlanReadiness = Field(
        description=(
            "Plan readiness after the change — the only plan detail the teacher "
            "and shared-screen tiers ever receive (plan §20.25)."
        )
    )
    selection_blocked: bool = Field(
        default=False,
        description=(
            "True while the plan state stops teachers from selecting; the "
            "teacher tier's actionable signal (plan §20.25)."
        ),
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Full department-head payload. Redacted entirely for the teacher and "
            "shared-screen tiers except for a viewer's own participant figures."
        ),
    )
    subject_process_teacher_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Participant this event is *about*, when it is about exactly one. "
            "Drives the plan §20.25 rule that a teacher may see their own hours "
            "but never another participant's."
        ),
    )


__all__ = ["DomainEvent"]
