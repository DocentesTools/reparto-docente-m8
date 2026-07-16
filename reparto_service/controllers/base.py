"""Controller-level helpers shared by every reparto domain resource."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlmodel import Session, select

from auth_sdk_m8.controllers.base import BaseController
from auth_sdk_m8.schemas.user import UserModel
from sqlmodel import SQLModel

from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.audit_events import AuditEvent
from reparto_service.db_models.departments import Department
from reparto_service.enums import AssignmentProcessStatus, AuditEventType, SseEventType
from reparto_service.schemas.events import DomainEvent
from reparto_service.services.sse import current_readiness, event_broker

logger = logging.getLogger(__name__)

# Child resources cannot be mutated when the parent process is in one of
# these statuses. ``final`` is locked by plan §8.4; ``archived`` is the
# terminal status and the lifecycle service refuses any edge out of it.
_IMMUTABLE_PROCESS_STATUSES: frozenset[AssignmentProcessStatus] = frozenset(
    {
        AssignmentProcessStatus.FINAL,
        AssignmentProcessStatus.ARCHIVED,
    }
)
_MUTATION_ROLES: frozenset[str] = frozenset(
    {
        "superadmin",
        "admin",
        "writer",
    }
)


class DomainController(BaseController):
    """Common domain helpers layered on top of ``auth_sdk_m8``'s ``BaseController``.

    Provides:

    * a "must mutate" permission helper (superuser or above the reader role),
    * lookup-or-404 helpers for every owned parent (process, teacher profile, etc.),
    * a ``ensure_process_mutable`` guard that every child resource
      controller calls before a write, enforcing plan §8.4's
      "final process is immutable" rule.
    """

    @staticmethod
    def require_writer(current_user: UserModel) -> None:
        """Raise 403 unless the caller may mutate the domain.

        Writer role or superuser are accepted. ``reader`` and ``user`` roles
        can still call GET endpoints — they are blocked only on POST/PATCH/DELETE.
        """
        if current_user.is_superuser:
            return
        role = current_user.role
        role_value = role.value if hasattr(role, "value") else str(role)
        if role_value in _MUTATION_ROLES:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Department-head role required to mutate this resource.",
        )

    @staticmethod
    def require_admin(current_user: UserModel) -> None:
        """Raise 403 unless the caller has the existing admin role."""
        role = current_user.role
        role_value = role.value if hasattr(role, "value") else str(role)
        if current_user.is_superuser or role_value in {"admin", "superadmin"}:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator role required to mutate classroom stages.",
        )

    @staticmethod
    def get_or_404(session: Session, model: type[SQLModel], item_id: uuid.UUID) -> Any:
        """Return the row with ``item_id`` or raise a 404."""
        item = session.get(model, item_id)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{model.__name__} {item_id} not found.",
            )
        return item

    @staticmethod
    def require_process_writer(
        session: Session, current_user: UserModel, process_id: uuid.UUID
    ) -> None:
        """Raise 403 unless the caller can mutate this process.

        Platform writer/admin roles keep broad setup access. A regular auth
        user can also mutate the process when the process department explicitly
        binds them as ``department_head_user_id``.
        """
        try:
            DomainController.require_writer(current_user)
            return
        except HTTPException:
            pass
        process = DomainController.get_process_or_404(session, process_id)
        department = session.get(Department, process.department_id)
        if department is not None and department.department_head_user_id == uuid.UUID(
            str(current_user.id)
        ):
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Department-head role required to mutate this process.",
        )

    @staticmethod
    def get_process_or_404(
        session: Session, process_id: uuid.UUID
    ) -> AssignmentProcess:
        """Return the process with ``process_id`` or raise a 404."""
        statement = select(AssignmentProcess).where(AssignmentProcess.id == process_id)
        process = session.exec(statement).first()
        if process is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"AssignmentProcess {process_id} not found.",
            )
        return process

    @staticmethod
    def ensure_process_mutable(process: AssignmentProcess) -> AssignmentProcess:
        """Raise 400 when the process is in a non-mutable status.

        The check is centralised here so every child resource controller
        enforces plan §8.4's immutability rule with one rule of thumb.
        """
        if process.status in _IMMUTABLE_PROCESS_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot mutate a process in status {process.status.value}; "
                    "reopen it first."
                ),
            )
        return process

    @staticmethod
    def record_audit_event(
        session: Session,
        *,
        process_id: uuid.UUID,
        current_user: UserModel,
        event_type: AuditEventType | str,
        entity_type: str,
        entity_id: uuid.UUID | None,
        before: SQLModel | None,
        after: SQLModel | None,
        reason: str | None = None,
    ) -> AuditEvent:
        """Add a domain audit event to the current transaction.

        ``event_type`` should be an :class:`~reparto_service.enums.AuditEventType`
        registry member; a raw string is still accepted for the few dynamic
        callers. It is normalised to the canonical string value before storage
        so the persisted trail is identical either way.
        """
        role = current_user.role
        role_value = role.value if hasattr(role, "value") else str(role)
        event = AuditEvent(
            assignment_process_id=process_id,
            actor_user_id=uuid.UUID(str(current_user.id)),
            actor_role=role_value,
            event_type=(
                event_type.value
                if isinstance(event_type, AuditEventType)
                else event_type
            ),
            entity_type=entity_type,
            entity_id=entity_id,
            before_json=DomainController._audit_payload(before),
            after_json=DomainController._audit_payload(after),
            reason=reason,
        )
        session.add(event)
        return event

    @staticmethod
    def publish_event(
        session: Session,
        *,
        process_id: uuid.UUID,
        event_type: SseEventType,
        payload: dict[str, Any] | None = None,
        subject_process_teacher_id: uuid.UUID | None = None,
    ) -> DomainEvent | None:
        """Fan one committed change out to the SSE subscribers (plan §11).

        The counterpart to :meth:`record_audit_event`, and its mirror image in
        two ways. It is called **after** ``session.commit()``, never before: an
        audit row is part of the transaction and must roll back with it, whereas
        an event announces a change that already happened — publishing inside the
        transaction would advertise a state a rollback could still erase.

        And it never raises. A failed audit write must fail the request; a failed
        broadcast must not, because the write already succeeded and the stream is
        explicitly best-effort (a viewer converges on the next event, gap frame or
        refetch — see :mod:`reparto_service.services.sse`). Returns the published
        event, or ``None`` if publishing failed.

        The plan readiness carried to the teacher/shared-screen tiers is read
        here, once, rather than at each emit site, so no caller can publish a
        readiness that disagrees with the committed plan status.
        """
        try:
            readiness, selection_blocked = current_readiness(session, process_id)
            return event_broker.publish(
                process_id=process_id,
                event_type=event_type,
                readiness=readiness,
                selection_blocked=selection_blocked,
                payload=payload,
                subject_process_teacher_id=subject_process_teacher_id,
            )
        except Exception:
            logger.exception(
                "sse publish failed event_type=%s process_id=%s",
                event_type.value,
                process_id,
            )
            return None

    @staticmethod
    def _audit_payload(row: SQLModel | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return row.model_dump(mode="json")
