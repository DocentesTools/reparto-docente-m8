"""Assignment controller.

Redesigned for the three-stage adaptation (plan §5.10, §20.9). An assignment
binds one process teacher to one **complete, indivisible** requirement slot.
Both entry points — the department-head manual assignment and the teacher LAN
direct choice — go through the single shared complete-slot routine
:meth:`AssignmentController._occupy_slot`, so there is no duplicated business
logic (plan §7.7).

Invariants enforced here (with the database as the final barrier, plan §20.9):

* one ACTIVE assignment per requirement slot — a slot cannot be shared or split
  (plan §3.6, §5.10);
* a teacher can never occupy two positions of the same activity (plan §3.7);
* the requirement's activity is denormalised onto the assignment from the
  requirement itself, never trusted from the client;
* mutations are blocked while the parent process is immutable (final/archived).

The deeper LAN concurrency work (row-lock recheck of remaining target hours,
witness repair) and the exact participant-target rules are their own later plan
tasks; this task establishes the model and the complete-slot occupancy rules.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.assignments import (
    Assignment,
    AssignmentCreate,
    AssignmentDirectChoice,
    AssignmentPublic,
    AssignmentsPublic,
    AssignmentUpdate,
)
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.meeting_sessions import MeetingSession
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.selection_turns import SelectionTurn
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.enums import (
    AssignmentSource,
    AssignmentStatus,
    HourRequirementStatus,
    MeetingSessionStatus,
    SelectionOrderMode,
    SelectionTurnStatus,
)


class AssignmentController(DomainController):
    """Complete-slot assignment logic inside one assignment process."""

    # ── Read ──────────────────────────────────────────────────────────────────

    @staticmethod
    def list_assignments(session: Session, process_id: uuid.UUID) -> AssignmentsPublic:
        DomainController.get_process_or_404(session, process_id)
        statement = select(Assignment).where(
            Assignment.assignment_process_id == process_id
        )
        items = list(session.exec(statement).all())
        return AssignmentsPublic(
            data=[AssignmentPublic.model_validate(item) for item in items],
            count=len(items),
        )

    @staticmethod
    def get_assignment(
        session: Session, process_id: uuid.UUID, assignment_id: uuid.UUID
    ) -> AssignmentPublic:
        assignment = AssignmentController._get_or_404(
            session, process_id, assignment_id
        )
        return AssignmentPublic.model_validate(assignment)

    # ── Mutations ─────────────────────────────────────────────────────────────

    @staticmethod
    def create_assignment(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
        assignment_in: AssignmentCreate,
    ) -> AssignmentPublic:
        AssignmentController._ensure_open(session, process_id)
        requirement = AssignmentController._get_requirement_or_404(
            session, process_id, assignment_in.hour_requirement_id
        )
        AssignmentController._get_process_teacher_or_404(
            session, process_id, assignment_in.process_teacher_id
        )
        assignment = AssignmentController._occupy_slot(
            session,
            process_id=process_id,
            requirement=requirement,
            process_teacher_id=assignment_in.process_teacher_id,
            source=AssignmentSource.DEPARTMENT_HEAD,
            chosen_by_user_id=uuid.UUID(str(current_user.id)),
            confirmed_by_user_id=None,
            notes=assignment_in.notes,
        )
        AssignmentController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="assignment.created",
            entity_type="assignment",
            entity_id=assignment.id,
            before=None,
            after=assignment,
        )
        session.commit()
        session.refresh(assignment)
        return AssignmentPublic.model_validate(assignment)

    @staticmethod
    def create_direct_choice(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
        choice: AssignmentDirectChoice,
    ) -> AssignmentPublic:
        AssignmentController._ensure_open(session, process_id)
        meeting = AssignmentController._get_direct_selection_session(
            session, process_id, choice.meeting_session_id
        )
        process_teacher = AssignmentController._get_linked_process_teacher(
            session, process_id, current_user
        )
        AssignmentController._enforce_direct_turn(session, meeting, process_teacher.id)
        requirement = AssignmentController._get_requirement_or_404(
            session, process_id, choice.hour_requirement_id
        )
        user_id = uuid.UUID(str(current_user.id))
        assignment = AssignmentController._occupy_slot(
            session,
            process_id=process_id,
            requirement=requirement,
            process_teacher_id=process_teacher.id,
            source=AssignmentSource.TEACHER_DIRECT,
            chosen_by_user_id=user_id,
            confirmed_by_user_id=user_id,
            notes=choice.notes,
        )
        AssignmentController._complete_active_turn_if_needed(
            session, meeting, process_teacher.id
        )
        AssignmentController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="assignment.direct_choice",
            entity_type="assignment",
            entity_id=assignment.id,
            before=None,
            after=assignment,
        )
        session.commit()
        session.refresh(assignment)
        return AssignmentPublic.model_validate(assignment)

    @staticmethod
    def update_assignment(
        session: Session,
        process_id: uuid.UUID,
        assignment_id: uuid.UUID,
        assignment_in: AssignmentUpdate,
        current_user: UserModel,
    ) -> AssignmentPublic:
        AssignmentController._ensure_open(session, process_id)
        assignment = AssignmentController._get_or_404(
            session, process_id, assignment_id
        )
        before = Assignment.model_validate(assignment.model_dump())
        assignment.sqlmodel_update(assignment_in.model_dump(exclude_unset=True))
        session.add(assignment)
        AssignmentController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="assignment.updated",
            entity_type="assignment",
            entity_id=assignment.id,
            before=before,
            after=assignment,
        )
        session.commit()
        session.refresh(assignment)
        return AssignmentPublic.model_validate(assignment)

    @staticmethod
    def delete_assignment(
        session: Session,
        process_id: uuid.UUID,
        assignment_id: uuid.UUID,
        current_user: UserModel,
    ) -> AssignmentPublic:
        """Cancel a live assignment, freeing its requirement slot (plan §20.12).

        A soft cancel (rather than a hard delete) keeps the row traceable for
        audit and versioning; the active partial-unique indexes ignore
        CANCELLED rows, so the slot is immediately re-assignable.
        """
        AssignmentController._ensure_open(session, process_id)
        assignment = AssignmentController._get_or_404(
            session, process_id, assignment_id
        )
        before = Assignment.model_validate(assignment.model_dump())
        if assignment.status == AssignmentStatus.ACTIVE:
            assignment.status = AssignmentStatus.CANCELLED
            requirement = session.get(HourRequirement, assignment.hour_requirement_id)
            if requirement is not None:
                requirement.status = HourRequirementStatus.AVAILABLE
                session.add(requirement)
            session.add(assignment)
        AssignmentController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="assignment.cancelled",
            entity_type="assignment",
            entity_id=assignment.id,
            before=before,
            after=assignment,
        )
        session.commit()
        session.refresh(assignment)
        return AssignmentPublic.model_validate(assignment)

    # ── Shared complete-slot routine ──────────────────────────────────────────

    @staticmethod
    def _occupy_slot(
        session: Session,
        *,
        process_id: uuid.UUID,
        requirement: HourRequirement,
        process_teacher_id: uuid.UUID,
        source: AssignmentSource,
        chosen_by_user_id: uuid.UUID,
        confirmed_by_user_id: uuid.UUID | None,
        notes: str | None,
    ) -> Assignment:
        """Occupy one complete slot, enforcing the indivisible-slot invariants.

        Shared by manual and direct assignment so both paths run identical rules
        (plan §7.7). The requirement's activity is denormalised onto the row
        (plan §20.9); the DB partial-unique indexes are the final barrier.
        """
        if requirement.status != HourRequirementStatus.AVAILABLE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Requirement {requirement.id} is not available for "
                    f"assignment (status {requirement.status.value})."
                ),
            )
        AssignmentController._ensure_slot_unassigned(session, requirement.id)
        AssignmentController._ensure_distinct_teacher(
            session, requirement.teaching_activity_id, process_teacher_id
        )
        assignment = Assignment(
            assignment_process_id=process_id,
            hour_requirement_id=requirement.id,
            teaching_activity_id=requirement.teaching_activity_id,
            process_teacher_id=process_teacher_id,
            source=source,
            status=AssignmentStatus.ACTIVE,
            chosen_by_user_id=chosen_by_user_id,
            confirmed_by_user_id=confirmed_by_user_id,
            notes=notes,
        )
        session.add(assignment)
        requirement.status = HourRequirementStatus.ASSIGNED
        session.add(requirement)
        return assignment

    @staticmethod
    def _ensure_slot_unassigned(session: Session, requirement_id: uuid.UUID) -> None:
        """Reject a second live assignment on the same slot (plan §5.10)."""
        statement = select(Assignment).where(
            Assignment.hour_requirement_id == requirement_id,
            Assignment.status == AssignmentStatus.ACTIVE,
        )
        if session.exec(statement).first() is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Requirement {requirement_id} is already assigned; a slot "
                    "cannot be shared or split."
                ),
            )

    @staticmethod
    def _ensure_distinct_teacher(
        session: Session,
        teaching_activity_id: uuid.UUID,
        process_teacher_id: uuid.UUID,
    ) -> None:
        """Reject the same teacher in two positions of one activity (plan §3.7)."""
        statement = select(Assignment).where(
            Assignment.teaching_activity_id == teaching_activity_id,
            Assignment.process_teacher_id == process_teacher_id,
            Assignment.status == AssignmentStatus.ACTIVE,
        )
        if session.exec(statement).first() is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Teacher already occupies a position of activity "
                    f"{teaching_activity_id}; distinct teachers are required."
                ),
            )

    # ── Internal lookups ──────────────────────────────────────────────────────

    @staticmethod
    def _get_or_404(
        session: Session, process_id: uuid.UUID, assignment_id: uuid.UUID
    ) -> Assignment:
        DomainController.get_process_or_404(session, process_id)
        statement = select(Assignment).where(Assignment.id == assignment_id)
        assignment = session.exec(statement).first()
        if assignment is None or assignment.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Assignment {assignment_id} not found in process {process_id}."
                ),
            )
        return assignment

    @staticmethod
    def _get_requirement_or_404(
        session: Session, process_id: uuid.UUID, requirement_id: uuid.UUID
    ) -> HourRequirement:
        statement = (
            select(HourRequirement)
            .where(HourRequirement.id == requirement_id)
            .with_for_update()
        )
        requirement = session.exec(statement).first()
        if requirement is None or requirement.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"HourRequirement {requirement_id} not found in process "
                    f"{process_id}."
                ),
            )
        return requirement

    @staticmethod
    def _get_process_teacher_or_404(
        session: Session, process_id: uuid.UUID, process_teacher_id: uuid.UUID
    ) -> ProcessTeacher:
        statement = select(ProcessTeacher).where(
            ProcessTeacher.id == process_teacher_id
        )
        process_teacher = session.exec(statement).first()
        if (
            process_teacher is None
            or process_teacher.assignment_process_id != process_id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"ProcessTeacher {process_teacher_id} not found in "
                    f"process {process_id}."
                ),
            )
        return process_teacher

    @staticmethod
    def _ensure_open(session: Session, process_id: uuid.UUID) -> AssignmentProcess:
        process = DomainController.get_process_or_404(session, process_id)
        DomainController.ensure_process_mutable(process)
        return process

    # ── Direct-selection helpers ──────────────────────────────────────────────

    @staticmethod
    def _get_direct_selection_session(
        session: Session, process_id: uuid.UUID, meeting_session_id: uuid.UUID
    ) -> MeetingSession:
        meeting = session.get(MeetingSession, meeting_session_id)
        if meeting is None or meeting.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"MeetingSession {meeting_session_id} not found.",
            )
        if meeting.status not in {
            MeetingSessionStatus.OPEN,
            MeetingSessionStatus.SELECTING,
            MeetingSessionStatus.REOPENED,
        }:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Meeting session must be open for direct selection.",
            )
        if (
            not meeting.lan_access_enabled
            or not meeting.direct_teacher_selection_enabled
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Direct teacher selection is disabled for this session.",
            )
        return meeting

    @staticmethod
    def _get_linked_process_teacher(
        session: Session, process_id: uuid.UUID, current_user: UserModel
    ) -> ProcessTeacher:
        statement = (
            select(ProcessTeacher, TeacherProfile)
            .where(ProcessTeacher.assignment_process_id == process_id)
            .where(ProcessTeacher.teacher_profile_id == TeacherProfile.id)
            .where(TeacherProfile.user_id == uuid.UUID(str(current_user.id)))
        )
        row = session.exec(statement).first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No teacher profile is linked to this auth user.",
            )
        process_teacher, _ = row
        return process_teacher

    @staticmethod
    def _active_turn(
        session: Session, meeting_session_id: uuid.UUID
    ) -> SelectionTurn | None:
        statement = select(SelectionTurn).where(
            SelectionTurn.meeting_session_id == meeting_session_id,
            SelectionTurn.status == SelectionTurnStatus.ACTIVE,
        )
        return session.exec(statement).first()

    @staticmethod
    def _enforce_direct_turn(
        session: Session, meeting: MeetingSession, process_teacher_id: uuid.UUID
    ) -> None:
        active = AssignmentController._active_turn(session, meeting.id)
        if meeting.selection_mode != SelectionOrderMode.STRICT:
            return
        if active is None or active.process_teacher_id != process_teacher_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Teacher cannot choose outside the active strict turn.",
            )

    @staticmethod
    def _complete_active_turn_if_needed(
        session: Session, meeting: MeetingSession, process_teacher_id: uuid.UUID
    ) -> None:
        active = AssignmentController._active_turn(session, meeting.id)
        if active is None or active.process_teacher_id != process_teacher_id:
            return
        from datetime import datetime, timezone

        active.status = SelectionTurnStatus.COMPLETED
        active.completed_at = datetime.now(tz=timezone.utc)
        session.add(active)


__all__ = ["AssignmentController"]
