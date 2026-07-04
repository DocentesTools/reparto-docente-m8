"""AssignmentProcess controller.

Owns CRUD plus the lifecycle operations introduced for the Phase 1 state
machine (plan §8.4, §10.2, §14.1):

* ``transition_process`` — moves the process through the documented
  status edges, tracking ``closed_at`` / ``closed_by_user_id`` for the
  ``final`` edge.
* ``reopen_process`` — explicit ``final`` → ``reopened`` edge with a
  mandatory reason; clears the close metadata.
* ``copy_from_process`` — copies the structure (and optionally the
  assignments) of a previous-year process into a fresh ``draft``
  process (plan §14.1).

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
from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.departments import Department
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.schools import School
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teaching_groups import TeachingGroup
from reparto_service.enums import (
    AssignmentProcessStatus,
    AssignmentSource,
    AssignmentStatus,
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
        """Copy structure (and optionally assignments) from a source process.

        The target process must:

        * exist,
        * belong to the same school as the source,
        * be in ``draft``,
        * currently be empty (no teachers, subjects, groups, requirements,
          or assignments).

        The selection-order fields of the target are kept as configured
        by the head (no source values are copied). When
        ``copy_assignments`` is true, each copied assignment is
        re-marked as ``draft`` with the source-original author cleared.
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
        AssignmentProcessController._copy_structure(session, source, target)
        if request.copy_assignments:
            AssignmentProcessController._copy_assignments(session, source, target)
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
                .select_from(HourRequirement)
                .where(HourRequirement.assignment_process_id == target_id)
            ).one()
            > 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Target process already has hour requirements.",
            )
        if (
            session.exec(
                select(func.count())
                .select_from(Assignment)
                .where(Assignment.assignment_process_id == target_id)
            ).one()
            > 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Target process already has assignments.",
            )

    @staticmethod
    def _copy_structure(
        session: Session, source: AssignmentProcess, target: AssignmentProcess
    ) -> None:
        """Copy subjects, teaching groups and hour requirements.

        ``ProcessTeacher`` rows are copied one-to-one: the same
        ``teacher_profile_id`` is reused and the selection-order fields
        are preserved (plan §14.1, the previous-year column is the
        teacher, not the per-process row). Available hours are reset to
        ``0`` so the head must re-enter the contract values for the new
        academic year.
        """
        for subject in session.exec(
            select(Subject).where(Subject.assignment_process_id == source.id)
        ).all():
            session.add(
                Subject(
                    assignment_process_id=target.id,
                    name=subject.name,
                    stage=subject.stage,
                    notes=subject.notes,
                )
            )
        for group in session.exec(
            select(TeachingGroup).where(
                TeachingGroup.assignment_process_id == source.id
            )
        ).all():
            session.add(
                TeachingGroup(
                    assignment_process_id=target.id,
                    stage=group.stage,
                    grade=group.grade,
                    group_code=group.group_code,
                    label=group.label,
                    notes=group.notes,
                )
            )
        for teacher in session.exec(
            select(ProcessTeacher).where(
                ProcessTeacher.assignment_process_id == source.id
            )
        ).all():
            session.add(
                ProcessTeacher(
                    assignment_process_id=target.id,
                    teacher_profile_id=teacher.teacher_profile_id,
                    available_hours=0,
                    participates_in_selection=teacher.participates_in_selection,
                    selection_position=teacher.selection_position,
                    selection_points=teacher.selection_points,
                    selection_criteria_label=teacher.selection_criteria_label,
                    selection_notes=teacher.selection_notes,
                    order_locked=teacher.order_locked,
                    status=teacher.status,
                )
            )
        for requirement in session.exec(
            select(HourRequirement).where(
                HourRequirement.assignment_process_id == source.id
            )
        ).all():
            session.add(
                HourRequirement(
                    assignment_process_id=target.id,
                    teaching_group_id=requirement.teaching_group_id,
                    subject_id=requirement.subject_id,
                    required_hours=requirement.required_hours,
                    requirement_type=requirement.requirement_type,
                    flags=requirement.flags,
                    notes=requirement.notes,
                )
            )
        session.flush()

    @staticmethod
    def _copy_assignments(
        session: Session, source: AssignmentProcess, target: AssignmentProcess
    ) -> None:
        """Copy assignments from the source to the target process.

        The mapping between old and new requirement / process-teacher
        rows is built by re-resolving the source rows' identifiers
        against the freshly-inserted target rows (group+subject for
        requirements, teacher-profile-id for process-teachers). The
        fresh rows are those just inserted in the same transaction; this
        works because ``_copy_structure`` runs first and ``session.flush``
        is called to obtain their IDs.
        """
        target_requirements: dict[tuple[uuid.UUID, uuid.UUID, str], uuid.UUID] = {}
        for requirement in session.exec(
            select(HourRequirement).where(
                HourRequirement.assignment_process_id == target.id
            )
        ).all():
            key = (
                requirement.teaching_group_id,
                requirement.subject_id,
                requirement.requirement_type.value,
            )
            target_requirements[key] = requirement.id
        target_process_teachers: dict[uuid.UUID, uuid.UUID] = {}
        for teacher in session.exec(
            select(ProcessTeacher).where(
                ProcessTeacher.assignment_process_id == target.id
            )
        ).all():
            target_process_teachers[teacher.teacher_profile_id] = teacher.id
        for source_assignment in session.exec(
            select(Assignment).where(Assignment.assignment_process_id == source.id)
        ).all():
            source_requirement = session.get(
                HourRequirement, source_assignment.hour_requirement_id
            )
            if source_requirement is None:
                continue
            new_requirement_id = target_requirements.get(
                (
                    source_requirement.teaching_group_id,
                    source_requirement.subject_id,
                    source_requirement.requirement_type.value,
                )
            )
            source_teacher = session.get(
                ProcessTeacher, source_assignment.process_teacher_id
            )
            if source_teacher is None:  # pragma: no cover
                continue
            new_process_teacher_id = target_process_teachers.get(
                source_teacher.teacher_profile_id
            )
            if (
                new_requirement_id is None or new_process_teacher_id is None
            ):  # pragma: no cover
                # The source row references a structure element that was
                # not copied over (e.g. a teacher profile that was not
                # in the target). Skip rather than invent data.
                continue
            session.add(
                Assignment(
                    assignment_process_id=target.id,
                    hour_requirement_id=new_requirement_id,
                    process_teacher_id=new_process_teacher_id,
                    assigned_hours=source_assignment.assigned_hours,
                    assignment_type=source_assignment.assignment_type,
                    source=AssignmentSource.SYSTEM_COPY,
                    status=AssignmentStatus.DRAFT,
                    chosen_by_user_id=None,
                    confirmed_by_user_id=None,
                    override_reason=None,
                    overridden_by_user_id=None,
                    notes=source_assignment.notes,
                )
            )


__all__ = ["AssignmentProcessController"]
