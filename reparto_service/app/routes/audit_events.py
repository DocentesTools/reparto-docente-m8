"""Audit-event routes.

The trail is a department-head read (remediation `W7.1`). An audit event is
stored with the payload the mutation carried, and the extra-hours event is
composed in
:meth:`reparto_service.controllers.process_teachers.ProcessTeacherController`
with ``reason`` — the head's written justification — beside the participant's
base, extra and target weekly hours and their overload flag.
:data:`reparto_service.services.sse.DEPARTMENT_HEAD_ONLY_PAYLOAD_KEYS` withholds
that key from a teacher on the live stream even when the event is about the
teacher themselves, so the stored record of the same event cannot be readable
by every participant of the department after the fact.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from reparto_service.app.deps import (
    CurrentAdmin,
    SessionDep,
    require_visible_process,
)
from reparto_service.controllers.audit_events import AuditEventController
from reparto_service.db_models.audit_events import AuditEventsPublic
from reparto_service.enums import AuditEventType

router = APIRouter(
    prefix="/assignment-processes/{process_id}/audit-events",
    tags=["audit-events"],
    # Read scope (plan §21.4): every route under this process — read and
    # mutation alike — is refused with 404 when the process lies outside the
    # caller's departments.
    dependencies=[Depends(require_visible_process)],
)


@router.get("/", response_model=AuditEventsPublic)
def list_audit_events(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    event_type: AuditEventType | None = Query(
        default=None, description="Filter to a single registered audit event type."
    ),
    entity_type: str | None = Query(
        default=None, description="Filter to a single mutated entity type."
    ),
) -> AuditEventsPublic:
    """Serve the stored trail only to an administrator (`W7.1`)."""
    return AuditEventController.list_events(
        session,
        process_id,
        event_type=event_type,
        entity_type=entity_type,
    )
