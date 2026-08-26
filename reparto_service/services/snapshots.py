"""Three-stage process snapshot and comparison service (plan §10.2, §10.3).

A *snapshot* is an immutable, JSON-safe capture of the full three-stage state of
one assignment process at a point in time. It is what a
:class:`~reparto_service.db_models.process_versions.ProcessVersion` stores and
what previous-year comparison diffs against. The three-stage adaptation replaces
the old single-global-balance snapshot (built on the retired ``SummaryService``)
with the plan §10.2 sections:

* the leadership **allocation revisions** and the current allocation;
* the **teaching plan** status, generation and reconciliation state;
* both independent **balances** (plan §3.1) and the per-participant assignment
  summary (participant base/extra/target hours);
* the **group-subject matrix**, the **teaching activities** with their linked
  group cells, and the generated **requirement** slots.

Every hour figure is a canonical two-place decimal string (plan §3.9): the
balance/summary sections come from the dual-balance calculation services (which
already quantize), and :meth:`SnapshotService.build_snapshot` serialises schemas
in JSON mode so a snapshot round-trips through the ``ProcessVersion.snapshot_json``
JSON column unchanged.

:meth:`SnapshotService.compare_snapshots` reduces two snapshots to the plan §10.3
comparison dimensions. It is pure of session state — it reads only the two
snapshot dicts — so a stored version and a virtual (live) snapshot compare
identically.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from reparto_service.core.decimals import quantize_hours
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.department_hour_allocation_revisions import (
    DepartmentHourAllocationRevision,
)
from reparto_service.db_models.group_subjects import GroupSubject
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.process_teachers import (
    ProcessTeacher,
    ProcessTeacherPublic,
)
from reparto_service.db_models.process_versions import VersionComparison
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teaching_activities import (
    TeachingActivity,
    TeachingActivityGroup,
)
from reparto_service.db_models.teaching_plans import TeachingPlan, TeachingPlanPublic
from reparto_service.services.calculations import (
    AssignmentCalculationService,
    PlanningCalculationService,
)

#: Canonical two-place zero used when a snapshot carries no hour value.
_ZERO = Decimal("0.00")

#: Top-level snapshot sections whose JSON is diffed for ``changed_sections``.
_COMPARED_SECTIONS = (
    "allocation_revisions",
    "teaching_plan",
    "subjects",
    "group_subjects",
    "teaching_activities",
    "requirements",
    "teachers",
)


class SnapshotService:
    """Build and compare three-stage process snapshots (plan §10.2, §10.3)."""

    # ── Snapshot building ─────────────────────────────────────────────────────

    @staticmethod
    def build_snapshot(session: Session, process_id: uuid.UUID) -> dict[str, Any]:
        """Capture the full three-stage state of a process (plan §10.2).

        Returns a JSON-safe dict with a stable key order and every list ordered
        deterministically, so two snapshots of identical state compare equal.
        The process must exist (404 otherwise); a process without a teaching
        plan yields ``teaching_plan``/``plan_balance`` of ``None`` and empty
        activity/requirement sections.
        """
        process = SnapshotService._process_or_404(session, process_id)
        plan = SnapshotService._plan(session, process_id)

        allocation = PlanningCalculationService.compute_current_allocation(
            session, process_id
        )
        assignment_summary = AssignmentCalculationService.compute_assignment_summary(
            session, process
        )

        return {
            "process": process.model_dump(mode="json"),
            "current_allocation": None if allocation is None else str(allocation),
            "allocation_revisions": SnapshotService._allocation_revisions(
                session, process_id
            ),
            "teaching_plan": (
                None
                if plan is None
                else TeachingPlanPublic.model_validate(plan).model_dump(mode="json")
            ),
            "plan_balance": (
                None
                if plan is None
                else PlanningCalculationService.compute_plan_balance(
                    session, plan
                ).model_dump(mode="json")
            ),
            "assignment_summary": assignment_summary.model_dump(mode="json"),
            "subjects": SnapshotService._subjects(session, process_id),
            "group_subjects": SnapshotService._group_subjects(session, process_id),
            "teaching_activities": SnapshotService._activities(session, plan),
            "requirements": SnapshotService._requirements(session, process_id),
            "teachers": SnapshotService._teachers(session, process_id),
        }

    @staticmethod
    def _process_or_404(session: Session, process_id: uuid.UUID) -> AssignmentProcess:
        process = session.get(AssignmentProcess, process_id)
        if process is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"AssignmentProcess {process_id} not found.",
            )
        return process

    @staticmethod
    def _plan(session: Session, process_id: uuid.UUID) -> Optional[TeachingPlan]:
        return session.exec(
            select(TeachingPlan).where(TeachingPlan.assignment_process_id == process_id)
        ).first()

    @staticmethod
    def _link_ids(session: Session, activity_id: uuid.UUID) -> list[uuid.UUID]:
        """Group-subject cell IDs an activity links, in a deterministic order."""
        return list(
            session.exec(
                select(TeachingActivityGroup.group_subject_id)
                .where(TeachingActivityGroup.teaching_activity_id == activity_id)
                .order_by(col(TeachingActivityGroup.group_subject_id))
            ).all()
        )

    @staticmethod
    def _allocation_revisions(
        session: Session, process_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        rows = session.exec(
            select(DepartmentHourAllocationRevision)
            .where(DepartmentHourAllocationRevision.assignment_process_id == process_id)
            .order_by(col(DepartmentHourAllocationRevision.revision_number))
        ).all()
        return [row.model_dump(mode="json") for row in rows]

    @staticmethod
    def _subjects(session: Session, process_id: uuid.UUID) -> list[dict[str, Any]]:
        rows = session.exec(
            select(Subject)
            .where(Subject.assignment_process_id == process_id)
            .order_by(col(Subject.id))
        ).all()
        return [row.model_dump(mode="json") for row in rows]

    @staticmethod
    def _group_subjects(
        session: Session, process_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        rows = session.exec(
            select(GroupSubject)
            .where(GroupSubject.assignment_process_id == process_id)
            .order_by(col(GroupSubject.id))
        ).all()
        return [row.model_dump(mode="json") for row in rows]

    @staticmethod
    def _activities(
        session: Session, plan: Optional[TeachingPlan]
    ) -> list[dict[str, Any]]:
        """Live teaching activities of the plan with linked cells and loads.

        Retired activities (``retired_at``) are excluded so the snapshot's
        activity set reflects the plan's live composition (plan §10.2); the
        linked ``group_subject_ids`` and both per-activity loads are captured for
        the §10.3 group-link and balance comparisons.
        """
        if plan is None:
            return []
        activities = session.exec(
            select(TeachingActivity)
            .where(TeachingActivity.teaching_plan_id == plan.id)
            .where(col(TeachingActivity.retired_at).is_(None))
            .order_by(col(TeachingActivity.id))
        ).all()
        result: list[dict[str, Any]] = []
        for activity in activities:
            link_ids = SnapshotService._link_ids(session, activity.id)
            group_load = PlanningCalculationService.compute_activity_group_load(
                activity, len(link_ids)
            )
            teacher_load = PlanningCalculationService.compute_activity_teacher_load(
                activity
            )
            result.append(
                {
                    "id": str(activity.id),
                    "subject_id": str(activity.subject_id),
                    "source": activity.source.value,
                    "allocation_category": activity.allocation_category.value,
                    "activity_type": activity.activity_type.value,
                    "group_weekly_hours_per_group": str(
                        quantize_hours(
                            Decimal(str(activity.group_weekly_hours_per_group))
                        )
                    ),
                    "teacher_weekly_hours_per_position": str(
                        quantize_hours(
                            Decimal(str(activity.teacher_weekly_hours_per_position))
                        )
                    ),
                    "required_teacher_count": activity.required_teacher_count,
                    "source_group_subject_id": (
                        None
                        if activity.source_group_subject_id is None
                        else str(activity.source_group_subject_id)
                    ),
                    "sync_state": activity.sync_state.value,
                    "group_subject_ids": [str(cell_id) for cell_id in link_ids],
                    "linked_group_count": len(link_ids),
                    "group_load": str(group_load),
                    "teacher_load": str(teacher_load),
                }
            )
        return result

    @staticmethod
    def _requirements(session: Session, process_id: uuid.UUID) -> list[dict[str, Any]]:
        """Live generated requirement slots, ordered by (activity, position).

        Retired slots (``retired_generation``) are excluded — a snapshot records
        the live generation, and the §10.3 "requirement generation changed"
        comparison keys off the live slot set plus the plan generation number.
        """
        rows = session.exec(
            select(HourRequirement)
            .where(HourRequirement.assignment_process_id == process_id)
            .where(col(HourRequirement.retired_generation).is_(None))
            .order_by(
                col(HourRequirement.teaching_activity_id),
                col(HourRequirement.position_index),
            )
        ).all()
        return [
            {
                "id": str(row.id),
                "teaching_activity_id": str(row.teaching_activity_id),
                "position_index": row.position_index,
                "required_teacher_hours": str(
                    quantize_hours(Decimal(str(row.required_teacher_hours)))
                ),
                "status": row.status.value,
                "created_generation": row.created_generation,
                "last_validated_generation": row.last_validated_generation,
            }
            for row in rows
        ]

    @staticmethod
    def _teachers(session: Session, process_id: uuid.UUID) -> list[dict[str, Any]]:
        rows = session.exec(
            select(ProcessTeacher)
            .where(ProcessTeacher.assignment_process_id == process_id)
            .order_by(col(ProcessTeacher.id))
        ).all()
        return [
            ProcessTeacherPublic.model_validate(row).model_dump(mode="json")
            for row in rows
        ]

    # ── Comparison (plan §10.3) ───────────────────────────────────────────────

    @staticmethod
    def compare_snapshots(
        left_version_id: uuid.UUID,
        right_version_id: uuid.UUID,
        left: dict[str, Any],
        right: dict[str, Any],
    ) -> VersionComparison:
        """Reduce two snapshots to the plan §10.3 comparison dimensions."""
        left_alloc = SnapshotService._opt_hours(left.get("current_allocation"))
        right_alloc = SnapshotService._opt_hours(right.get("current_allocation"))

        left_group = SnapshotService._balance_total(left, "group", "total_group_load")
        right_group = SnapshotService._balance_total(right, "group", "total_group_load")
        left_teacher = SnapshotService._balance_total(
            left, "teacher", "total_teacher_load"
        )
        right_teacher = SnapshotService._balance_total(
            right, "teacher", "total_teacher_load"
        )
        left_target = SnapshotService._target_total(left)
        right_target = SnapshotService._target_total(right)

        changed_sections = [
            section
            for section in _COMPARED_SECTIONS
            if left.get(section) != right.get(section)
        ]

        return VersionComparison(
            left_version_id=left_version_id,
            right_version_id=right_version_id,
            changed_sections=changed_sections,
            allocation_changed=left_alloc != right_alloc,
            group_hours_changed=left_group != right_group,
            teacher_load_changed=left_teacher != right_teacher,
            subject_category_changed=(
                SnapshotService._subject_categories(left)
                != SnapshotService._subject_categories(right)
            ),
            activity_added_or_removed=(
                SnapshotService._activity_ids(left)
                != SnapshotService._activity_ids(right)
            ),
            group_link_added_or_removed=(
                SnapshotService._group_links(left)
                != SnapshotService._group_links(right)
            ),
            teacher_position_count_changed=(
                SnapshotService._position_count(left)
                != SnapshotService._position_count(right)
            ),
            participant_target_changed=(
                SnapshotService._participant_targets(left)
                != SnapshotService._participant_targets(right)
            ),
            requirement_generation_changed=(
                SnapshotService._generation_fingerprint(left)
                != SnapshotService._generation_fingerprint(right)
            ),
            allocation_delta=(
                None
                if left_alloc is None or right_alloc is None
                else str(quantize_hours(right_alloc - left_alloc))
            ),
            group_load_delta=str(quantize_hours(right_group - left_group)),
            teacher_load_delta=str(quantize_hours(right_teacher - left_teacher)),
            participant_target_total_delta=str(
                quantize_hours(right_target - left_target)
            ),
            generation_number_delta=(
                SnapshotService._generation_number(right)
                - SnapshotService._generation_number(left)
            ),
            teacher_count_delta=(
                len(SnapshotService._list(right, "teachers"))
                - len(SnapshotService._list(left, "teachers"))
            ),
            activity_count_delta=(
                len(SnapshotService._list(right, "teaching_activities"))
                - len(SnapshotService._list(left, "teaching_activities"))
            ),
            requirement_count_delta=(
                len(SnapshotService._list(right, "requirements"))
                - len(SnapshotService._list(left, "requirements"))
            ),
        )

    # ── Comparison helpers ────────────────────────────────────────────────────

    @staticmethod
    def _list(snapshot: dict[str, Any], key: str) -> list[Any]:
        value = snapshot.get(key)
        return value if isinstance(value, list) else []

    @staticmethod
    def _opt_hours(value: Any) -> Optional[Decimal]:
        return None if value is None else quantize_hours(Decimal(str(value)))

    @staticmethod
    def _balance_total(snapshot: dict[str, Any], axis: str, field: str) -> Decimal:
        balance = snapshot.get("plan_balance")
        if not isinstance(balance, dict):
            return _ZERO
        return quantize_hours(Decimal(str(balance[axis][field])))

    @staticmethod
    def _target_total(snapshot: dict[str, Any]) -> Decimal:
        summary = snapshot.get("assignment_summary")
        if not isinstance(summary, dict):
            return _ZERO
        return quantize_hours(Decimal(str(summary["total_target_hours"])))

    @staticmethod
    def _subject_categories(snapshot: dict[str, Any]) -> dict[str, str]:
        return {
            str(row["id"]): str(row["allocation_category"])
            for row in SnapshotService._list(snapshot, "subjects")
        }

    @staticmethod
    def _activity_ids(snapshot: dict[str, Any]) -> set[str]:
        return {
            str(row["id"])
            for row in SnapshotService._list(snapshot, "teaching_activities")
        }

    @staticmethod
    def _group_links(snapshot: dict[str, Any]) -> set[tuple[str, str]]:
        links: set[tuple[str, str]] = set()
        for row in SnapshotService._list(snapshot, "teaching_activities"):
            activity_id = str(row["id"])
            for cell_id in row.get("group_subject_ids", []):
                links.add((activity_id, str(cell_id)))
        return links

    @staticmethod
    def _position_count(snapshot: dict[str, Any]) -> int:
        return sum(
            int(row["required_teacher_count"])
            for row in SnapshotService._list(snapshot, "teaching_activities")
        )

    @staticmethod
    def _participant_targets(snapshot: dict[str, Any]) -> dict[str, tuple[str, str]]:
        return {
            str(row["id"]): (
                str(row["base_weekly_hours"]),
                str(row["extra_weekly_hours"]),
            )
            for row in SnapshotService._list(snapshot, "teachers")
        }

    @staticmethod
    def _generation_number(snapshot: dict[str, Any]) -> int:
        plan = snapshot.get("teaching_plan")
        if not isinstance(plan, dict):
            return 0
        return int(plan["current_generation_number"])

    @staticmethod
    def _generation_fingerprint(
        snapshot: dict[str, Any],
    ) -> tuple[int, tuple[str, ...]]:
        slot_ids = tuple(
            str(row["id"]) for row in SnapshotService._list(snapshot, "requirements")
        )
        return SnapshotService._generation_number(snapshot), slot_ids


__all__ = ["SnapshotService"]
