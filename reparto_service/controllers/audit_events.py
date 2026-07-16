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
from reparto_service.enums import AuditEventType


class AuditEventController(DomainController):
    """Read access to process-scoped audit events."""

    @staticmethod
    def list_events(
        session: Session,
        process_id: uuid.UUID,
        *,
        event_type: AuditEventType | None = None,
        entity_type: str | None = None,
    ) -> AuditEventsPublic:
        """List a process's audit trail, oldest first.

        Optional ``event_type`` / ``entity_type`` filters narrow the trail so an
        auditor can pull just one kind of change (e.g. every
        ``allocation.revised`` or every ``teaching_plan`` event) without paging
        the whole history (plan §13.1 "Extend audit events"). ``event_type`` is
        validated against the :class:`~reparto_service.enums.AuditEventType`
        registry at the request boundary.
        """
        DomainController.get_process_or_404(session, process_id)
        statement = select(AuditEvent).where(
            AuditEvent.assignment_process_id == process_id
        )
        if event_type is not None:
            statement = statement.where(AuditEvent.event_type == event_type.value)
        if entity_type is not None:
            statement = statement.where(AuditEvent.entity_type == entity_type)
        rows = list(
            session.exec(
                statement.order_by(col(AuditEvent.created_at), col(AuditEvent.id))
            ).all()
        )
        return AuditEventsPublic(
            data=[AuditEventPublic.model_validate(row) for row in rows],
            count=len(rows),
        )


__all__ = ["AuditEventController"]
