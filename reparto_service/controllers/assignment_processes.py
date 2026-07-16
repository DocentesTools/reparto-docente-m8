"""AssignmentProcess controller.

Owns CRUD plus the lifecycle operations introduced for the Phase 1 state
machine (plan §8.4, §10.2, §14.1):

* ``transition_process`` — moves the process through the documented
  status edges, tracking ``closed_at`` / ``closed_by_user_id`` for the
  ``final`` edge.
* ``reopen_process`` — explicit ``final`` → ``reopened`` edge with a
  mandatory reason; clears the close metadata.
* ``copy_from_process`` — copies the *configuration* of a previous-year
  process (subjects, groups, group-subject cells, participants) into a
  fresh ``draft`` process, optionally carrying the secondary-activity
  templates. It never activates the previous leadership allocation and
  never copies assignments, meetings, turns or extra-hour approvals
  (plan §10.1).

``update_process`` no longer accepts ``status``: the field is owned by
the transition endpoint so the table cannot be bypassed with a
malformed PATCH.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlmodel import Session, func, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.academic_years import AcademicYear
from reparto_service.db_models.assignment_processes import (
    AssignmentProcess,
    AssignmentProcessCreate,
    AssignmentProcessPublic,
    AssignmentProcessesPublic,
    AssignmentProcessUpdate,
    ProcessCopyRequest,
    ProcessReopenRequest,
    ProcessTransitionRequest,
)
from reparto_service.db_models.departments import Department
from reparto_service.db_models.group_subjects import GroupSubject
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.schools import School
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teaching_activities import (
    TeachingActivity,
    TeachingActivityGroup,
)
from reparto_service.db_models.teaching_groups import TeachingGroup
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    AssignmentProcessStatus,
    TeachingActivitySource,
    TeachingPlanStatus,
)
from reparto_service.services.process_lifecycle import (
    IllegalTransitionError,
    assert_allowed_transition,
    is_closing_transition,
    is_reopen_edge,
)


class AssignmentProcessController(DomainController):
    """CRUD and lifecycle logic for assignment processes."""

    @staticmethod
    def list_processes(
        session: Session,
        academic_year_id: uuid.UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> AssignmentProcessesPublic:
        count_stmt = select(func.count()).select_from(AssignmentProcess)
        list_stmt = select(AssignmentProcess)
        if academic_year_id is not None:
            count_stmt = count_stmt.where(
                AssignmentProcess.academic_year_id == academic_year_id
            )
            list_stmt = list_stmt.where(
                AssignmentProcess.academic_year_id == academic_year_id
            )
        count = session.exec(count_stmt).one()
        items = list(session.exec(list_stmt.offset(skip).limit(limit)).all())
        return AssignmentProcessesPublic(
            data=[AssignmentProcessPublic.model_validate(item) for item in items],
            count=count,
        )

    @staticmethod
    def get_process(session: Session, process_id: uuid.UUID) -> AssignmentProcessPublic:
        process = DomainController.get_process_or_404(session, process_id)
        return AssignmentProcessPublic.model_validate(process)

    @staticmethod
    def create_process(
        session: Session,
        current_user: UserModel,
        process_in: AssignmentProcessCreate,
    ) -> AssignmentProcessPublic:
        DomainController.get_or_404(session, AcademicYear, process_in.academic_year_id)
        DomainController.get_or_404(session, School, process_in.school_id)
        DomainController.get_or_404(session, Department, process_in.department_id)
        process = AssignmentProcess.model_validate(
            process_in.model_dump(),
            update={"created_by_user_id": current_user.id},
        )
        session.add(process)
        AssignmentProcessController.record_audit_event(
            session,
            process_id=process.id,
            current_user=current_user,
            event_type="process.created",
            entity_type="assignment_process",
            entity_id=process.id,
            before=None,
            after=process,
        )
        session.commit()
        session.refresh(process)
        return AssignmentProcessPublic.model_validate(process)

    @staticmethod
    def update_process(
        session: Session,
        process_id: uuid.UUID,
        process_in: AssignmentProcessUpdate,
        current_user: UserModel,
    ) -> AssignmentProcessPublic:
        process = DomainController.get_process_or_404(session, process_id)
        before = AssignmentProcess.model_validate(process.model_dump())
        update_dict = process_in.model_dump(exclude_unset=True)
        if "status" in update_dict:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Process status is owned by the transition endpoint. "
                    "POST /assignment-processes/{id}/transition instead."
                ),
            )
        process.sqlmodel_update(update_dict)
        session.add(process)
        AssignmentProcessController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="process.updated",
            entity_type="assignment_process",
            entity_id=process.id,
            before=before,
            after=process,
        )
        session.commit()
        session.refresh(process)
        return AssignmentProcessPublic.model_validate(process)

    # ── Lifecycle (plan §8.4, §10.2) ────────────────────────────────────────

    @staticmethod
    def transition_process(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
        request: ProcessTransitionRequest,
    ) -> AssignmentProcessPublic:
        """Apply a status transition through the documented state machine.

        Refuses the reopen edge (``final`` → ``reopened``); reopen must go
        through :meth:`reopen_process` so a reason is always recorded.
        When the transition closes the process (``is_close_edge``), the
        controller records ``closed_at`` and ``closed_by_user_id``.
        """
        process = DomainController.get_process_or_404(session, process_id)
        before = AssignmentProcess.model_validate(process.model_dump())
        current = process.status
        target = request.target_status
        if is_reopen_edge(current, target):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Reopen must go through POST "
                    "/assignment-processes/{id}/reopen with a reason."
                ),
            )
        try:
            assert_allowed_transition(current, target)
        except IllegalTransitionError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        process.status = target
        if is_closing_transition(current, target):
            process.closed_at = datetime.now(tz=timezone.utc)
            process.closed_by_user_id = uuid.UUID(str(current_user.id))
        session.add(process)
        AssignmentProcessController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="process.transitioned",
            entity_type="assignment_process",
            entity_id=process.id,
            before=before,
            after=process,
            reason=target.value,
        )
        session.commit()
        session.refresh(process)
        return AssignmentProcessPublic.model_validate(process)

    @staticmethod
    def reopen_process(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
        request: ProcessReopenRequest,
    ) -> AssignmentProcessPublic:
        """Apply the explicit ``final`` → ``reopened`` edge.

        Clears ``closed_at`` and ``closed_by_user_id``; the reopen reason
        is required at the schema level. The actor is recorded implicitly
        through the transition — full ``AuditEvent`` rows are a post-MVP
        item (plan §8.14). The ``current_user`` and ``request`` arguments
        are kept on the signature for that future audit-event
        integration; today they are unused apart from validating the
        reason at the schema layer.
        """
        process = DomainController.get_process_or_404(session, process_id)
        before = AssignmentProcess.model_validate(process.model_dump())
        current = process.status
        target = AssignmentProcessStatus.REOPENED
        if not is_reopen_edge(current, target):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=("Only processes in status 'final' can be reopened."),
            )
        process.status = target
        process.closed_at = None
        process.closed_by_user_id = None
        session.add(process)
        AssignmentProcessController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="process.reopened",
            entity_type="assignment_process",
            entity_id=process.id,
            before=before,
            after=process,
            reason=request.reason,
        )
        session.commit()
        session.refresh(process)
        return AssignmentProcessPublic.model_validate(process)

    @staticmethod
    def copy_from_process(
        session: Session,
        target_process_id: uuid.UUID,
        source_process_id: uuid.UUID,
        request: ProcessCopyRequest,
        current_user: UserModel,
    ) -> AssignmentProcessPublic:
        """Copy the configuration of a source process into a fresh draft.

        The target process must:

        * exist,
        * belong to the same school as the source,
        * be in ``draft``,
        * currently be empty (no participants, subjects, groups, group-subject
          cells or teaching plan).

        The configuration always copied is: subjects and their defaults,
        teaching groups, group-subject cells and participants — the latter
        with base hours preserved but **extra-hour approvals dropped**
        (``extra_weekly_hours`` reset to ``0`` and the extra-hours audit
        pointer cleared, plan §10.1). The selection-order fields are kept
        from the source. The previous leadership allocation is **never**
        activated (no allocation revision is copied); assignments, meetings,
        turns and extra-hour approvals are **never** copied. When
        ``copy_activities`` is true, the source plan's live secondary-activity
        templates are additionally copied into a fresh ``draft`` teaching plan
        (plan §10.1).
        """
        target = DomainController.get_process_or_404(session, target_process_id)
        source = DomainController.get_process_or_404(session, source_process_id)
        before = AssignmentProcess.model_validate(target.model_dump())
        if target.id == source.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Source and target process must differ.",
            )
        if target.school_id != source.school_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=("Source and target processes must belong to the same school."),
            )
        if target.status != AssignmentProcessStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=("Copy is only allowed into a process in status 'draft'."),
            )
        AssignmentProcessController._ensure_target_empty(session, target.id)
        subject_map, cell_map = AssignmentProcessController._copy_structure(
            session, source, target
        )
        if request.copy_activities:
            AssignmentProcessController._copy_activity_templates(
                session, source, target, subject_map, cell_map
            )
        target.created_from_process_id = source.id
        session.add(target)
        AssignmentProcessController.record_audit_event(
            session,
            process_id=target_process_id,
            current_user=current_user,
            event_type="process.copied_from_previous_year",
            entity_type="assignment_process",
            entity_id=target.id,
            before=before,
            after=target,
            reason=str(source.id),
        )
        session.commit()
        session.refresh(target)
        return AssignmentProcessPublic.model_validate(target)

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _ensure_target_empty(session: Session, target_id: uuid.UUID) -> None:
        """Refuse the copy if the target process already carries data."""
        if (
            session.exec(
                select(func.count())
                .select_from(ProcessTeacher)
                .where(ProcessTeacher.assignment_process_id == target_id)
            ).one()
            > 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Target process already has teachers.",
            )
        if (
            session.exec(
                select(func.count())
                .select_from(Subject)
                .where(Subject.assignment_process_id == target_id)
            ).one()
            > 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Target process already has subjects.",
            )
        if (
            session.exec(
                select(func.count())
                .select_from(TeachingGroup)
                .where(TeachingGroup.assignment_process_id == target_id)
            ).one()
            > 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Target process already has teaching groups.",
            )
        if (
            session.exec(
                select(func.count())
                .select_from(GroupSubject)
                .where(GroupSubject.assignment_process_id == target_id)
            ).one()
            > 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Target process already has group-subject cells.",
            )
        if (
            session.exec(
                select(func.count())
                .select_from(TeachingPlan)
                .where(TeachingPlan.assignment_process_id == target_id)
            ).one()
            > 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Target process already has a teaching plan.",
            )

    @staticmethod
    def _copy_structure(
        session: Session, source: AssignmentProcess, target: AssignmentProcess
    ) -> tuple[dict[uuid.UUID, uuid.UUID], dict[uuid.UUID, uuid.UUID]]:
        """Copy the configuration structure (plan §10.1).

        Copies subjects and their defaults, teaching groups, group-subject
        cells and participants. ``ProcessTeacher`` rows are copied one-to-one:
        the same ``teacher_profile_id`` and the selection-order fields are
        preserved, ``base_weekly_hours`` is carried, but the extra-hour
        approval is dropped (``extra_weekly_hours`` reset to ``0`` and the
        extra-hours audit pointer cleared) so no prior authorization survives
        into the new year (plan §10.1). Generated hour requirements and
        assignments are deliberately NOT copied — requirements are regenerated
        from the plan and assignments never carry over.

        Returns the source→target id maps for subjects and group-subject cells
        so the optional activity-template copy can remap its references.
        """
        subject_map: dict[uuid.UUID, uuid.UUID] = {}
        group_map: dict[uuid.UUID, uuid.UUID] = {}
        cell_map: dict[uuid.UUID, uuid.UUID] = {}
        for subject in session.exec(
            select(Subject).where(Subject.assignment_process_id == source.id)
        ).all():
            copied_subject = Subject(
                assignment_process_id=target.id,
                name=subject.name,
                allocation_category=subject.allocation_category,
                activity_type=subject.activity_type,
                default_group_weekly_hours=subject.default_group_weekly_hours,
                default_teacher_weekly_hours_per_position=(
                    subject.default_teacher_weekly_hours_per_position
                ),
                default_required_teacher_count=subject.default_required_teacher_count,
                allows_multiple_groups=subject.allows_multiple_groups,
                allows_zero_groups=subject.allows_zero_groups,
                notes=subject.notes,
            )
            subject_map[subject.id] = copied_subject.id
            session.add(copied_subject)
        for group in session.exec(
            select(TeachingGroup).where(
                TeachingGroup.assignment_process_id == source.id
            )
        ).all():
            copied_group = TeachingGroup(
                assignment_process_id=target.id,
                classroom_stage_id=group.classroom_stage_id,
                grade=group.grade,
                group_code=group.group_code,
                label=group.label,
                notes=group.notes,
            )
            group_map[group.id] = copied_group.id
            session.add(copied_group)
        for teacher in session.exec(
            select(ProcessTeacher).where(
                ProcessTeacher.assignment_process_id == source.id
            )
        ).all():
            session.add(
                ProcessTeacher(
                    assignment_process_id=target.id,
                    teacher_profile_id=teacher.teacher_profile_id,
                    base_weekly_hours=teacher.base_weekly_hours,
                    extra_weekly_hours=0,
                    extra_hours_reason=None,
                    extra_hours_updated_by_user_id=None,
                    extra_hours_updated_at=None,
                    participates_in_selection=teacher.participates_in_selection,
                    selection_position=teacher.selection_position,
                    selection_points=teacher.selection_points,
                    selection_criteria_label=teacher.selection_criteria_label,
                    selection_notes=teacher.selection_notes,
                    order_locked=teacher.order_locked,
                    status=teacher.status,
                )
            )
        for cell in session.exec(
            select(GroupSubject).where(GroupSubject.assignment_process_id == source.id)
        ).all():
            copied_cell = GroupSubject(
                assignment_process_id=target.id,
                teaching_group_id=group_map[cell.teaching_group_id],
                subject_id=subject_map[cell.subject_id],
                group_weekly_hours=cell.group_weekly_hours,
                teacher_weekly_hours_per_position=(
                    cell.teacher_weekly_hours_per_position
                ),
                required_teacher_count=cell.required_teacher_count,
                active=cell.active,
                notes=cell.notes,
            )
            cell_map[cell.id] = copied_cell.id
            session.add(copied_cell)
        session.flush()
        return subject_map, cell_map

    @staticmethod
    def _copy_activity_templates(
        session: Session,
        source: AssignmentProcess,
        target: AssignmentProcess,
        subject_map: dict[uuid.UUID, uuid.UUID],
        cell_map: dict[uuid.UUID, uuid.UUID],
    ) -> None:
        """Copy live secondary-activity templates into a fresh draft plan.

        Only runs when ``copy_activities`` was explicitly requested (plan
        §10.1, "optional activity templates when explicitly selected").
        Requires a source teaching plan; if the source has none there is
        nothing to copy. A fresh ``DRAFT`` :class:`TeachingPlan` is created on
        the target (generation ``0``, no allocation, no generated requirements)
        and every live ``SECONDARY_MANUAL`` activity is re-created under it with
        its subject and group-subject links remapped through the source→target
        id maps from :meth:`_copy_structure`.

        ``MAIN_GENERATED`` activities are never copied — they are re-materialised
        from the copied group-subject cells by the materialisation flow — and
        retired activities are skipped.
        """
        source_plan = session.exec(
            select(TeachingPlan).where(TeachingPlan.assignment_process_id == source.id)
        ).first()
        if source_plan is None:
            return
        target_plan = TeachingPlan(
            assignment_process_id=target.id,
            status=TeachingPlanStatus.DRAFT,
            current_generation_number=0,
        )
        session.add(target_plan)
        for activity in session.exec(
            select(TeachingActivity).where(
                TeachingActivity.teaching_plan_id == source_plan.id
            )
        ).all():
            if (
                activity.source != TeachingActivitySource.SECONDARY_MANUAL
                or activity.retired_at is not None
            ):
                continue
            copied_activity = TeachingActivity(
                teaching_plan_id=target_plan.id,
                subject_id=subject_map[activity.subject_id],
                allocation_category=activity.allocation_category,
                activity_type=activity.activity_type,
                group_weekly_hours_per_group=activity.group_weekly_hours_per_group,
                teacher_weekly_hours_per_position=(
                    activity.teacher_weekly_hours_per_position
                ),
                required_teacher_count=activity.required_teacher_count,
                source=TeachingActivitySource.SECONDARY_MANUAL,
                notes=activity.notes,
            )
            session.add(copied_activity)
            for link in session.exec(
                select(TeachingActivityGroup).where(
                    TeachingActivityGroup.teaching_activity_id == activity.id
                )
            ).all():
                session.add(
                    TeachingActivityGroup(
                        teaching_activity_id=copied_activity.id,
                        group_subject_id=cell_map[link.group_subject_id],
                    )
                )
        session.flush()


__all__ = ["AssignmentProcessController"]
