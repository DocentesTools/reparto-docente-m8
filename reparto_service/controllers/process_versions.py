"""Process version snapshot and comparison controller (plan §10.2, §10.3).

Versioning captures the full three-stage state of a process as an immutable
:class:`~reparto_service.db_models.process_versions.ProcessVersion` and diffs two
captures along the plan §10.3 comparison dimensions. Both operations delegate the
domain work to :class:`~reparto_service.services.snapshots.SnapshotService`:

* :meth:`~ProcessVersionController.create_version` stores a fresh snapshot with a
  monotonic per-process version number;
* :meth:`~ProcessVersionController.compare_versions` diffs two stored versions;
* :meth:`~ProcessVersionController.compare_previous_year` diffs a live snapshot of
  the process against a live snapshot of its previous-year source process.

Export artifacts and backup/restore stay on the (separately adapted)
``HistoryController``; this controller owns only the version snapshot and
comparison surface (plan §10.2/§10.3).
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, col, func, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.process_versions import (
    ProcessVersion,
    ProcessVersionCreate,
    ProcessVersionPublic,
    ProcessVersionsPublic,
    VersionComparison,
)
from reparto_service.services.snapshots import SnapshotService


class ProcessVersionController(DomainController):
    """List, create and compare immutable three-stage process snapshots."""

    @staticmethod
    def list_versions(session: Session, process_id: uuid.UUID) -> ProcessVersionsPublic:
        """Return every version of a process, oldest-first (plan §10.2)."""
        DomainController.get_process_or_404(session, process_id)
        rows = list(
            session.exec(
                select(ProcessVersion)
                .where(ProcessVersion.assignment_process_id == process_id)
                .order_by(col(ProcessVersion.version_number))
            ).all()
        )
        return ProcessVersionsPublic(
            data=[ProcessVersionPublic.model_validate(row) for row in rows],
            count=len(rows),
        )

    @staticmethod
    def create_version(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
        payload: ProcessVersionCreate,
    ) -> ProcessVersionPublic:
        """Store a fresh three-stage snapshot with the next version number."""
        process = DomainController.get_process_or_404(session, process_id)
        next_number = (
            session.exec(
                select(func.max(ProcessVersion.version_number)).where(
                    ProcessVersion.assignment_process_id == process_id
                )
            ).one()
            or 0
        ) + 1
        version = ProcessVersion(
            assignment_process_id=process_id,
            version_number=next_number,
            status=process.status,
            reason=payload.reason,
            created_by_user_id=uuid.UUID(str(current_user.id)),
            snapshot_json=SnapshotService.build_snapshot(session, process_id),
        )
        session.add(version)
        session.commit()
        session.refresh(version)
        return ProcessVersionPublic.model_validate(version)

    @staticmethod
    def compare_versions(
        session: Session,
        process_id: uuid.UUID,
        left_version_id: uuid.UUID,
        right_version_id: uuid.UUID,
    ) -> VersionComparison:
        """Diff two stored versions along the plan §10.3 dimensions."""
        left = ProcessVersionController._get_version_or_404(
            session, process_id, left_version_id
        )
        right = ProcessVersionController._get_version_or_404(
            session, process_id, right_version_id
        )
        return SnapshotService.compare_snapshots(
            left.id, right.id, left.snapshot_json, right.snapshot_json
        )

    @staticmethod
    def compare_previous_year(
        session: Session, process_id: uuid.UUID
    ) -> VersionComparison:
        """Diff a live snapshot of the process against its previous-year source.

        The comparison uses live snapshots (not stored versions), so the two
        ``version_id`` fields carry the two process ids as diff identifiers.
        """
        process = DomainController.get_process_or_404(session, process_id)
        if process.created_from_process_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Process has no previous-year source process.",
            )
        left = SnapshotService.build_snapshot(session, process.created_from_process_id)
        right = SnapshotService.build_snapshot(session, process_id)
        return SnapshotService.compare_snapshots(
            process.created_from_process_id, process_id, left, right
        )

    @staticmethod
    def _get_version_or_404(
        session: Session, process_id: uuid.UUID, version_id: uuid.UUID
    ) -> ProcessVersion:
        version = session.get(ProcessVersion, version_id)
        if version is None or version.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"ProcessVersion {version_id} not found.",
            )
        return version


__all__ = ["ProcessVersionController"]
