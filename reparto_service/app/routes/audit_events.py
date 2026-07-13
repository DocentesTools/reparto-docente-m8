"""Audit-event routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import SessionDep
from reparto_service.controllers.audit_events import AuditEventController
from reparto_service.db_models.audit_events import AuditEventsPublic

router = APIRouter(
    prefix="/assignment-processes/{process_id}/audit-events",
    tags=["audit-events"],
)


@router.get("/", response_model=AuditEventsPublic)
def list_audit_events(session: SessionDep, process_id: uuid.UUID) -> AuditEventsPublic:
    return AuditEventController.list_events(session, process_id)
