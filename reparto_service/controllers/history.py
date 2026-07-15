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

from reparto_service.controllers.assignment_processes import AssignmentProcessController
from reparto_service.controllers.base import DomainController
from reparto_service.db_models.assignment_processes import (
    AssignmentProcess,
    AssignmentProcessPublic,
)
from reparto_service.db_models.assignments import Assignment, AssignmentPublic
from reparto_service.db_models.export_artifacts import (
    ExportArtifact,
    ExportBackupRestoreRequest,
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
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teaching_groups import TeachingGroup
from reparto_service.enums import (
    ActivityType,
    AssignmentProcessStatus,
    AssignmentSource,
    AssignmentStatus,
    ExportArtifactFormat,
    ExportArtifactType,
    SelectionOrderMode,
    SubjectAllocationCategory,
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
        if payload.export_type == ExportArtifactType.BACKUP:
            snapshot["versions"] = HistoryController._version_summaries(
                session, process_id
            )
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
    def restore_backup_to_draft(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
        payload: ExportBackupRestoreRequest,
    ) -> AssignmentProcessPublic:
        """Restore backup JSON into an empty draft process."""
        target = DomainController.get_process_or_404(session, process_id)
        if target.status != AssignmentProcessStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Backup restore is only allowed into a draft process.",
            )
        AssignmentProcessController._ensure_target_empty(session, target.id)
        snapshot = HistoryController._parse_backup(payload.content)
        source_process = HistoryController._process_section(snapshot)
        HistoryController._restore_process_settings(target, source_process)
        subject_map = HistoryController._restore_subjects(session, target, snapshot)
        group_map = HistoryController._restore_groups(session, target, snapshot)
        teacher_map = HistoryController._restore_teachers(session, target, snapshot)
        requirement_map = HistoryController._restore_requirements(
            session, target, snapshot, subject_map, group_map
        )
        if payload.restore_assignments:
            HistoryController._restore_assignments(
                session, target, snapshot, requirement_map, teacher_map
            )
        target.created_from_process_id = uuid.UUID(str(source_process["id"]))
        target.created_by_user_id = uuid.UUID(str(current_user.id))
        session.add(target)
        session.commit()
        session.refresh(target)
        return AssignmentProcessPublic.model_validate(target)

    @staticmethod
    def _snapshot(session: Session, process_id: uuid.UUID) -> dict[str, Any]:
        process = DomainController.get_process_or_404(session, process_id)
        subjects = list(
            session.exec(
                select(Subject).where(Subject.assignment_process_id == process_id)
            ).all()
        )
        teaching_groups = list(
            session.exec(
                select(TeachingGroup).where(
                    TeachingGroup.assignment_process_id == process_id
                )
            ).all()
        )
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
            "subjects": [row.model_dump(mode="json") for row in subjects],
            "teaching_groups": [row.model_dump(mode="json") for row in teaching_groups],
            "teachers": [row.model_dump(mode="json") for row in teachers],
            "requirements": [row.model_dump(mode="json") for row in requirements],
            "assignments": [
                AssignmentPublic.model_validate(row).model_dump(mode="json")
                for row in assignments
            ],
        }

    @staticmethod
    def _version_summaries(
        session: Session, process_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        versions = session.exec(
            select(ProcessVersion)
            .where(ProcessVersion.assignment_process_id == process_id)
            .order_by(col(ProcessVersion.version_number))
        ).all()
        return [
            {
                "id": str(version.id),
                "version_number": version.version_number,
                "status": version.status.value,
                "reason": version.reason,
                "created_by_user_id": str(version.created_by_user_id),
                "created_at": version.created_at.isoformat(),
            }
            for version in versions
        ]

    @staticmethod
    def _parse_backup(content: str) -> dict[str, Any]:
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Backup content must be valid JSON.",
            ) from exc
        if not isinstance(raw, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Backup content must be a process snapshot object.",
            )
        HistoryController._process_section(raw)
        for key in ("subjects", "teaching_groups", "teachers"):
            HistoryController._list_section(raw, key)
        for key in ("requirements", "assignments"):
            HistoryController._list_section(raw, key)
        return raw

    @staticmethod
    def _process_section(snapshot: dict[str, Any]) -> dict[str, Any]:
        section = snapshot.get("process")
        if not isinstance(section, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Backup snapshot is missing process.",
            )
        return section

    @staticmethod
    def _list_section(snapshot: dict[str, Any], key: str) -> list[Any]:
        section = snapshot.get(key)
        if not isinstance(section, list):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Backup snapshot is missing {key}.",
            )
        return section

    @staticmethod
    def _restore_process_settings(
        target: AssignmentProcess, source: dict[str, Any]
    ) -> None:
        target.default_teacher_hours_reference = source.get(
            "default_teacher_hours_reference"
        )
        target.selection_order_enabled = bool(source.get("selection_order_enabled"))
        target.selection_order_mode = SelectionOrderMode(
            source.get("selection_order_mode", "none")
        )
        target.direct_teacher_selection_enabled = False
        target.lan_access_enabled = False

    @staticmethod
    def _restore_subjects(
        session: Session, target: AssignmentProcess, snapshot: dict[str, Any]
    ) -> dict[str, uuid.UUID]:
        mapping: dict[str, uuid.UUID] = {}
        for row in HistoryController._list_section(snapshot, "subjects"):
            subject = Subject(
                assignment_process_id=target.id,
                name=row["name"],
                allocation_category=row.get(
                    "allocation_category", SubjectAllocationCategory.MAIN
                ),
                activity_type=row.get("activity_type", ActivityType.ORDINARY),
                default_group_weekly_hours=row.get("default_group_weekly_hours"),
                default_teacher_weekly_hours_per_position=row.get(
                    "default_teacher_weekly_hours_per_position"
                ),
                default_required_teacher_count=row.get(
                    "default_required_teacher_count", 1
                ),
                allows_multiple_groups=row.get("allows_multiple_groups", False),
                allows_zero_groups=row.get("allows_zero_groups", False),
                notes=row.get("notes"),
            )
            session.add(subject)
            session.flush()
            mapping[str(row["id"])] = subject.id
        return mapping

    @staticmethod
    def _restore_groups(
        session: Session, target: AssignmentProcess, snapshot: dict[str, Any]
    ) -> dict[str, uuid.UUID]:
        mapping: dict[str, uuid.UUID] = {}
        for row in HistoryController._list_section(snapshot, "teaching_groups"):
            group = TeachingGroup(
                assignment_process_id=target.id,
                classroom_stage_id=uuid.UUID(str(row["classroom_stage_id"])),
                grade=row["grade"],
                group_code=row["group_code"],
                label=row["label"],
                notes=row.get("notes"),
            )
            session.add(group)
            session.flush()
            mapping[str(row["id"])] = group.id
        return mapping

    @staticmethod
    def _restore_teachers(
        session: Session, target: AssignmentProcess, snapshot: dict[str, Any]
    ) -> dict[str, uuid.UUID]:
        mapping: dict[str, uuid.UUID] = {}
        for row in HistoryController._list_section(snapshot, "teachers"):
            teacher = ProcessTeacher(
                assignment_process_id=target.id,
                teacher_profile_id=uuid.UUID(str(row["teacher_profile_id"])),
                base_weekly_hours=row["base_weekly_hours"],
                extra_weekly_hours=row["extra_weekly_hours"],
                participates_in_selection=row["participates_in_selection"],
                selection_position=row.get("selection_position"),
                selection_points=row.get("selection_points"),
                selection_criteria_label=row.get("selection_criteria_label"),
                selection_notes=row.get("selection_notes"),
                order_locked=row["order_locked"],
                status=row["status"],
            )
            session.add(teacher)
            session.flush()
            mapping[str(row["id"])] = teacher.id
        return mapping

    @staticmethod
    def _restore_requirements(
        session: Session,
        target: AssignmentProcess,
        snapshot: dict[str, Any],
        subject_map: dict[str, uuid.UUID],
        group_map: dict[str, uuid.UUID],
    ) -> dict[str, uuid.UUID]:
        mapping: dict[str, uuid.UUID] = {}
        for row in HistoryController._list_section(snapshot, "requirements"):
            requirement = HourRequirement(
                assignment_process_id=target.id,
                teaching_group_id=group_map[str(row["teaching_group_id"])],
                subject_id=subject_map[str(row["subject_id"])],
                required_hours=row["required_hours"],
                requirement_type=row["requirement_type"],
                flags=row.get("flags"),
                notes=row.get("notes"),
            )
            session.add(requirement)
            session.flush()
            mapping[str(row["id"])] = requirement.id
        return mapping

    @staticmethod
    def _restore_assignments(
        session: Session,
        target: AssignmentProcess,
        snapshot: dict[str, Any],
        requirement_map: dict[str, uuid.UUID],
        teacher_map: dict[str, uuid.UUID],
    ) -> None:
        for row in HistoryController._list_section(snapshot, "assignments"):
            session.add(
                Assignment(
                    assignment_process_id=target.id,
                    hour_requirement_id=requirement_map[
                        str(row["hour_requirement_id"])
                    ],
                    process_teacher_id=teacher_map[str(row["process_teacher_id"])],
                    assigned_hours=row["assigned_hours"],
                    assignment_type=row["assignment_type"],
                    source=AssignmentSource.SYSTEM_COPY,
                    status=AssignmentStatus.DRAFT,
                    chosen_by_user_id=None,
                    confirmed_by_user_id=None,
                    override_reason=None,
                    overridden_by_user_id=None,
                    notes=row.get("notes"),
                )
            )

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
        if artifact_format == ExportArtifactFormat.PDF:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="PDF export is not implemented.",
            )
        raise AssertionError(
            f"Unsupported export format: {artifact_format}"
        )  # pragma: no cover

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
