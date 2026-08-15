"""Controller-level helpers shared by every reparto domain resource."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import HTTPException, status
from fastapi_m8 import BaseController, RoleType, UserModel, has_minimum_role
from sqlmodel import Session, SQLModel, select

from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.audit_events import AuditEvent
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.enums import (
    AssignmentProcessStatus,
    AuditEventType,
    FeasibilityStatus,
    SseEventType,
)
from reparto_service.schemas.events import DomainEvent
from reparto_service.services.sse import publish_domain_event

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


class DomainController(BaseController):
    """Common domain helpers layered on top of ``fastapi_m8``'s ``BaseController``.

    Provides:

    * the ownership resolvers backing "own records only" (``linked_process_
      teacher``, ``require_own_teacher_profile``) and the two role predicates
      they build on (``require_writer``, ``require_department_head``),
    * lookup-or-404 helpers for every owned parent (process, teacher profile, etc.),
    * a ``ensure_process_mutable`` guard that every child resource
      controller calls before a write, enforcing plan §8.4's
      "final process is immutable" rule.

    Route-level role gating lives in the SDK dependencies the routes declare
    (``CurrentReader``/``CurrentWriter``/``CurrentAdmin``, plan §21.6), not
    here. What remains are the checks a *dependency* cannot express, because
    they need the row: is this the caller's own participation, their own
    profile, and — for the SSE projection — would this caller be allowed to act
    as department head at all.

    Every role decision goes through the SDK's ``has_minimum_role``. Nothing
    here inspects ``is_superuser``: the SDK's truth table makes the flag
    equivalent to ``role == SUPERADMIN``, so consulting it separately could
    only ever create a second, divergent answer (``AUTH-INV-01``).
    """

    @staticmethod
    def _require_role(current_user: UserModel, required: RoleType, detail: str) -> None:
        if not has_minimum_role(current_user.role, required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=detail,
            )

    @staticmethod
    def require_writer(current_user: UserModel) -> None:
        """Raise 403 unless the caller may mutate its **own** records (§21.3).

        This is the floor for a self-service action — claiming a slot in one's
        own turn, editing one's own profile. It is never sufficient on its own
        for process or planning data: those call
        :meth:`require_department_head`, and the ownership of a self-service
        action is proven separately by the resolvers below.
        """
        DomainController._require_role(
            current_user,
            RoleType.WRITER,
            "Writer role required to mutate your own records.",
        )

    @staticmethod
    def require_department_head(current_user: UserModel) -> None:
        """Raise 403 unless the caller may act as department head (§21.2).

        Department-head authorization is the caller's own canonical role —
        ``ADMIN`` or ``SUPERADMIN`` — and nothing else.
        ``Department.department_head_user_id`` is descriptive metadata: it
        records *who* heads a department for attribution and UI defaults, and
        deliberately no longer grants capability, because a binding is not a
        credential and cannot be revoked by demoting the account.
        """
        DomainController._require_role(
            current_user,
            RoleType.ADMIN,
            "Department-head role (admin) required to mutate this resource.",
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
    def linked_process_teacher(
        session: Session, process_id: uuid.UUID, current_user: UserModel
    ) -> ProcessTeacher:
        """Return the caller's own participation row in *process_id*, or 404.

        The single ownership resolver for "own records only": a participation
        row is the caller's when the process teacher points at a teacher
        profile whose ``user_id`` is the caller's own auth id. A caller with no
        linked profile owns nothing here, which is a 404 rather than a 403 —
        the caller is authorized to act on their own record; there simply is no
        such record in this process.
        """
        statement = (
            select(ProcessTeacher, TeacherProfile)
            .where(ProcessTeacher.assignment_process_id == process_id)
            .where(ProcessTeacher.teacher_profile_id == TeacherProfile.id)
            .where(TeacherProfile.user_id == uuid.UUID(str(current_user.id)))
        )
        row = session.exec(statement).first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No teacher profile is linked to this auth user.",
            )
        process_teacher, _ = row
        return process_teacher

    @staticmethod
    def require_own_process_teacher(
        session: Session,
        current_user: UserModel,
        process_id: uuid.UUID,
        process_teacher_id: uuid.UUID,
    ) -> None:
        """Authorize an action on *process_teacher_id* (§21.3 own-data).

        A department head may act on any participant; anyone else must be
        acting on their own participation row and hold at least ``WRITER``.
        """
        if has_minimum_role(current_user.role, RoleType.ADMIN):
            return
        DomainController.require_writer(current_user)
        own = DomainController.linked_process_teacher(session, process_id, current_user)
        if own.id != process_teacher_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You may only act on your own participation in this process.",
            )

    @staticmethod
    def require_own_teacher_profile(
        session: Session, current_user: UserModel, profile_id: uuid.UUID
    ) -> None:
        """Authorize an update of *profile_id* (§21.3 own-data).

        A department head may update any profile; anyone else must hold at
        least ``WRITER`` and be the account the profile is linked to. The
        linkage itself is not editable this way — the route narrows the
        accepted fields — so a caller can never re-point a profile at
        somebody else and inherit their participation.
        """
        if has_minimum_role(current_user.role, RoleType.ADMIN):
            return
        DomainController.require_writer(current_user)
        profile = DomainController.get_or_404(session, TeacherProfile, profile_id)
        if profile.user_id != uuid.UUID(str(current_user.id)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You may only update your own teacher profile.",
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

        The counterpart to :meth:`record_audit_event`, and its mirror image: an
        audit row is part of the transaction and must roll back with it, whereas
        an event announces a change that already happened, so this is called
        **after** ``session.commit()`` and never raises. The whole behaviour
        lives with the broker in :func:`~reparto_service.services.sse.publish_domain_event`,
        because the services layer emits through it too (feasibility, §20.25).
        """
        return publish_domain_event(
            session,
            process_id=process_id,
            event_type=event_type,
            payload=payload,
            subject_process_teacher_id=subject_process_teacher_id,
        )

    @staticmethod
    def publish_feasibility_invalidated(
        session: Session, process_id: uuid.UUID
    ) -> DomainEvent | None:
        """Announce that a committed input change dropped the stored result (§20.25).

        Emitted only where :meth:`FeasibilityWitnessService.invalidate` actually
        discarded an evaluation, so a subscriber never sees a transition that did
        not happen. The payload names the resulting status and nothing else: the
        witness and the individualized diagnostics are administration-only, and
        the mutation that caused the invalidation publishes its own event with
        its own payload.
        """
        return publish_domain_event(
            session,
            process_id=process_id,
            event_type=SseEventType.TEACHING_PLAN_FEASIBILITY_INVALIDATED,
            payload={"feasibility_status": FeasibilityStatus.NOT_EVALUATED.value},
        )

    @staticmethod
    def _audit_payload(row: SQLModel | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return row.model_dump(mode="json")
