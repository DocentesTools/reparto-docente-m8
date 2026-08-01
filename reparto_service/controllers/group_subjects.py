"""GroupSubject controller (per process).

CRUD logic for the intermediate group-subject matrix (plan §5.5, §7.2). Every
mutation validates that the referenced teaching group and subject both belong to
the URL process, enforces the per-process
``(assignment_process_id, teaching_group_id, subject_id)`` uniqueness and honours
the final/archived-process immutability guard (plan §8.4).

Editing a materialized source cell never overwrites its ``MAIN_GENERATED``
activity.  It marks the activity ``OUT_OF_SYNC`` and invalidates the plan until
the explicit sync-preview/apply flow is confirmed (plan §20.10). Guarded
retirement is an explicit guarded action under plan §20.12.
"""

from __future__ import annotations

import uuid

from auth_sdk_m8.schemas.user import UserModel
from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlmodel import Session, SQLModel, col, select

from reparto_service.controllers.base import DomainController
from reparto_service.controllers.teaching_activities import TeachingActivityController
from reparto_service.controllers.teaching_plans import TeachingPlanController
from reparto_service.db_models.classroom_stages import ClassroomStage
from reparto_service.db_models.group_subjects import (
    GroupSubject,
    GroupSubjectBulkApplyRequest,
    GroupSubjectBulkChange,
    GroupSubjectBulkConflict,
    GroupSubjectBulkPreview,
    GroupSubjectBulkRequest,
    GroupSubjectBulkResult,
    GroupSubjectCreate,
    GroupSubjectPublic,
    GroupSubjectsPublic,
    GroupSubjectUpdate,
)
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teaching_activities import (
    MainActivityAssignmentImpact,
    MainActivitySyncApplyRequest,
    MainActivitySyncPreview,
    MainActivitySyncResult,
    TeachingActivity,
    TeachingActivityGroup,
)
from reparto_service.db_models.teaching_groups import TeachingGroup
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    AssignmentProcessStatus,
    AuditEventType,
    GroupSubjectBulkMode,
    TeachingActivitySyncState,
    TeachingPlanStatus,
)
from reparto_service.services.calculations import PlanningCalculationService
from reparto_service.services.feasibility_witnesses import FeasibilityWitnessService
from reparto_service.services.group_subject_sync import GroupSubjectSyncService

# Planning-value fields a bulk operation may set on a cell.
_BULK_VALUE_FIELDS = (
    "group_weekly_hours",
    "teacher_weekly_hours_per_position",
    "required_teacher_count",
)


class _BulkAuditPayload(SQLModel):
    """Row-level detail recorded in the single ``bulk_applied`` audit event."""

    mode: str
    subject_id: str
    created: int
    updated: int
    rows: list[dict[str, object]]


class GroupSubjectController(DomainController):
    """CRUD logic for group-subject cells inside one assignment process."""

    @staticmethod
    def list_group_subjects(
        session: Session, process_id: uuid.UUID
    ) -> GroupSubjectsPublic:
        DomainController.get_process_or_404(session, process_id)
        statement = select(GroupSubject).where(
            GroupSubject.assignment_process_id == process_id
        )
        items = list(session.exec(statement).all())
        return GroupSubjectsPublic(
            data=[GroupSubjectPublic.model_validate(item) for item in items],
            count=len(items),
        )

    @staticmethod
    def get_group_subject(
        session: Session, process_id: uuid.UUID, group_subject_id: uuid.UUID
    ) -> GroupSubjectPublic:
        group_subject = GroupSubjectController._get_or_404(
            session, process_id, group_subject_id
        )
        return GroupSubjectPublic.model_validate(group_subject)

    @staticmethod
    def create_group_subject(
        session: Session,
        process_id: uuid.UUID,
        group_subject_in: GroupSubjectCreate,
        current_user: UserModel,
    ) -> GroupSubjectPublic:
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        if group_subject_in.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "assignment_process_id in the payload does not match the "
                    "URL process_id."
                ),
            )
        # Both references must live in the same process.
        GroupSubjectController._get_group_or_404(
            session, process_id, group_subject_in.teaching_group_id
        )
        GroupSubjectController._get_subject_or_404(
            session, process_id, group_subject_in.subject_id
        )
        group_subject = GroupSubject.model_validate(group_subject_in.model_dump())
        session.add(group_subject)
        GroupSubjectController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type=AuditEventType.GROUP_SUBJECT_CREATED,
            entity_type="group_subject",
            entity_id=group_subject.id,
            before=None,
            after=group_subject,
        )
        try:
            FeasibilityWitnessService.invalidate(session, process_id)
            session.commit()
        except Exception as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Could not create group-subject: this group/subject pair "
                    "is already configured in the process."
                ),
            ) from exc
        session.refresh(group_subject)
        return GroupSubjectPublic.model_validate(group_subject)

    @staticmethod
    def update_group_subject(
        session: Session,
        process_id: uuid.UUID,
        group_subject_id: uuid.UUID,
        group_subject_in: GroupSubjectUpdate,
        current_user: UserModel,
    ) -> GroupSubjectPublic:
        group_subject = GroupSubjectController._get_or_404(
            session, process_id, group_subject_id
        )
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        patch = group_subject_in.model_dump(exclude_unset=True)
        if patch.get("active") is False:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Use the explicit guarded retirement action to deactivate a "
                    "GroupSubject."
                ),
            )
        before = GroupSubject.model_validate(group_subject.model_dump())
        group_subject.sqlmodel_update(patch)
        session.add(group_subject)
        impact = GroupSubjectController._mark_source_activity_out_of_sync(
            session, process_id, group_subject, current_user
        )
        if impact is not None:
            GroupSubjectController._invalidate_plan_for_out_of_sync(
                session,
                process_id,
                requires_reconciliation=impact.requires_reconciliation,
            )
        GroupSubjectController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type=AuditEventType.GROUP_SUBJECT_UPDATED,
            entity_type="group_subject",
            entity_id=group_subject.id,
            before=before,
            after=group_subject,
        )
        FeasibilityWitnessService.invalidate(session, process_id)
        session.commit()
        session.refresh(group_subject)
        return GroupSubjectPublic.model_validate(group_subject)

    @staticmethod
    def retire_group_subject(
        session: Session,
        process_id: uuid.UUID,
        group_subject_id: uuid.UUID,
        current_user: UserModel,
    ) -> GroupSubjectPublic:
        """Retire a draft source cell only after all downstream activity retires."""

        group_subject = GroupSubjectController._get_or_404(
            session, process_id, group_subject_id, lock=True
        )
        process = DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        if process.status != AssignmentProcessStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="GroupSubject retirement is allowed only in a draft process.",
            )
        if not group_subject.active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The GroupSubject is already retired.",
            )
        live_activity = GroupSubjectController._live_downstream_activity(
            session, group_subject.id
        )
        if live_activity is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Retire the downstream teaching activity through its guarded "
                    "retirement and regeneration/reconciliation flow first."
                ),
            )
        before = GroupSubject.model_validate(group_subject.model_dump())
        group_subject.active = False
        session.add(group_subject)
        GroupSubjectController._advance_plan_after_retirement(session, process_id)
        GroupSubjectController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type=AuditEventType.GROUP_SUBJECT_RETIRED,
            entity_type="group_subject",
            entity_id=group_subject.id,
            before=before,
            after=group_subject,
        )
        FeasibilityWitnessService.invalidate(session, process_id)
        session.commit()
        session.refresh(group_subject)
        return GroupSubjectPublic.model_validate(group_subject)

    # ── Bulk preview / apply (plan §7.2, §8.4) ───────────────────────────────

    @staticmethod
    def bulk_preview(
        session: Session,
        process_id: uuid.UUID,
        request: GroupSubjectBulkRequest,
    ) -> GroupSubjectBulkPreview:
        """Dry-run a bulk operation without writing anything (plan §7.2)."""
        DomainController.get_process_or_404(session, process_id)
        GroupSubjectController._get_subject_or_404(
            session, process_id, request.subject_id
        )
        preview, _create_specs, _update_specs = GroupSubjectController._plan_bulk(
            session, process_id, request
        )
        return preview

    @staticmethod
    def bulk_apply(
        session: Session,
        process_id: uuid.UUID,
        request: GroupSubjectBulkApplyRequest,
        current_user: UserModel,
    ) -> GroupSubjectBulkResult:
        """Transactionally apply a previewed bulk operation (plan §7.2)."""
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        subject = GroupSubjectController._get_subject_or_404(
            session, process_id, request.subject_id
        )
        preview, create_specs, update_specs = GroupSubjectController._plan_bulk(
            session, process_id, request
        )
        if preview.validation_errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="; ".join(preview.validation_errors),
            )
        # Staleness guard: the confirmed count must still match the recomputed
        # plan, otherwise the underlying selection changed since preview.
        if preview.expected_affected_count != request.expected_affected_count:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Bulk selection changed since preview "
                    f"(now {preview.expected_affected_count} affected, "
                    f"confirmed {request.expected_affected_count}); re-preview."
                ),
            )
        affected: list[GroupSubject] = []
        rows_detail: list[dict[str, object]] = []
        for group_id, values in create_specs:
            row = GroupSubject(
                assignment_process_id=process_id,
                teaching_group_id=group_id,
                subject_id=subject.id,
                **values,
            )
            session.add(row)
            affected.append(row)
            rows_detail.append(
                {
                    "action": "create",
                    "teaching_group_id": str(group_id),
                    "after": values,
                }
            )
        for row, patch in update_specs:
            before = {field: getattr(row, field) for field in _BULK_VALUE_FIELDS}
            row.sqlmodel_update(patch)
            session.add(row)
            impact = GroupSubjectController._mark_source_activity_out_of_sync(
                session, process_id, row, current_user
            )
            if impact is not None:
                GroupSubjectController._invalidate_plan_for_out_of_sync(
                    session,
                    process_id,
                    requires_reconciliation=impact.requires_reconciliation,
                )
            affected.append(row)
            rows_detail.append(
                {
                    "action": "update",
                    "group_subject_id": str(row.id),
                    "teaching_group_id": str(row.teaching_group_id),
                    "before": before,
                    "after": {**before, **patch},
                }
            )
        GroupSubjectController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type=AuditEventType.GROUP_SUBJECT_BULK_APPLIED,
            entity_type="group_subject",
            entity_id=subject.id,
            before=None,
            after=_BulkAuditPayload(
                mode=request.mode.value,
                subject_id=str(subject.id),
                created=len(create_specs),
                updated=len(update_specs),
                rows=rows_detail,
            ),
        )
        FeasibilityWitnessService.invalidate(session, process_id)
        session.commit()
        for row in affected:
            session.refresh(row)
        return GroupSubjectBulkResult(
            created_count=len(create_specs),
            updated_count=len(update_specs),
            data=[GroupSubjectPublic.model_validate(row) for row in affected],
            count=len(affected),
        )

    # ── MAIN_GENERATED source sync (plan §20.10) ───────────────────────────

    @staticmethod
    def sync_preview(
        session: Session,
        process_id: uuid.UUID,
        group_subject_id: uuid.UUID,
    ) -> MainActivitySyncPreview:
        """Preview source/current differences and assigned-slot impact."""

        cell = GroupSubjectController._get_or_404(session, process_id, group_subject_id)
        subject = GroupSubjectController._get_subject_or_404(
            session, process_id, cell.subject_id
        )
        activity = GroupSubjectSyncService.live_main_activity(session, cell.id)
        if activity is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This GroupSubject has no live MAIN_GENERATED activity to sync."
                ),
            )
        plan = GroupSubjectController._plan_for_activity(session, process_id, activity)
        return GroupSubjectSyncService.preview(session, plan, cell, subject, activity)

    @staticmethod
    def sync_apply(
        session: Session,
        process_id: uuid.UUID,
        group_subject_id: uuid.UUID,
        request: MainActivitySyncApplyRequest,
        current_user: UserModel,
    ) -> MainActivitySyncResult:
        """Explicitly apply a fresh source preview to its main activity."""

        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        cell = GroupSubjectController._get_or_404(
            session, process_id, group_subject_id, lock=True
        )
        subject = GroupSubjectController._get_subject_or_404(
            session, process_id, cell.subject_id
        )
        activity = GroupSubjectSyncService.live_main_activity(
            session, cell.id, lock=True
        )
        if activity is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This GroupSubject has no live MAIN_GENERATED activity to sync."
                ),
            )
        plan = GroupSubjectController._plan_for_activity(
            session, process_id, activity, lock=True
        )
        preview = GroupSubjectSyncService.preview(
            session, plan, cell, subject, activity
        )
        if preview.preview_fingerprint != request.expected_preview_fingerprint:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The sync inputs changed since preview; re-run sync-preview.",
            )
        if preview.retirement_required:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "The source GroupSubject is inactive; use the explicit guarded "
                    "activity-retirement flow instead of sync-apply."
                ),
            )

        before = TeachingActivity.model_validate(activity.model_dump())
        was_noop = (
            preview.is_noop and activity.sync_state == TeachingActivitySyncState.IN_SYNC
        )
        activity.group_weekly_hours_per_group = (
            preview.source_values.group_weekly_hours_per_group
        )
        activity.teacher_weekly_hours_per_position = (
            preview.source_values.teacher_weekly_hours_per_position
        )
        activity.required_teacher_count = preview.source_values.required_teacher_count
        activity.sync_state = TeachingActivitySyncState.IN_SYNC
        session.add(activity)
        GroupSubjectSyncService.mark_requirements_for_reconciliation(
            session, preview.assignment_impact.affected_requirement_ids
        )

        if not was_noop:
            GroupSubjectController.record_audit_event(
                session,
                process_id=process_id,
                current_user=current_user,
                event_type=AuditEventType.TEACHING_ACTIVITY_SYNC_APPLIED,
                entity_type="teaching_activity",
                entity_id=activity.id,
                before=before,
                after=activity,
            )
            FeasibilityWitnessService.invalidate(session, process_id)

        if not was_noop:
            session.flush()
            GroupSubjectController._advance_plan_after_sync(
                session, plan, preview.assignment_impact
            )
        session.commit()
        session.refresh(activity)
        session.refresh(plan)
        return MainActivitySyncResult(
            activity=TeachingActivityController._to_public(session, activity),
            applied_differences=preview.differences,
            assignment_impact=preview.assignment_impact,
            teaching_plan_status=plan.status,
            was_noop=was_noop,
        )

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _get_or_404(
        session: Session,
        process_id: uuid.UUID,
        group_subject_id: uuid.UUID,
        *,
        lock: bool = False,
    ) -> GroupSubject:
        DomainController.get_process_or_404(session, process_id)
        statement = select(GroupSubject).where(GroupSubject.id == group_subject_id)
        if lock:
            statement = statement.with_for_update()
        group_subject = session.exec(statement).first()
        if group_subject is None or group_subject.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"GroupSubject {group_subject_id} not found in process "
                    f"{process_id}."
                ),
            )
        return group_subject

    @staticmethod
    def _mark_source_activity_out_of_sync(
        session: Session,
        process_id: uuid.UUID,
        cell: GroupSubject,
        current_user: UserModel,
    ) -> MainActivityAssignmentImpact | None:
        """Mark a materialized source OUT_OF_SYNC and audit the transition."""

        subject = GroupSubjectController._get_subject_or_404(
            session, process_id, cell.subject_id
        )
        transition = GroupSubjectSyncService.mark_out_of_sync(session, cell, subject)
        if transition is None:
            return None
        before, activity = transition
        plan = GroupSubjectController._plan_for_activity(session, process_id, activity)
        preview = GroupSubjectSyncService.preview(
            session, plan, cell, subject, activity
        )
        GroupSubjectController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type=AuditEventType.TEACHING_ACTIVITY_OUT_OF_SYNC,
            entity_type="teaching_activity",
            entity_id=activity.id,
            before=before,
            after=activity,
        )
        return preview.assignment_impact

    @staticmethod
    def _live_downstream_activity(
        session: Session, group_subject_id: uuid.UUID
    ) -> TeachingActivity | None:
        """Return any live activity sourced from or linked to this cell."""

        return session.exec(
            select(TeachingActivity)
            .outerjoin(
                TeachingActivityGroup,
                col(TeachingActivityGroup.teaching_activity_id)
                == col(TeachingActivity.id),
            )
            .where(
                or_(
                    col(TeachingActivity.source_group_subject_id) == group_subject_id,
                    col(TeachingActivityGroup.group_subject_id) == group_subject_id,
                )
            )
            .where(col(TeachingActivity.retired_at).is_(None))
            .order_by(col(TeachingActivity.id))
            .with_for_update()
        ).first()

    @staticmethod
    def _advance_plan_after_retirement(session: Session, process_id: uuid.UUID) -> None:
        plan = session.exec(
            select(TeachingPlan)
            .where(TeachingPlan.assignment_process_id == process_id)
            .with_for_update()
        ).first()
        if plan is None or plan.status not in {
            TeachingPlanStatus.DRAFT,
            TeachingPlanStatus.UNBALANCED,
            TeachingPlanStatus.BALANCED,
        }:
            return
        exact = PlanningCalculationService.compute_plan_balance(session, plan).is_exact
        target = TeachingPlanStatus.BALANCED if exact else TeachingPlanStatus.UNBALANCED
        if plan.status != target:
            TeachingPlanController.apply_status_transition(plan, target)
        session.add(plan)

    @staticmethod
    def _invalidate_plan_for_out_of_sync(
        session: Session,
        process_id: uuid.UUID,
        *,
        requires_reconciliation: bool,
    ) -> None:
        """Reflect an OUT_OF_SYNC source in the operational plan lifecycle."""

        plan = session.exec(
            select(TeachingPlan).where(TeachingPlan.assignment_process_id == process_id)
        ).first()
        if plan is None:
            return
        reason = "A MAIN_GENERATED activity is out of sync with its source."
        if plan.status == TeachingPlanStatus.BALANCED:
            TeachingPlanController.apply_status_transition(
                plan, TeachingPlanStatus.UNBALANCED
            )
        elif plan.status == TeachingPlanStatus.LOCKED:
            TeachingPlanController.apply_status_transition(
                plan, TeachingPlanStatus.STALE, stale_reason=reason
            )
        elif plan.status == TeachingPlanStatus.REQUIREMENTS_GENERATED:
            target = (
                TeachingPlanStatus.RECONCILIATION_REQUIRED
                if requires_reconciliation
                else TeachingPlanStatus.STALE
            )
            TeachingPlanController.apply_status_transition(
                plan,
                target,
                stale_reason=reason if target == TeachingPlanStatus.STALE else None,
            )
            if target == TeachingPlanStatus.RECONCILIATION_REQUIRED:
                plan.stale_reason = reason
        session.add(plan)

    @staticmethod
    def _advance_plan_after_sync(
        session: Session,
        plan: TeachingPlan,
        impact: MainActivityAssignmentImpact,
    ) -> None:
        """Recalculate unlocked plans or route generated changes to regeneration."""

        if plan.status in {
            TeachingPlanStatus.DRAFT,
            TeachingPlanStatus.UNBALANCED,
            TeachingPlanStatus.BALANCED,
        }:
            exact = PlanningCalculationService.compute_plan_balance(
                session, plan
            ).is_exact
            target = (
                TeachingPlanStatus.BALANCED if exact else TeachingPlanStatus.UNBALANCED
            )
            if plan.status != target:
                TeachingPlanController.apply_status_transition(plan, target)
            return
        reason = "MAIN_GENERATED activity values changed during source sync."
        if plan.status == TeachingPlanStatus.LOCKED:
            TeachingPlanController.apply_status_transition(
                plan, TeachingPlanStatus.STALE, stale_reason=reason
            )
        elif plan.status == TeachingPlanStatus.REQUIREMENTS_GENERATED:
            target = (
                TeachingPlanStatus.RECONCILIATION_REQUIRED
                if impact.requires_reconciliation
                else TeachingPlanStatus.STALE
            )
            TeachingPlanController.apply_status_transition(
                plan,
                target,
                stale_reason=reason if target == TeachingPlanStatus.STALE else None,
            )
            if target == TeachingPlanStatus.RECONCILIATION_REQUIRED:
                plan.stale_reason = reason
        session.add(plan)

    @staticmethod
    def _plan_for_activity(
        session: Session,
        process_id: uuid.UUID,
        activity: TeachingActivity,
        *,
        lock: bool = False,
    ) -> TeachingPlan:
        statement = select(TeachingPlan).where(
            TeachingPlan.assignment_process_id == process_id,
            TeachingPlan.id == activity.teaching_plan_id,
        )
        if lock:
            statement = statement.with_for_update()
        plan = session.exec(statement).first()
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The materialized activity has no owning teaching plan.",
            )
        return plan

    @staticmethod
    def _get_group_or_404(
        session: Session, process_id: uuid.UUID, group_id: uuid.UUID
    ) -> TeachingGroup:
        statement = select(TeachingGroup).where(TeachingGroup.id == group_id)
        group = session.exec(statement).first()
        if group is None or group.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(f"TeachingGroup {group_id} not found in process {process_id}."),
            )
        return group

    @staticmethod
    def _get_subject_or_404(
        session: Session, process_id: uuid.UUID, subject_id: uuid.UUID
    ) -> Subject:
        statement = select(Subject).where(Subject.id == subject_id)
        subject = session.exec(statement).first()
        if subject is None or subject.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(f"Subject {subject_id} not found in process {process_id}."),
            )
        return subject

    @staticmethod
    def _plan_bulk(
        session: Session,
        process_id: uuid.UUID,
        request: GroupSubjectBulkRequest,
    ) -> tuple[
        GroupSubjectBulkPreview,
        list[tuple[uuid.UUID, dict[str, object]]],
        list[tuple[GroupSubject, dict[str, object]]],
    ]:
        """Compute the create/update/unchanged/conflict split for a bulk request.

        Returns the preview alongside the concrete work lists apply consumes:
        ``create_specs`` as ``(teaching_group_id, create_values)`` and
        ``update_specs`` as ``(existing_row, patch)``.
        """
        groups, errors = GroupSubjectController._matched_groups(
            session, process_id, request
        )
        existing = {
            row.teaching_group_id: row
            for row in session.exec(
                select(GroupSubject).where(
                    GroupSubject.assignment_process_id == process_id,
                    GroupSubject.subject_id == request.subject_id,
                )
            ).all()
        }
        provided = {
            field: getattr(request, field)
            for field in _BULK_VALUE_FIELDS
            if field in request.model_fields_set
        }
        create_values = GroupSubjectController._create_values(provided)

        to_create: list[GroupSubjectBulkChange] = []
        to_update: list[GroupSubjectBulkChange] = []
        unchanged: list[GroupSubjectBulkChange] = []
        conflicts: list[GroupSubjectBulkConflict] = []
        create_specs: list[tuple[uuid.UUID, dict[str, object]]] = []
        update_specs: list[tuple[GroupSubject, dict[str, object]]] = []

        for group in groups:
            row = existing.get(group.id)
            if row is None:
                if request.mode == GroupSubjectBulkMode.UPDATE_EXISTING:
                    conflicts.append(
                        GroupSubjectBulkConflict(
                            teaching_group_id=group.id,
                            reason="No existing group-subject row to update.",
                        )
                    )
                    continue
                create_specs.append((group.id, create_values))
                to_create.append(
                    GroupSubjectBulkChange(teaching_group_id=group.id, **create_values)
                )
                continue
            if request.mode == GroupSubjectBulkMode.CREATE_MISSING:
                unchanged.append(GroupSubjectController._row_change(row))
                continue
            patch = {
                field: value
                for field, value in provided.items()
                if getattr(row, field) != value
            }
            if patch:
                update_specs.append((row, patch))
                to_update.append(GroupSubjectController._row_change(row, patch))
            else:
                unchanged.append(GroupSubjectController._row_change(row))

        preview = GroupSubjectBulkPreview(
            mode=request.mode,
            subject_id=request.subject_id,
            matched_group_ids=[group.id for group in groups],
            to_create=to_create,
            to_update=to_update,
            unchanged=unchanged,
            conflicts=conflicts,
            validation_errors=errors,
            expected_affected_count=len(to_create) + len(to_update),
        )
        return preview, create_specs, update_specs

    @staticmethod
    def _matched_groups(
        session: Session,
        process_id: uuid.UUID,
        request: GroupSubjectBulkRequest,
    ) -> tuple[list[TeachingGroup], list[str]]:
        """Resolve the groups a bulk request targets and any selection errors."""
        errors: list[str] = []
        if (
            request.minimum_grade is not None
            and request.maximum_grade is not None
            and request.minimum_grade > request.maximum_grade
        ):
            errors.append("minimum_grade must be less than or equal to maximum_grade.")
            return [], errors
        statement = select(TeachingGroup).where(
            TeachingGroup.assignment_process_id == process_id
        )
        if request.minimum_grade is not None:
            statement = statement.where(
                col(TeachingGroup.grade) >= request.minimum_grade
            )
        if request.maximum_grade is not None:
            statement = statement.where(
                col(TeachingGroup.grade) <= request.maximum_grade
            )
        if request.stage is not None:
            stage = " ".join(request.stage.split())
            statement = statement.join(
                ClassroomStage,
                col(TeachingGroup.classroom_stage_id) == col(ClassroomStage.id),
            ).where(ClassroomStage.stage == stage)
        statement = statement.order_by(
            col(TeachingGroup.grade), col(TeachingGroup.group_code)
        )
        return list(session.exec(statement).all()), errors

    @staticmethod
    def _create_values(provided: dict[str, object]) -> dict[str, object]:
        """Resolve the concrete field values for a newly created cell.

        Unset hour fields inherit (NULL); an unset count falls back to 1.
        """
        return {
            "group_weekly_hours": provided.get("group_weekly_hours"),
            "teacher_weekly_hours_per_position": provided.get(
                "teacher_weekly_hours_per_position"
            ),
            "required_teacher_count": provided.get("required_teacher_count", 1),
        }

    @staticmethod
    def _row_change(
        row: GroupSubject, patch: dict[str, object] | None = None
    ) -> GroupSubjectBulkChange:
        """Build a preview change carrying a row's resulting field values."""
        values = {field: getattr(row, field) for field in _BULK_VALUE_FIELDS}
        if patch:
            values.update(patch)
        return GroupSubjectBulkChange(
            teaching_group_id=row.teaching_group_id,
            group_subject_id=row.id,
            **values,
        )


__all__ = ["GroupSubjectController"]
