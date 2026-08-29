"""Explicit GroupSubject to MAIN_GENERATED activity sync (plan §20.10).

The source cell and the materialized activity deliberately remain separate:
editing a source never overwrites planning values.  This service computes the
source/current diff, identifies assigned requirement slots an apply would
disturb, and produces a deterministic preview fingerprint for the controller's
explicit apply action.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal

from sqlmodel import Session, col, select

from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.group_subjects import GroupSubject
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teaching_activities import (
    MainActivityAssignmentImpact,
    MainActivitySyncDifference,
    MainActivitySyncPreview,
    MainActivitySyncValues,
    TeachingActivity,
)
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    AssignmentStatus,
    HourRequirementStatus,
    TeachingActivitySource,
    TeachingActivitySyncState,
)


#: The canonical two-place zero an unset override/default resolves to.
_ZERO_HOURS = Decimal("0.00")


def _resolved_hours(override: Decimal | None, default: Decimal | None) -> Decimal:
    """Resolve a cell override, its subject default, or the materialized zero."""

    if override is not None:
        return override
    if default is not None:
        return default
    return _ZERO_HOURS


class GroupSubjectSyncService:
    """Pure comparison plus focused persistence helpers for main-activity sync."""

    @staticmethod
    def live_main_activity(
        session: Session,
        source_group_subject_id: uuid.UUID,
        *,
        lock: bool = False,
    ) -> TeachingActivity | None:
        """Return the live main activity materialized from ``source_group_subject_id``."""

        statement = (
            select(TeachingActivity)
            .where(
                TeachingActivity.source_group_subject_id == source_group_subject_id,
                TeachingActivity.source == TeachingActivitySource.MAIN_GENERATED,
                col(TeachingActivity.retired_at).is_(None),
            )
            .order_by(col(TeachingActivity.id))
        )
        if lock:
            statement = statement.with_for_update()
        return session.exec(statement).first()

    @staticmethod
    def source_values(cell: GroupSubject, subject: Subject) -> MainActivitySyncValues:
        """Resolve the effective planning values represented by a source cell."""

        return MainActivitySyncValues(
            group_weekly_hours_per_group=_resolved_hours(
                cell.group_weekly_hours, subject.default_group_weekly_hours
            ),
            teacher_weekly_hours_per_position=_resolved_hours(
                cell.teacher_weekly_hours_per_position,
                subject.default_teacher_weekly_hours_per_position,
            ),
            required_teacher_count=cell.required_teacher_count,
        )

    @staticmethod
    def current_values(activity: TeachingActivity) -> MainActivitySyncValues:
        """Read the current materialized planning values from an activity."""

        return MainActivitySyncValues(
            group_weekly_hours_per_group=activity.group_weekly_hours_per_group,
            teacher_weekly_hours_per_position=(
                activity.teacher_weekly_hours_per_position
            ),
            required_teacher_count=activity.required_teacher_count,
        )

    @staticmethod
    def differences(
        source: MainActivitySyncValues,
        current: MainActivitySyncValues,
    ) -> list[MainActivitySyncDifference]:
        """Return deterministic source/current differences in contract order."""

        differences: list[MainActivitySyncDifference] = []
        for field_name in (
            "group_weekly_hours_per_group",
            "teacher_weekly_hours_per_position",
            "required_teacher_count",
        ):
            source_value = getattr(source, field_name)
            current_value = getattr(current, field_name)
            if source_value == current_value:
                continue
            differences.append(
                MainActivitySyncDifference(
                    field=field_name,
                    current_value=current_value,
                    source_value=source_value,
                )
            )
        return differences

    @staticmethod
    def mark_out_of_sync(
        session: Session,
        cell: GroupSubject,
        subject: Subject,
    ) -> tuple[TeachingActivity, TeachingActivity] | None:
        """Mark a changed materialized source OUT_OF_SYNC without overwriting it.

        Returns immutable ``(before, after)`` activity values only when the state
        actually transitions, so the caller can record one audit event.
        """

        activity = GroupSubjectSyncService.live_main_activity(session, cell.id)
        if activity is None:
            return None
        source = GroupSubjectSyncService.source_values(cell, subject)
        current = GroupSubjectSyncService.current_values(activity)
        needs_sync = not cell.active or bool(
            GroupSubjectSyncService.differences(source, current)
        )
        if (
            not needs_sync
            or activity.sync_state == TeachingActivitySyncState.OUT_OF_SYNC
        ):
            return None
        before = TeachingActivity.model_validate(activity.model_dump())
        activity.sync_state = TeachingActivitySyncState.OUT_OF_SYNC
        session.add(activity)
        return before, activity

    @staticmethod
    def preview(
        session: Session,
        plan: TeachingPlan,
        cell: GroupSubject,
        subject: Subject,
        activity: TeachingActivity,
    ) -> MainActivitySyncPreview:
        """Build the complete deterministic sync preview without mutating rows."""

        source = GroupSubjectSyncService.source_values(cell, subject)
        current = GroupSubjectSyncService.current_values(activity)
        differences = GroupSubjectSyncService.differences(source, current)
        impact = GroupSubjectSyncService.assignment_impact(
            session, cell, activity, source, differences
        )
        retirement_required = not cell.active
        fingerprint = GroupSubjectSyncService._fingerprint(
            plan,
            cell,
            activity,
            source,
            current,
            impact,
        )
        return MainActivitySyncPreview(
            group_subject_id=cell.id,
            teaching_activity_id=activity.id,
            sync_state=activity.sync_state,
            source_active=cell.active,
            source_values=source,
            current_values=current,
            differences=differences,
            assignment_impact=impact,
            retirement_required=retirement_required,
            is_noop=not differences and not retirement_required,
            preview_fingerprint=fingerprint,
        )

    @staticmethod
    def assignment_impact(
        session: Session,
        cell: GroupSubject,
        activity: TeachingActivity,
        source: MainActivitySyncValues,
        differences: list[MainActivitySyncDifference],
    ) -> MainActivityAssignmentImpact:
        """Identify assigned live slots changed or removed by the source values."""

        assigned = list(
            session.exec(
                select(HourRequirement)
                .join(
                    Assignment,
                    col(Assignment.hour_requirement_id) == col(HourRequirement.id),
                )
                .where(
                    HourRequirement.teaching_activity_id == activity.id,
                    col(HourRequirement.retired_generation).is_(None),
                    Assignment.status == AssignmentStatus.ACTIVE,
                )
                .order_by(col(HourRequirement.position_index), col(HourRequirement.id))
            ).all()
        )
        changed_fields = {difference.field for difference in differences}
        hours_changed = "teacher_weekly_hours_per_position" in changed_fields
        affected = [
            requirement
            for requirement in assigned
            if (
                not cell.active
                or hours_changed
                or requirement.position_index >= source.required_teacher_count
            )
        ]
        return MainActivityAssignmentImpact(
            active_assignment_count=len(assigned),
            affected_assignment_count=len(affected),
            affected_requirement_ids=[requirement.id for requirement in affected],
            requires_reconciliation=bool(affected),
        )

    @staticmethod
    def mark_requirements_for_reconciliation(
        session: Session, requirement_ids: list[uuid.UUID]
    ) -> None:
        """Put affected assigned slots on their explicit reconciliation path."""

        if not requirement_ids:
            return
        requirements = session.exec(
            select(HourRequirement).where(col(HourRequirement.id).in_(requirement_ids))
        ).all()
        for requirement in requirements:
            requirement.status = HourRequirementStatus.RECONCILIATION_REQUIRED
            session.add(requirement)

    @staticmethod
    def _fingerprint(
        plan: TeachingPlan,
        cell: GroupSubject,
        activity: TeachingActivity,
        source: MainActivitySyncValues,
        current: MainActivitySyncValues,
        impact: MainActivityAssignmentImpact,
    ) -> str:
        """Hash every mutable input whose drift must invalidate an apply."""

        payload = {
            "activity_id": str(activity.id),
            "activity_updated_at": activity.updated_at.isoformat(),
            "affected_requirement_ids": [
                str(requirement_id)
                for requirement_id in impact.affected_requirement_ids
            ],
            "current": current.model_dump(mode="json"),
            "group_subject_id": str(cell.id),
            "group_subject_updated_at": cell.updated_at.isoformat(),
            "plan_generation": plan.current_generation_number,
            "plan_status": plan.status.value,
            "source": source.model_dump(mode="json"),
            "source_active": cell.active,
        }
        serialized = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()


__all__ = ["GroupSubjectSyncService"]
