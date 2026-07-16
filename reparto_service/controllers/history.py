"""Process history, backup and export controller.

Backup and restore are adapted to the three-stage domain (plan §10.4). A backup
snapshot is a complete, restorable capture of the new domain: the leadership
allocation revisions, the teaching plan, the group-subject matrix, the teaching
activities with their linked cells, the generated indivisible requirement slots
and the assignments — alongside the configuration (subjects, groups, teachers).
:meth:`HistoryController.restore_backup_to_draft` rebuilds that state into an
empty draft process, remapping every id, resetting the plan feasibility (a
restore never trusts a stored feasibility result — plan §20.25) and restoring the
generated requirement slots and their assignments only when the restore mode asks
for them (plan §10.4). Generation and reconciliation consistency of the backup is
validated before anything is written.

The version-comparison flow lives on the separately adapted
:class:`~reparto_service.controllers.process_versions.ProcessVersionController`;
this controller owns the export artifacts and the backup/restore round trip.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.assignment_processes import AssignmentProcessController
from reparto_service.controllers.base import DomainController
from reparto_service.db_models.assignment_processes import (
    AssignmentProcess,
    AssignmentProcessPublic,
)
from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.department_hour_allocation_revisions import (
    DepartmentHourAllocationRevision,
)
from reparto_service.db_models.export_artifacts import (
    ExportArtifact,
    ExportBackupRestoreRequest,
    ExportArtifactCreate,
    ExportArtifactPublic,
    ExportArtifactsPublic,
)
from reparto_service.db_models.group_subjects import GroupSubject
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.process_versions import ProcessVersion
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teaching_activities import (
    TeachingActivity,
    TeachingActivityGroup,
)
from reparto_service.db_models.teaching_groups import TeachingGroup
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    ActivityType,
    AssignmentProcessStatus,
    AssignmentSource,
    AssignmentStatus,
    DepartmentHourAllocationSource,
    ExportArtifactFormat,
    ExportArtifactType,
    FeasibilityStatus,
    HourRequirementStatus,
    SelectionOrderMode,
    SubjectAllocationCategory,
    TeachingActivitySource,
    TeachingActivitySyncState,
    TeachingPlanStatus,
)
from reparto_service.services.validations import AssignmentValidationService

#: The literal JSON value the ``ACTIVE`` assignment status serialises to.
_ACTIVE = AssignmentStatus.ACTIVE.value

#: Teaching-plan statuses that are coherent with a plan whose requirement slots
#: have NOT been restored (plan §10.4): a restore that skips requirements resets
#: any post-generation status back to the pre-generation ``LOCKED`` baseline.
_PRE_GENERATION_PLAN_STATUSES: frozenset[TeachingPlanStatus] = frozenset(
    {
        TeachingPlanStatus.DRAFT,
        TeachingPlanStatus.UNBALANCED,
        TeachingPlanStatus.BALANCED,
        TeachingPlanStatus.LOCKED,
    }
)

#: Plan statuses that imply the plan was locked (``locked_at`` is set).
_LOCKED_PLAN_STATUSES: frozenset[TeachingPlanStatus] = frozenset(
    {
        TeachingPlanStatus.LOCKED,
        TeachingPlanStatus.REQUIREMENTS_GENERATED,
        TeachingPlanStatus.STALE,
        TeachingPlanStatus.RECONCILIATION_REQUIRED,
    }
)

#: Plan statuses that imply requirement slots exist (``requirements_generated_at``).
_GENERATED_PLAN_STATUSES: frozenset[TeachingPlanStatus] = frozenset(
    {
        TeachingPlanStatus.REQUIREMENTS_GENERATED,
        TeachingPlanStatus.STALE,
        TeachingPlanStatus.RECONCILIATION_REQUIRED,
    }
)


class HistoryController(DomainController):
    """Export, backup and restore process state for the three-stage domain."""

    # ── Export artifacts ─────────────────────────────────────────────────────

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
            HistoryController._ensure_no_blocking_validations(session, process)
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

    # ── Restore ──────────────────────────────────────────────────────────────

    @staticmethod
    def restore_backup_to_draft(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
        payload: ExportBackupRestoreRequest,
    ) -> AssignmentProcessPublic:
        """Restore a backup snapshot into an empty draft process (plan §10.4).

        The target process must be in ``draft`` and currently empty. The backup's
        generation and reconciliation consistency is validated before any write.
        Configuration, allocation history, the teaching plan and its activities
        are always restored; the generated requirement slots and their
        assignments are restored only when ``restore_assignments`` is set (the
        "existing restore mode", plan §10.4). Plan feasibility is always reset —
        a restore never trusts a stored feasibility result (plan §20.25). No auth
        user/actor attribution is carried across.
        """
        target = DomainController.get_process_or_404(session, process_id)
        before = AssignmentProcess.model_validate(target.model_dump())
        if target.status != AssignmentProcessStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Backup restore is only allowed into a draft process.",
            )
        AssignmentProcessController._ensure_target_empty(session, target.id)
        snapshot = HistoryController._parse_backup(payload.content)
        HistoryController._validate_consistency(snapshot)
        restore_slots = payload.restore_assignments
        source_process = HistoryController._process_section(snapshot)

        HistoryController._restore_process_settings(target, source_process)
        allocation_map = HistoryController._restore_allocation_revisions(
            session, target, snapshot, current_user
        )
        subject_map = HistoryController._restore_subjects(session, target, snapshot)
        group_map = HistoryController._restore_groups(session, target, snapshot)
        cell_map = HistoryController._restore_group_subjects(
            session, target, snapshot, group_map, subject_map
        )
        teacher_map = HistoryController._restore_teachers(session, target, snapshot)
        plan = HistoryController._restore_plan(
            session, target, snapshot, allocation_map, current_user, restore_slots
        )
        activity_map = HistoryController._restore_activities(
            session, plan, snapshot, subject_map, cell_map
        )
        HistoryController._restore_activity_groups(
            session, snapshot, activity_map, cell_map
        )
        if restore_slots:
            requirement_map = HistoryController._restore_requirements(
                session, target, snapshot, activity_map
            )
            HistoryController._restore_assignments(
                session, target, snapshot, requirement_map, teacher_map, activity_map
            )

        target.created_from_process_id = uuid.UUID(str(source_process["id"]))
        target.created_by_user_id = uuid.UUID(str(current_user.id))
        session.add(target)
        HistoryController.record_audit_event(
            session,
            process_id=target.id,
            current_user=current_user,
            event_type="process.restored_from_backup",
            entity_type="assignment_process",
            entity_id=target.id,
            before=before,
            after=target,
            reason=str(source_process["id"]),
        )
        session.commit()
        session.refresh(target)
        return AssignmentProcessPublic.model_validate(target)

    # ── Snapshot capture (plan §10.4) ────────────────────────────────────────

    @staticmethod
    def _snapshot(session: Session, process_id: uuid.UUID) -> dict[str, Any]:
        """Capture the complete restorable three-stage domain (plan §10.4).

        Every section is a raw ``model_dump(mode="json")`` of the owned rows in a
        deterministic order, so a JSON backup is a byte-stable, fully restorable
        description of the process. Requirement slots and assignments include the
        retired/cancelled rows so generation lineage and reconciliation state
        round-trip.
        """
        process = DomainController.get_process_or_404(session, process_id)
        plan = HistoryController._plan(session, process_id)
        activities = HistoryController._activities(session, plan)
        return {
            "process": process.model_dump(mode="json"),
            "allocation_revisions": [
                row.model_dump(mode="json")
                for row in session.exec(
                    select(DepartmentHourAllocationRevision)
                    .where(
                        DepartmentHourAllocationRevision.assignment_process_id
                        == process_id
                    )
                    .order_by(col(DepartmentHourAllocationRevision.revision_number))
                ).all()
            ],
            "teaching_plan": (None if plan is None else plan.model_dump(mode="json")),
            "subjects": [
                row.model_dump(mode="json")
                for row in session.exec(
                    select(Subject)
                    .where(Subject.assignment_process_id == process_id)
                    .order_by(col(Subject.id))
                ).all()
            ],
            "teaching_groups": [
                row.model_dump(mode="json")
                for row in session.exec(
                    select(TeachingGroup)
                    .where(TeachingGroup.assignment_process_id == process_id)
                    .order_by(col(TeachingGroup.id))
                ).all()
            ],
            "group_subjects": [
                row.model_dump(mode="json")
                for row in session.exec(
                    select(GroupSubject)
                    .where(GroupSubject.assignment_process_id == process_id)
                    .order_by(col(GroupSubject.id))
                ).all()
            ],
            "teachers": [
                row.model_dump(mode="json")
                for row in session.exec(
                    select(ProcessTeacher)
                    .where(ProcessTeacher.assignment_process_id == process_id)
                    .order_by(col(ProcessTeacher.id))
                ).all()
            ],
            "teaching_activities": [
                activity.model_dump(mode="json") for activity in activities
            ],
            "teaching_activity_groups": [
                link.model_dump(mode="json")
                for activity in activities
                for link in session.exec(
                    select(TeachingActivityGroup)
                    .where(TeachingActivityGroup.teaching_activity_id == activity.id)
                    .order_by(col(TeachingActivityGroup.group_subject_id))
                ).all()
            ],
            "requirements": [
                row.model_dump(mode="json")
                for row in session.exec(
                    select(HourRequirement)
                    .where(HourRequirement.assignment_process_id == process_id)
                    .order_by(
                        col(HourRequirement.teaching_activity_id),
                        col(HourRequirement.position_index),
                        col(HourRequirement.id),
                    )
                ).all()
            ],
            "assignments": [
                row.model_dump(mode="json")
                for row in session.exec(
                    select(Assignment)
                    .where(Assignment.assignment_process_id == process_id)
                    .order_by(col(Assignment.id))
                ).all()
            ],
        }

    @staticmethod
    def _plan(session: Session, process_id: uuid.UUID) -> Optional[TeachingPlan]:
        return session.exec(
            select(TeachingPlan).where(TeachingPlan.assignment_process_id == process_id)
        ).first()

    @staticmethod
    def _activities(
        session: Session, plan: Optional[TeachingPlan]
    ) -> list[TeachingActivity]:
        if plan is None:
            return []
        return list(
            session.exec(
                select(TeachingActivity)
                .where(TeachingActivity.teaching_plan_id == plan.id)
                .order_by(col(TeachingActivity.id))
            ).all()
        )

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

    # ── Backup parsing and consistency (plan §10.4) ──────────────────────────

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
        for key in (
            "allocation_revisions",
            "subjects",
            "teaching_groups",
            "group_subjects",
            "teachers",
            "teaching_activities",
            "teaching_activity_groups",
            "requirements",
            "assignments",
        ):
            HistoryController._list_section(raw, key)
        return raw

    @staticmethod
    def _validate_consistency(snapshot: dict[str, Any]) -> None:
        """Validate the backup's generation and reconciliation consistency.

        Rejects (400) a backup that references a generation beyond the plan's
        current generation, retires a slot out of order, links a supersession to
        a missing slot, or carries an assignment whose slot/teacher is missing,
        whose denormalised activity disagrees with its slot, or that would place
        two active assignments on one slot or one teacher twice on an activity
        (plan §10.4, mirroring the DB active-slot invariants of plan §20.9).
        """
        plan_section = snapshot.get("teaching_plan")
        requirements = HistoryController._list_section(snapshot, "requirements")
        assignments = HistoryController._list_section(snapshot, "assignments")

        if (requirements or assignments) and not isinstance(plan_section, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Backup carries requirements or assignments but no teaching plan."
                ),
            )
        current_generation = (
            int(plan_section["current_generation_number"])
            if isinstance(plan_section, dict)
            else 0
        )

        requirement_activity: dict[str, str] = {}
        for row in requirements:
            requirement_id = str(row["id"])
            requirement_activity[requirement_id] = str(row["teaching_activity_id"])
            created = int(row["created_generation"])
            validated = int(row["last_validated_generation"])
            retired = row.get("retired_generation")
            if created > current_generation or validated > current_generation:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Requirement {requirement_id} references a generation "
                        "beyond the plan's current generation."
                    ),
                )
            if retired is not None and (
                int(retired) > current_generation or int(retired) < created
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Requirement {requirement_id} has an inconsistent "
                        "retirement generation."
                    ),
                )
        for row in requirements:
            superseded = row.get("superseded_by_requirement_id")
            if superseded is not None and str(superseded) not in requirement_activity:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Requirement {row['id']} is superseded by a slot missing "
                        "from the backup."
                    ),
                )

        teacher_ids = {
            str(row["id"])
            for row in HistoryController._list_section(snapshot, "teachers")
        }
        active_slots: set[str] = set()
        active_activity_teacher: set[tuple[str, str]] = set()
        for row in assignments:
            requirement_id = str(row["hour_requirement_id"])
            if requirement_id not in requirement_activity:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Assignment {row['id']} references a requirement missing "
                        "from the backup."
                    ),
                )
            activity_id = str(row["teaching_activity_id"])
            if activity_id != requirement_activity[requirement_id]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Assignment {row['id']} activity does not match its "
                        "requirement slot."
                    ),
                )
            teacher_id = str(row["process_teacher_id"])
            if teacher_id not in teacher_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Assignment {row['id']} references a teacher missing from "
                        "the backup."
                    ),
                )
            if str(row.get("status", _ACTIVE)) != _ACTIVE:
                continue
            if requirement_id in active_slots:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Requirement {requirement_id} has more than one active "
                        "assignment."
                    ),
                )
            active_slots.add(requirement_id)
            if (activity_id, teacher_id) in active_activity_teacher:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Teacher {teacher_id} is actively assigned twice on one "
                        "activity."
                    ),
                )
            active_activity_teacher.add((activity_id, teacher_id))

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

    # ── Restore helpers ──────────────────────────────────────────────────────

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
        # Never auto-enable live LAN/direct access on a restore (plan §10.4).
        target.direct_teacher_selection_enabled = False
        target.lan_access_enabled = False

    @staticmethod
    def _restore_allocation_revisions(
        session: Session,
        target: AssignmentProcess,
        snapshot: dict[str, Any],
        current_user: UserModel,
    ) -> dict[str, uuid.UUID]:
        """Restore the immutable allocation history (plan §10.4, §3.11).

        The actor is the restoring user (no original actor is carried across);
        ``superseded_at`` is preserved so exactly the same revision stays current.
        """
        mapping: dict[str, uuid.UUID] = {}
        for row in HistoryController._list_section(snapshot, "allocation_revisions"):
            revision = DepartmentHourAllocationRevision(
                assignment_process_id=target.id,
                revision_number=row["revision_number"],
                allocated_group_weekly_hours=row["allocated_group_weekly_hours"],
                reason=row["reason"],
                source=DepartmentHourAllocationSource(
                    row.get(
                        "source", DepartmentHourAllocationSource.MANUAL_TRANSCRIPTION
                    )
                ),
                source_reference=row.get("source_reference"),
                received_at=HistoryController._parse_dt(row.get("received_at")),
                superseded_at=HistoryController._parse_dt(row.get("superseded_at")),
                created_by_user_id=uuid.UUID(str(current_user.id)),
            )
            session.add(revision)
            session.flush()
            mapping[str(row["id"])] = revision.id
        return mapping

    @staticmethod
    def _restore_subjects(
        session: Session, target: AssignmentProcess, snapshot: dict[str, Any]
    ) -> dict[str, uuid.UUID]:
        mapping: dict[str, uuid.UUID] = {}
        for row in HistoryController._list_section(snapshot, "subjects"):
            subject = Subject(
                assignment_process_id=target.id,
                name=row["name"],
                allocation_category=SubjectAllocationCategory(
                    row.get("allocation_category", SubjectAllocationCategory.MAIN)
                ),
                activity_type=ActivityType(
                    row.get("activity_type", ActivityType.ORDINARY)
                ),
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
    def _restore_group_subjects(
        session: Session,
        target: AssignmentProcess,
        snapshot: dict[str, Any],
        group_map: dict[str, uuid.UUID],
        subject_map: dict[str, uuid.UUID],
    ) -> dict[str, uuid.UUID]:
        mapping: dict[str, uuid.UUID] = {}
        for row in HistoryController._list_section(snapshot, "group_subjects"):
            cell = GroupSubject(
                assignment_process_id=target.id,
                teaching_group_id=group_map[str(row["teaching_group_id"])],
                subject_id=subject_map[str(row["subject_id"])],
                group_weekly_hours=row.get("group_weekly_hours"),
                teacher_weekly_hours_per_position=row.get(
                    "teacher_weekly_hours_per_position"
                ),
                required_teacher_count=row.get("required_teacher_count", 1),
                active=row.get("active", True),
                notes=row.get("notes"),
            )
            session.add(cell)
            session.flush()
            mapping[str(row["id"])] = cell.id
        return mapping

    @staticmethod
    def _restore_teachers(
        session: Session, target: AssignmentProcess, snapshot: dict[str, Any]
    ) -> dict[str, uuid.UUID]:
        """Restore participants with base and authorized-extra hours.

        The extra-hours audit pointer (actor + timestamp) is dropped — a restore
        carries no auth user attribution (plan §10.4) — while the numeric
        ``extra_weekly_hours`` and its reason are preserved.
        """
        mapping: dict[str, uuid.UUID] = {}
        for row in HistoryController._list_section(snapshot, "teachers"):
            teacher = ProcessTeacher(
                assignment_process_id=target.id,
                teacher_profile_id=uuid.UUID(str(row["teacher_profile_id"])),
                base_weekly_hours=row["base_weekly_hours"],
                extra_weekly_hours=row["extra_weekly_hours"],
                extra_hours_reason=row.get("extra_hours_reason"),
                extra_hours_updated_by_user_id=None,
                extra_hours_updated_at=None,
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
    def _restore_plan(
        session: Session,
        target: AssignmentProcess,
        snapshot: dict[str, Any],
        allocation_map: dict[str, uuid.UUID],
        current_user: UserModel,
        restore_slots: bool,
    ) -> Optional[TeachingPlan]:
        """Restore the teaching plan (plan §10.4).

        Feasibility is always reset to ``NOT_EVALUATED`` — a restore never trusts
        a stored feasibility result or witness (plan §20.25). When the requirement
        slots are NOT restored the generation markers are reset and any
        post-generation status is downgraded to the ``LOCKED`` baseline so the
        plan stays coherent with an empty slot set.
        """
        section = snapshot.get("teaching_plan")
        if section is None:
            return None
        source_status = TeachingPlanStatus(section["status"])
        if restore_slots:
            plan_status = source_status
            generation = int(section["current_generation_number"])
            stale_reason = section.get("stale_reason")
        else:
            plan_status = (
                source_status
                if source_status in _PRE_GENERATION_PLAN_STATUSES
                else TeachingPlanStatus.LOCKED
            )
            generation = 0
            stale_reason = None
        now = datetime.now(tz=timezone.utc)
        locked = plan_status in _LOCKED_PLAN_STATUSES
        generated = restore_slots and plan_status in _GENERATED_PLAN_STATUSES
        source_allocation_id = section.get("allocation_revision_id")
        plan = TeachingPlan(
            assignment_process_id=target.id,
            allocation_revision_id=(
                allocation_map.get(str(source_allocation_id))
                if source_allocation_id is not None
                else None
            ),
            status=plan_status,
            current_generation_number=generation,
            locked_at=now if locked else None,
            locked_by_user_id=uuid.UUID(str(current_user.id)) if locked else None,
            requirements_generated_at=now if generated else None,
            stale_reason=stale_reason,
            feasibility_status=FeasibilityStatus.NOT_EVALUATED,
        )
        session.add(plan)
        session.flush()
        return plan

    @staticmethod
    def _restore_activities(
        session: Session,
        plan: Optional[TeachingPlan],
        snapshot: dict[str, Any],
        subject_map: dict[str, uuid.UUID],
        cell_map: dict[str, uuid.UUID],
    ) -> dict[str, uuid.UUID]:
        mapping: dict[str, uuid.UUID] = {}
        if plan is None:
            return mapping
        for row in HistoryController._list_section(snapshot, "teaching_activities"):
            source_cell = row.get("source_group_subject_id")
            activity = TeachingActivity(
                teaching_plan_id=plan.id,
                subject_id=subject_map[str(row["subject_id"])],
                allocation_category=SubjectAllocationCategory(
                    row.get("allocation_category", SubjectAllocationCategory.SECONDARY)
                ),
                activity_type=ActivityType(
                    row.get("activity_type", ActivityType.ORDINARY)
                ),
                group_weekly_hours_per_group=row["group_weekly_hours_per_group"],
                teacher_weekly_hours_per_position=row[
                    "teacher_weekly_hours_per_position"
                ],
                required_teacher_count=row.get("required_teacher_count", 1),
                source=TeachingActivitySource(
                    row.get("source", TeachingActivitySource.SECONDARY_MANUAL)
                ),
                source_group_subject_id=(
                    cell_map[str(source_cell)] if source_cell is not None else None
                ),
                sync_state=TeachingActivitySyncState(
                    row.get("sync_state", TeachingActivitySyncState.IN_SYNC)
                ),
                retired_at=HistoryController._parse_dt(row.get("retired_at")),
                notes=row.get("notes"),
            )
            session.add(activity)
            session.flush()
            mapping[str(row["id"])] = activity.id
        return mapping

    @staticmethod
    def _restore_activity_groups(
        session: Session,
        snapshot: dict[str, Any],
        activity_map: dict[str, uuid.UUID],
        cell_map: dict[str, uuid.UUID],
    ) -> None:
        for row in HistoryController._list_section(
            snapshot, "teaching_activity_groups"
        ):
            session.add(
                TeachingActivityGroup(
                    teaching_activity_id=activity_map[str(row["teaching_activity_id"])],
                    group_subject_id=cell_map[str(row["group_subject_id"])],
                )
            )
        session.flush()

    @staticmethod
    def _restore_requirements(
        session: Session,
        target: AssignmentProcess,
        snapshot: dict[str, Any],
        activity_map: dict[str, uuid.UUID],
    ) -> dict[str, uuid.UUID]:
        """Restore the generated indivisible slots, remapping supersession links."""
        mapping: dict[str, uuid.UUID] = {}
        pending: list[tuple[HourRequirement, str]] = []
        for row in HistoryController._list_section(snapshot, "requirements"):
            requirement = HourRequirement(
                assignment_process_id=target.id,
                teaching_activity_id=activity_map[str(row["teaching_activity_id"])],
                position_index=row["position_index"],
                required_teacher_hours=row["required_teacher_hours"],
                created_generation=row["created_generation"],
                last_validated_generation=row["last_validated_generation"],
                retired_generation=row.get("retired_generation"),
                superseded_by_requirement_id=None,
                status=row.get("status", HourRequirementStatus.AVAILABLE),
            )
            session.add(requirement)
            session.flush()
            mapping[str(row["id"])] = requirement.id
            superseded = row.get("superseded_by_requirement_id")
            if superseded is not None:
                pending.append((requirement, str(superseded)))
        for requirement, old_superseded in pending:
            requirement.superseded_by_requirement_id = mapping[old_superseded]
            session.add(requirement)
        session.flush()
        return mapping

    @staticmethod
    def _restore_assignments(
        session: Session,
        target: AssignmentProcess,
        snapshot: dict[str, Any],
        requirement_map: dict[str, uuid.UUID],
        teacher_map: dict[str, uuid.UUID],
        activity_map: dict[str, uuid.UUID],
    ) -> None:
        """Restore slot occupancies as SYSTEM_COPY, dropping actor attribution."""
        for row in HistoryController._list_section(snapshot, "assignments"):
            session.add(
                Assignment(
                    assignment_process_id=target.id,
                    hour_requirement_id=requirement_map[
                        str(row["hour_requirement_id"])
                    ],
                    teaching_activity_id=activity_map[str(row["teaching_activity_id"])],
                    process_teacher_id=teacher_map[str(row["process_teacher_id"])],
                    source=AssignmentSource.SYSTEM_COPY,
                    status=row.get("status", AssignmentStatus.ACTIVE),
                    chosen_by_user_id=None,
                    confirmed_by_user_id=None,
                    notes=row.get("notes"),
                )
            )
        session.flush()

    @staticmethod
    def _parse_dt(value: Any) -> Optional[datetime]:
        return None if value is None else datetime.fromisoformat(str(value))

    # ── Rendering / gating ───────────────────────────────────────────────────

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
                writer.writerow(
                    [
                        "requirement",
                        row["id"],
                        row["required_teacher_hours"],
                        row["status"],
                    ]
                )
            for row in snapshot["assignments"]:
                writer.writerow(["assignment", row["id"], "", row["status"]])
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
        session: Session, process: AssignmentProcess
    ) -> None:
        report = AssignmentValidationService.compute_assignment_validations(
            session, process
        )
        if not report.is_final_ready:
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


__all__ = ["HistoryController"]
