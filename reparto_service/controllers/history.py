"""Process history, comparison and export controller."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlmodel import Session, col, func, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.assignments import Assignment, AssignmentPublic
from reparto_service.db_models.export_artifacts import (
    ExportArtifact,
    ExportArtifactCreate,
    ExportArtifactPublic,
    ExportArtifactsPublic,
)
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.process_versions import (
    ProcessVersion,
    ProcessVersionCreate,
    ProcessVersionPublic,
    ProcessVersionsPublic,
    VersionComparison,
)
from reparto_service.enums import (
    AssignmentProcessStatus,
    ExportArtifactFormat,
    ExportArtifactType,
    ValidationSeverity,
)
from reparto_service.services.summary import SummaryService


class HistoryController(DomainController):
    """Snapshot, compare and export process state."""

    @staticmethod
    def list_versions(session: Session, process_id: uuid.UUID) -> ProcessVersionsPublic:
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
            snapshot_json=HistoryController._snapshot(session, process_id),
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
        left = HistoryController._get_version_or_404(
            session, process_id, left_version_id
        )
        right = HistoryController._get_version_or_404(
            session, process_id, right_version_id
        )
        return HistoryController._compare_snapshots(left, right)

    @staticmethod
    def compare_previous_year(
        session: Session, process_id: uuid.UUID
    ) -> VersionComparison:
        process = DomainController.get_process_or_404(session, process_id)
        if process.created_from_process_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Process has no previous-year source process.",
            )
        left_snapshot = HistoryController._snapshot(
            session, process.created_from_process_id
        )
        right_snapshot = HistoryController._snapshot(session, process_id)
        left = HistoryController._virtual_version(
            process.created_from_process_id, left_snapshot
        )
        right = HistoryController._virtual_version(process_id, right_snapshot)
        return HistoryController._compare_snapshots(left, right)

    @staticmethod
    def list_artifacts(
        session: Session, process_id: uuid.UUID
    ) -> ExportArtifactsPublic:
        DomainController.get_process_or_404(session, process_id)
        rows = list(
            session.exec(
                select(ExportArtifact).where(
                    ExportArtifact.assignment_process_id == process_id
                )
            ).all()
        )
        return ExportArtifactsPublic(
            data=[ExportArtifactPublic.model_validate(row) for row in rows],
            count=len(rows),
        )

    @staticmethod
    def create_artifact(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
        payload: ExportArtifactCreate,
    ) -> ExportArtifactPublic:
        process = DomainController.get_process_or_404(session, process_id)
        if payload.export_type == ExportArtifactType.FINAL:
            HistoryController._ensure_no_blocking_validations(session, process_id)
        if payload.process_version_id is not None:
            HistoryController._get_version_or_404(
                session, process_id, payload.process_version_id
            )
        snapshot = HistoryController._snapshot(session, process_id)
        content = HistoryController._render_artifact(payload.format, snapshot)
        checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
        artifact = ExportArtifact(
            assignment_process_id=process_id,
            process_version_id=payload.process_version_id,
            export_type=payload.export_type,
            format=payload.format,
            file_path=(
                f"exports/{process_id}/{payload.export_type.value}-"
                f"{checksum[:12]}.{payload.format.value}"
            ),
            created_by_user_id=uuid.UUID(str(current_user.id)),
            checksum=checksum,
            content=content,
        )
        if payload.export_type == ExportArtifactType.FINAL:
            process.status = AssignmentProcessStatus.ARCHIVED
            session.add(process)
        session.add(artifact)
        session.commit()
        session.refresh(artifact)
        return ExportArtifactPublic.model_validate(artifact)

    @staticmethod
    def _snapshot(session: Session, process_id: uuid.UUID) -> dict[str, Any]:
        process = DomainController.get_process_or_404(session, process_id)
        teachers = list(
            session.exec(
                select(ProcessTeacher).where(
                    ProcessTeacher.assignment_process_id == process_id
                )
            ).all()
        )
        requirements = list(
            session.exec(
                select(HourRequirement).where(
                    HourRequirement.assignment_process_id == process_id
                )
            ).all()
        )
        assignments = list(
            session.exec(
                select(Assignment).where(Assignment.assignment_process_id == process_id)
            ).all()
        )
        return {
            "process": process.model_dump(mode="json"),
            "summary": SummaryService.compute_summary(session, process_id).model_dump(
                mode="json"
            ),
            "teachers": [row.model_dump(mode="json") for row in teachers],
            "requirements": [row.model_dump(mode="json") for row in requirements],
            "assignments": [
                AssignmentPublic.model_validate(row).model_dump(mode="json")
                for row in assignments
            ],
        }

    @staticmethod
    def _render_artifact(
        artifact_format: ExportArtifactFormat, snapshot: dict[str, Any]
    ) -> str:
        if artifact_format == ExportArtifactFormat.JSON:
            return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        if artifact_format == ExportArtifactFormat.CSV:
            output = io.StringIO()
            writer = csv.writer(output, lineterminator="\n")
            writer.writerow(["section", "id", "hours", "status"])
            for row in snapshot["requirements"]:
                writer.writerow(["requirement", row["id"], row["required_hours"], ""])
            for row in snapshot["assignments"]:
                writer.writerow(
                    ["assignment", row["id"], row["assigned_hours"], row["status"]]
                )
            return output.getvalue()
        return "\n".join(
            [
                "Reparto docente export",
                f"Process: {snapshot['process']['id']}",
                f"Status: {snapshot['process']['status']}",
                f"Required hours: {snapshot['summary']['global_balance']['total_required_hours']}",
                f"Assigned hours: {snapshot['summary']['global_balance']['total_assigned_hours']}",
            ]
        )

    @staticmethod
    def _ensure_no_blocking_validations(
        session: Session, process_id: uuid.UUID
    ) -> None:
        summary = SummaryService.compute_summary(session, process_id)
        if any(
            item.severity == ValidationSeverity.BLOCKING for item in summary.validations
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Final export is blocked by blocking validations.",
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

    @staticmethod
    def _virtual_version(
        process_id: uuid.UUID, snapshot: dict[str, Any]
    ) -> ProcessVersion:
        return ProcessVersion(
            assignment_process_id=process_id,
            version_number=1,
            status=AssignmentProcessStatus(snapshot["process"]["status"]),
            reason=None,
            created_by_user_id=uuid.UUID(snapshot["process"]["created_by_user_id"]),
            snapshot_json=snapshot,
        )

    @staticmethod
    def _compare_snapshots(
        left: ProcessVersion, right: ProcessVersion
    ) -> VersionComparison:
        left_summary = left.snapshot_json["summary"]["global_balance"]
        right_summary = right.snapshot_json["summary"]["global_balance"]
        changed = [
            section
            for section in ("teachers", "requirements", "assignments")
            if left.snapshot_json[section] != right.snapshot_json[section]
        ]
        return VersionComparison(
            left_version_id=left.id,
            right_version_id=right.id,
            changed_sections=changed,
            required_hours_delta=(
                right_summary["total_required_hours"]
                - left_summary["total_required_hours"]
            ),
            assigned_hours_delta=(
                right_summary["total_assigned_hours"]
                - left_summary["total_assigned_hours"]
            ),
            teacher_count_delta=(
                len(right.snapshot_json["teachers"])
                - len(left.snapshot_json["teachers"])
            ),
            requirement_count_delta=(
                len(right.snapshot_json["requirements"])
                - len(left.snapshot_json["requirements"])
            ),
            assignment_count_delta=(
                len(right.snapshot_json["assignments"])
                - len(left.snapshot_json["assignments"])
            ),
        )


__all__ = ["HistoryController"]
