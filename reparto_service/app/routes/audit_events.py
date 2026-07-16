"""Audit-event routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from reparto_service.app.deps import SessionDep
from reparto_service.controllers.audit_events import AuditEventController
from reparto_service.db_models.audit_events import AuditEventsPublic
from reparto_service.enums import AuditEventType

router = APIRouter(
    prefix="/assignment-processes/{process_id}/audit-events",
    tags=["audit-events"],
)


@router.get("/", response_model=AuditEventsPublic)
def list_audit_events(
    session: SessionDep,
    process_id: uuid.UUID,
    event_type: AuditEventType | None = Query(
        default=None, description="Filter to a single registered audit event type."
    ),
    entity_type: str | None = Query(
        default=None, description="Filter to a single mutated entity type."
    ),
) -> AuditEventsPublic:
    return AuditEventController.list_events(
        session,
        process_id,
        event_type=event_type,
        entity_type=entity_type,
    )
