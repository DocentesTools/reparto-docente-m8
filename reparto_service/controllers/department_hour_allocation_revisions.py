"""DepartmentHourAllocationRevision controller.

Owns the immutable-revision lifecycle for the school-leadership group-hour
allocation (plan §5.1, §3.11, §9):

* revisions are append-only — there is no update or delete path;
* exactly one revision per process is *current* (``superseded_at IS NULL``);
* creating a revision supersedes the current one and increments
  ``revision_number`` inside a single transaction;
* every creation records an ``AuditEvent``.

Downstream side effects of a revision (marking the ``TeachingPlan`` stale,
recomputing both balances, blocking assignment operations — plan §3.11, §9)
are owned by the later tasks that introduce those models; this controller only
establishes the revision history and the one-current invariant.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlmodel import Session, col, desc, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.department_hour_allocation_revisions import (
    DepartmentHourAllocationRevision,
    DepartmentHourAllocationRevisionCreate,
    DepartmentHourAllocationRevisionPublic,
    DepartmentHourAllocationRevisionsPublic,
)
from reparto_service.enums import AuditEventType


class DepartmentHourAllocationRevisionController(DomainController):
    """Read and append-only create logic for allocation revisions."""

    @staticmethod
    def list_revisions(
        session: Session, process_id: uuid.UUID
    ) -> DepartmentHourAllocationRevisionsPublic:
        DomainController.get_process_or_404(session, process_id)
        rows = list(
            session.exec(
                select(DepartmentHourAllocationRevision)
                .where(
                    DepartmentHourAllocationRevision.assignment_process_id == process_id
                )
                .order_by(col(DepartmentHourAllocationRevision.revision_number))
            ).all()
        )
        return DepartmentHourAllocationRevisionsPublic(
            data=[
                DepartmentHourAllocationRevisionPublic.model_validate(row)
                for row in rows
            ],
            count=len(rows),
        )

    @staticmethod
    def get_current_revision(
        session: Session, process_id: uuid.UUID
    ) -> DepartmentHourAllocationRevisionPublic:
        DomainController.get_process_or_404(session, process_id)
        current = DepartmentHourAllocationRevisionController._current_revision(
            session, process_id
        )
        if current is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(f"No current allocation revision for process {process_id}."),
            )
        return DepartmentHourAllocationRevisionPublic.model_validate(current)

    @staticmethod
    def create_revision(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
        revision_in: DepartmentHourAllocationRevisionCreate,
    ) -> DepartmentHourAllocationRevisionPublic:
        """Append a new revision, superseding the current one transactionally.

        A ``final``/``archived`` process must be reopened before its allocation
        can change (plan §3.11); ``ensure_process_mutable`` enforces that.
        """
        process = DomainController.get_process_or_404(session, process_id)
        DomainController.ensure_process_mutable(process)

        current = DepartmentHourAllocationRevisionController._current_revision(
            session, process_id
        )
        if current is not None:
            current.superseded_at = datetime.now(tz=timezone.utc)
            session.add(current)
            next_number = current.revision_number + 1
        else:
            next_number = 1

        revision = DepartmentHourAllocationRevision(
            assignment_process_id=process_id,
            revision_number=next_number,
            allocated_group_weekly_hours=revision_in.allocated_group_weekly_hours,
            reason=revision_in.reason,
            source=revision_in.source,
            source_reference=revision_in.source_reference,
            received_at=revision_in.received_at,
            created_by_user_id=uuid.UUID(str(current_user.id)),
        )
        session.add(revision)
        DepartmentHourAllocationRevisionController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type=AuditEventType.ALLOCATION_REVISED,
            entity_type="department_hour_allocation_revision",
            entity_id=revision.id,
            before=None,
            after=revision,
            reason=revision_in.reason,
        )
        try:
            session.commit()
        except Exception as exc:  # pragma: no cover - DB race guard
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Could not create allocation revision; please retry.",
            ) from exc
        session.refresh(revision)
        return DepartmentHourAllocationRevisionPublic.model_validate(revision)

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _current_revision(
        session: Session, process_id: uuid.UUID
    ) -> DepartmentHourAllocationRevision | None:
        """Return the single non-superseded revision, or ``None`` if there is none."""
        return session.exec(
            select(DepartmentHourAllocationRevision)
            .where(
                DepartmentHourAllocationRevision.assignment_process_id == process_id,
                col(DepartmentHourAllocationRevision.superseded_at).is_(None),
            )
            .order_by(desc(col(DepartmentHourAllocationRevision.revision_number)))
        ).first()


__all__ = ["DepartmentHourAllocationRevisionController"]
