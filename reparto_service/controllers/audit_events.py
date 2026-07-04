"""Audit-event read controller."""

from __future__ import annotations

import uuid

from sqlmodel import Session, col, select

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.audit_events import (
    AuditEvent,
    AuditEventPublic,
    AuditEventsPublic,
)


class AuditEventController(DomainController):
    """Read access to process-scoped audit events."""

    @staticmethod
    def list_events(session: Session, process_id: uuid.UUID) -> AuditEventsPublic:
        DomainController.get_process_or_404(session, process_id)
        rows = list(
            session.exec(
                select(AuditEvent)
                .where(AuditEvent.assignment_process_id == process_id)
                .order_by(col(AuditEvent.created_at), col(AuditEvent.id))
            ).all()
        )
        return AuditEventsPublic(
            data=[AuditEventPublic.model_validate(row) for row in rows],
            count=len(rows),
        )


__all__ = ["AuditEventController"]
