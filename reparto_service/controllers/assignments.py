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

Concurrency (plan §20.5): direct teacher selection is a teacher-triggerable hot
path, so every occupancy runs only *cheap* in-transaction guards under
pessimistic row locks — never the NP-hard feasibility solver. Both entry points
funnel through :meth:`AssignmentController._lock_selection_state`, which locks
the requirement slot, the participant row and the activity's sibling occupancy
in one canonical order (slot → participant → siblings, identical on the manual
and direct paths so the two can never deadlock against each other). The
slot-availability, distinct-teacher and exact-target rechecks then run against
that serialized, freshly re-read view (plan §20.5 guards 1–2/4). The persisted
witness is loaded and boundedly repaired on this path. The full solver stays off
it and is available only through the administrator evaluation operation
(plan §20.24).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from fastapi_m8 import UserModel
from sqlmodel import Session, col, select

from reparto_service.controllers.base import DomainController
from reparto_service.core.decimals import quantize_hours
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.assignments import (
    Assignment,
    AssignmentCreate,
    AssignmentDirectChoice,
    AssignmentPublic,
    AssignmentReassign,
    AssignmentUndo,
    AssignmentsPublic,
    AssignmentUpdate,
)
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.meeting_sessions import MeetingSession
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.selection_turns import SelectionTurn
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    AssignmentSource,
    AssignmentStatus,
    AuditEventType,
    FeasibilityStatus,
    HourRequirementStatus,
    MeetingSessionStatus,
    ProcessTeacherStatus,
    SelectionOrderMode,
    SelectionTurnStatus,
)
from reparto_service.schemas.planning import AssignmentValidationReport
from reparto_service.services.calculations import AssignmentCalculationService
from reparto_service.services.lifecycle_gates import PlanReadinessGate
from reparto_service.services.selection_guards import (
    FastGuardFinding,
    WitnessRepairCode,
    build_remaining_assignment_state,
    compute_fast_feasibility_checks,
)
from reparto_service.services.feasibility import FeasibilityWitnessEntry
from reparto_service.services.feasibility_witnesses import FeasibilityWitnessService
from reparto_service.services.validations import AssignmentValidationService

_ZERO = Decimal("0.00")


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

    @staticmethod
    def get_validations(
        session: Session, process_id: uuid.UUID
    ) -> AssignmentValidationReport:
        """Return the process's assignment-stage findings (plan §6.3, §6.4).

        Read-only and solver-free (plan §20.23): it reports unassigned slots and
        participants off their exact target but never triggers an evaluation.
        """
        process = DomainController.get_process_or_404(session, process_id)
        return AssignmentValidationService.compute_assignment_validations(
            session, process
        )

    # ── Mutations ─────────────────────────────────────────────────────────────

    @staticmethod
    def create_assignment(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
        assignment_in: AssignmentCreate,
    ) -> AssignmentPublic:
        assignment = AssignmentController.create_manual_assignment(
            session,
            process_id=process_id,
            current_user=current_user,
            hour_requirement_id=assignment_in.hour_requirement_id,
            process_teacher_id=assignment_in.process_teacher_id,
            notes=assignment_in.notes,
        )
        session.commit()
        session.refresh(assignment)
        return AssignmentPublic.model_validate(assignment)

    @staticmethod
    def create_manual_assignment(
        session: Session,
        *,
        process_id: uuid.UUID,
        current_user: UserModel,
        hour_requirement_id: uuid.UUID,
        process_teacher_id: uuid.UUID,
        notes: str | None,
    ) -> Assignment:
        """Department-head manual complete-slot occupancy (plan §7.7).

        The single manual-assignment primitive. Both the standalone
        ``POST /assignments`` route and the meeting turn-completion flow
        (:class:`~reparto_service.controllers.selection_turns.SelectionTurnController`)
        route through here, so the department-head manual path keeps **no
        separate business logic** — it uses the same shared
        :meth:`_occupy_slot` complete-slot service as the teacher LAN direct
        choice, with the same availability/distinct-teacher/exact-target guards
        under the same pessimistic locks.

        The caller owns the transaction boundary: this records the
        ``assignment.created`` audit event and returns the (uncommitted)
        assignment so a wider operation — e.g. completing a selection turn —
        can commit it together with its own changes.
        """
        AssignmentController._ensure_open(session, process_id)
        # A new assignment is blocked while an allocation change leaves the plan
        # awaiting reconciliation (plan §3.11.9, §9.7).
        PlanReadinessGate.ensure_assignments_unblocked(
            session, process_id, operation="create an assignment"
        )
        requirement = AssignmentController._get_requirement_or_404(
            session, process_id, hour_requirement_id
        )
        process_teacher = AssignmentController._get_process_teacher_or_404(
            session, process_id, process_teacher_id
        )
        assignment = AssignmentController._occupy_slot(
            session,
            process_id=process_id,
            requirement=requirement,
            process_teacher=process_teacher,
            source=AssignmentSource.DEPARTMENT_HEAD,
            chosen_by_user_id=uuid.UUID(str(current_user.id)),
            confirmed_by_user_id=None,
            notes=notes,
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
        return assignment

    @staticmethod
    def create_direct_choice(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
        choice: AssignmentDirectChoice,
    ) -> AssignmentPublic:
        AssignmentController._ensure_open(session, process_id)
        # Direct selection is a meeting-time assignment operation: block it while
        # an allocation change leaves the plan awaiting reconciliation (plan
        # §3.11.9, §9.7). The strict stage-entry contract (plan §3.10) was already
        # enforced when the meeting was opened.
        PlanReadinessGate.ensure_assignments_unblocked(
            session, process_id, operation="select a teacher directly"
        )
        meeting = AssignmentController._get_direct_selection_session(
            session, process_id, choice.meeting_session_id
        )
        process_teacher = AssignmentController.linked_process_teacher(
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
            process_teacher=process_teacher,
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
    def undo_assignment(
        session: Session,
        process_id: uuid.UUID,
        assignment_id: uuid.UUID,
        current_user: UserModel,
        action: AssignmentUndo,
    ) -> AssignmentPublic:
        """Undo a live assignment and restore its turn queue (plan §20.13)."""

        AssignmentController._ensure_open(session, process_id)
        assignment = AssignmentController._get_or_404(
            session, process_id, assignment_id
        )
        AssignmentController._lock_assignment(session, assignment)
        AssignmentController._ensure_active_for_action(assignment)
        before = Assignment.model_validate(assignment.model_dump())
        AssignmentController._release_assignment(session, assignment)
        invalidated = FeasibilityWitnessService.invalidate(session, process_id)
        AssignmentController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type=AuditEventType.ASSIGNMENT_UNDONE,
            entity_type="assignment",
            entity_id=assignment.id,
            before=before,
            after=assignment,
            reason=action.reason,
        )
        AssignmentController._requeue_completed_turns(
            session,
            process_id=process_id,
            process_teacher_id=assignment.process_teacher_id,
            current_user=current_user,
            reason=action.reason,
        )
        session.commit()
        session.refresh(assignment)
        if invalidated:
            AssignmentController.publish_feasibility_invalidated(session, process_id)
        return AssignmentPublic.model_validate(assignment)

    @staticmethod
    def reassign_assignment(
        session: Session,
        process_id: uuid.UUID,
        assignment_id: uuid.UUID,
        current_user: UserModel,
        action: AssignmentReassign,
    ) -> AssignmentPublic:
        """Atomically move one live slot through the normal guarded insert path."""

        AssignmentController._ensure_open(session, process_id)
        PlanReadinessGate.ensure_assignments_unblocked(
            session, process_id, operation="reassign an assignment"
        )
        assignment = AssignmentController._get_or_404(
            session, process_id, assignment_id
        )
        AssignmentController._ensure_active_for_action(assignment)
        requirement = AssignmentController._get_requirement_or_404(
            session, process_id, assignment.hour_requirement_id
        )
        replacement = AssignmentController._get_process_teacher_or_404(
            session, process_id, action.process_teacher_id
        )
        if replacement.id == assignment.process_teacher_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reassignment requires a different process teacher.",
            )
        AssignmentController._lock_reassignment_state(
            session, assignment, requirement, replacement
        )
        AssignmentController._ensure_active_for_action(assignment)
        AssignmentController._ensure_eligible_process_teacher(replacement)
        AssignmentController._ensure_distinct_teacher(
            session, requirement.teaching_activity_id, replacement.id
        )
        AssignmentController._ensure_fits_target(session, replacement, requirement)
        repaired = FeasibilityWitnessService.repair_for_reassignment(
            session,
            process_id=process_id,
            assignment=assignment,
            requirement=requirement,
            proposed_participant_id=replacement.id,
        )
        if repaired is not None and (
            repaired.code != WitnessRepairCode.REPAIRED or repaired.witness is None
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Reassignment would strand the remaining assignment state "
                    f"({repaired.code.value}); administrative feasibility "
                    "evaluation is required."
                ),
            )
        before = Assignment.model_validate(assignment.model_dump())
        AssignmentController._release_assignment(session, assignment)
        replacement_assignment = AssignmentController._occupy_slot(
            session,
            process_id=process_id,
            requirement=requirement,
            process_teacher=replacement,
            source=AssignmentSource.DEPARTMENT_HEAD,
            chosen_by_user_id=uuid.UUID(str(current_user.id)),
            confirmed_by_user_id=None,
            notes=action.notes,
            prevalidated_repair=None if repaired is None else repaired.witness,
            feasibility_prevalidated=True,
        )
        AssignmentController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type=AuditEventType.ASSIGNMENT_REASSIGNED,
            entity_type="assignment",
            entity_id=replacement_assignment.id,
            before=before,
            after=replacement_assignment,
            reason=action.reason,
        )
        AssignmentController._requeue_completed_turns(
            session,
            process_id=process_id,
            process_teacher_id=assignment.process_teacher_id,
            current_user=current_user,
            reason=action.reason,
        )
        session.commit()
        session.refresh(replacement_assignment)
        return AssignmentPublic.model_validate(replacement_assignment)

    # ── Shared complete-slot routine ──────────────────────────────────────────

    @staticmethod
    def _occupy_slot(
        session: Session,
        *,
        process_id: uuid.UUID,
        requirement: HourRequirement,
        process_teacher: ProcessTeacher,
        source: AssignmentSource,
        chosen_by_user_id: uuid.UUID,
        confirmed_by_user_id: uuid.UUID | None,
        notes: str | None,
        prevalidated_repair: tuple[FeasibilityWitnessEntry, ...] | None = None,
        feasibility_prevalidated: bool = False,
    ) -> Assignment:
        """Occupy one complete slot, enforcing the indivisible-slot invariants.

        Shared by manual and direct assignment so both paths run identical rules
        (plan §7.7). The requirement's activity is denormalised onto the row
        (plan §20.9); the DB partial-unique indexes are the final barrier.

        Concurrency (plan §20.5): the slot, the participant and the activity's
        sibling occupancy are pessimistically locked *before* the guards run, so
        the slot-availability, distinct-teacher and exact-target checks below are
        rechecked against a serialized, up-to-date view. Two concurrent
        selections for the same slot, the same participant, or two positions of
        the same activity therefore serialize here and one gets a clean domain
        error instead of racing to the DB partial-unique barrier.
        """
        AssignmentController._lock_selection_state(
            session, requirement=requirement, process_teacher=process_teacher
        )
        if requirement.status != HourRequirementStatus.AVAILABLE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Requirement {requirement.id} is not available for "
                    f"assignment (status {requirement.status.value})."
                ),
            )
        AssignmentController._ensure_slot_unassigned(session, requirement.id)
        AssignmentController._ensure_eligible_process_teacher(process_teacher)
        AssignmentController._ensure_distinct_teacher(
            session, requirement.teaching_activity_id, process_teacher.id
        )
        AssignmentController._ensure_fits_target(session, process_teacher, requirement)
        repaired_witness = (
            prevalidated_repair
            if feasibility_prevalidated
            else AssignmentController._ensure_fast_feasibility(
                session,
                process_id=process_id,
                requirement=requirement,
                process_teacher=process_teacher,
            )
        )
        assignment = Assignment(
            assignment_process_id=process_id,
            hour_requirement_id=requirement.id,
            teaching_activity_id=requirement.teaching_activity_id,
            process_teacher_id=process_teacher.id,
            source=source,
            status=AssignmentStatus.ACTIVE,
            chosen_by_user_id=chosen_by_user_id,
            confirmed_by_user_id=confirmed_by_user_id,
            notes=notes,
        )
        session.add(assignment)
        requirement.status = HourRequirementStatus.ASSIGNED
        session.add(requirement)
        if repaired_witness is not None:
            FeasibilityWitnessService.persist_repair(
                session,
                process_id=process_id,
                repaired_remaining=repaired_witness,
            )
        return assignment

    @staticmethod
    def _lock_assignment(session: Session, assignment: Assignment) -> None:
        """Lock and refresh one assignment before an undo-style action."""

        session.exec(
            select(Assignment).where(Assignment.id == assignment.id).with_for_update()
        ).all()
        session.refresh(assignment)

    @staticmethod
    def _lock_reassignment_state(
        session: Session,
        assignment: Assignment,
        requirement: HourRequirement,
        replacement: ProcessTeacher,
    ) -> None:
        """Lock every row used by an atomic reassignment in canonical order."""

        AssignmentController._lock_requirement_row(session, requirement)
        participant_ids = sorted(
            {assignment.process_teacher_id, replacement.id}, key=str
        )
        participants = session.exec(
            select(ProcessTeacher)
            .where(col(ProcessTeacher.id).in_(participant_ids))
            .order_by(col(ProcessTeacher.id))
            .with_for_update()
        ).all()
        for participant in participants:
            session.refresh(participant)
        AssignmentController._lock_activity_assignments(
            session, requirement.teaching_activity_id
        )
        session.refresh(assignment)
        session.refresh(replacement)

    @staticmethod
    def _ensure_active_for_action(assignment: Assignment) -> None:
        """Reject repeated undo/reassignment attempts against historical rows."""

        if assignment.status != AssignmentStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only an active assignment can be undone or reassigned.",
            )

    @staticmethod
    def _release_assignment(session: Session, assignment: Assignment) -> None:
        """Soft-cancel an assignment and release its live requirement slot."""

        assignment.status = AssignmentStatus.CANCELLED
        requirement = session.get(HourRequirement, assignment.hour_requirement_id)
        if requirement is not None:
            requirement.status = HourRequirementStatus.AVAILABLE
            session.add(requirement)
        session.add(assignment)

    @staticmethod
    def _ensure_eligible_process_teacher(process_teacher: ProcessTeacher) -> None:
        """Require an active participant for every new slot occupancy."""

        if process_teacher.status != ProcessTeacherStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only an active process teacher is eligible for assignment.",
            )

    @staticmethod
    def _requeue_completed_turns(
        session: Session,
        *,
        process_id: uuid.UUID,
        process_teacher_id: uuid.UUID,
        current_user: UserModel,
        reason: str,
    ) -> None:
        """Re-enter completed live-meeting turns and recompute current turns."""

        meetings = session.exec(
            select(MeetingSession)
            .where(MeetingSession.assignment_process_id == process_id)
            .where(
                col(MeetingSession.status).in_(
                    {
                        MeetingSessionStatus.OPEN,
                        MeetingSessionStatus.SELECTING,
                        MeetingSessionStatus.REOPENED,
                    }
                )
            )
            .order_by(col(MeetingSession.id))
        ).all()
        now = datetime.now(tz=timezone.utc)
        for meeting in meetings:
            turn = session.exec(
                select(SelectionTurn).where(
                    SelectionTurn.meeting_session_id == meeting.id,
                    SelectionTurn.process_teacher_id == process_teacher_id,
                    SelectionTurn.status == SelectionTurnStatus.COMPLETED,
                )
            ).first()
            if turn is None:
                continue
            before = AssignmentController._snapshot_turn(turn)
            AssignmentController._reset_turn_to_pending(turn)
            session.add(turn)
            session.flush()
            AssignmentController._recompute_current_turn(
                session, process_id, meeting.id, current_user, reason, now
            )
            AssignmentController.record_audit_event(
                session,
                process_id=process_id,
                current_user=current_user,
                event_type=AuditEventType.SELECTION_TURN_REENTERED,
                entity_type="selection_turn",
                entity_id=turn.id,
                before=before,
                after=turn,
                reason=reason,
            )

    @staticmethod
    def _recompute_current_turn(
        session: Session,
        process_id: uuid.UUID,
        meeting_session_id: uuid.UUID,
        current_user: UserModel,
        reason: str,
        now: datetime,
    ) -> None:
        """Make the earliest unfinished turn the sole deterministic current turn."""

        turns = list(
            session.exec(
                select(SelectionTurn)
                .where(SelectionTurn.meeting_session_id == meeting_session_id)
                .where(
                    col(SelectionTurn.status).in_(
                        {SelectionTurnStatus.PENDING, SelectionTurnStatus.ACTIVE}
                    )
                )
                .order_by(col(SelectionTurn.position), col(SelectionTurn.id))
            ).all()
        )
        current = turns[0]
        for turn in turns:
            expected = (
                SelectionTurnStatus.ACTIVE
                if turn.id == current.id
                else SelectionTurnStatus.PENDING
            )
            if turn.status == expected:
                continue
            before = AssignmentController._snapshot_turn(turn)
            turn.status = expected
            turn.started_at = now if expected == SelectionTurnStatus.ACTIVE else None
            session.add(turn)
            AssignmentController.record_audit_event(
                session,
                process_id=process_id,
                current_user=current_user,
                event_type=AuditEventType.SELECTION_TURN_RECOMPUTED,
                entity_type="selection_turn",
                entity_id=turn.id,
                before=before,
                after=turn,
                reason=reason,
            )

    @staticmethod
    def _reset_turn_to_pending(turn: SelectionTurn) -> None:
        """Clear terminal metadata before a completed turn re-enters the queue."""

        turn.status = SelectionTurnStatus.PENDING
        turn.started_at = None
        turn.completed_at = None
        turn.skipped_at = None
        turn.skip_reason = None
        turn.forced_by_user_id = None

    @staticmethod
    def _snapshot_turn(turn: SelectionTurn) -> SelectionTurn:
        """Detach a selection-turn snapshot for audit before mutation."""

        return SelectionTurn.model_validate(turn.model_dump())

    @staticmethod
    def _lock_selection_state(
        session: Session,
        *,
        requirement: HourRequirement,
        process_teacher: ProcessTeacher,
    ) -> None:
        """Pessimistically lock the state a selection depends on (plan §20.5).

        Acquired in one canonical order — the requirement slot, then the
        participant, then the activity's sibling occupancy — shared by both the
        manual and direct entry points so concurrent selections can never
        deadlock against each other. Locking the slot serializes two teachers
        racing for the *same* slot; locking the participant serializes one
        teacher's concurrent selections (so the remaining-target recheck sees
        every in-flight assignment); locking the activity's ACTIVE assignments
        serializes the distinct-teacher recheck across sibling positions. Each
        locked row is re-read so the guards run on current data.
        """
        AssignmentController._lock_requirement_row(session, requirement)
        AssignmentController._lock_participant_row(session, process_teacher)
        AssignmentController._lock_activity_assignments(
            session, requirement.teaching_activity_id
        )

    @staticmethod
    def _lock_requirement_row(session: Session, requirement: HourRequirement) -> None:
        """Lock the requirement slot row FOR UPDATE and re-read it."""
        session.exec(
            select(HourRequirement)
            .where(HourRequirement.id == requirement.id)
            .with_for_update()
        ).all()
        session.refresh(requirement)

    @staticmethod
    def _lock_participant_row(
        session: Session, process_teacher: ProcessTeacher
    ) -> None:
        """Lock the participant row FOR UPDATE and re-read it (fresh target)."""
        session.exec(
            select(ProcessTeacher)
            .where(ProcessTeacher.id == process_teacher.id)
            .with_for_update()
        ).all()
        session.refresh(process_teacher)

    @staticmethod
    def _lock_activity_assignments(
        session: Session, teaching_activity_id: uuid.UUID
    ) -> None:
        """Lock the activity's ACTIVE sibling occupancy FOR UPDATE (plan §3.7).

        Serializes the distinct-teacher recheck across the positions of one
        activity; the DB partial-unique index stays the final barrier (plan
        §20.9).
        """
        session.exec(
            select(Assignment)
            .where(Assignment.teaching_activity_id == teaching_activity_id)
            .where(Assignment.status == AssignmentStatus.ACTIVE)
            .with_for_update()
        ).all()

    @staticmethod
    def _ensure_fits_target(
        session: Session,
        process_teacher: ProcessTeacher,
        requirement: HourRequirement,
    ) -> None:
        """Reject an assignment that would push a teacher above target (plan §3.8).

        A slot is indivisible (plan §3.6), so it fits only when the participant's
        already-assigned hours plus the whole slot stay within
        ``target_weekly_hours``. There is no override: an overload must first be
        authorized by raising ``extra_weekly_hours`` (plan §3.8), which is why
        this guard has no bypass path.
        """
        assigned = AssignmentCalculationService.compute_participant_assigned_hours(
            session, process_teacher
        )
        slot_hours = requirement.required_teacher_hours
        target = process_teacher.target_weekly_hours
        if quantize_hours(assigned + slot_hours) > target:
            remaining = quantize_hours(target - assigned)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Requirement {requirement.id} needs {slot_hours} hours but the "
                    f"participant has only {remaining} remaining before the target "
                    f"of {target}; a slot cannot be split, so authorize extra hours "
                    "first."
                ),
            )

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

    @staticmethod
    def _ensure_fast_feasibility(
        session: Session,
        *,
        process_id: uuid.UUID,
        requirement: HourRequirement,
        process_teacher: ProcessTeacher,
    ) -> tuple[FeasibilityWitnessEntry, ...] | None:
        """Run the process-wide polynomial guards inside the transaction.

        During the staged §20.20 rollout, legacy NOT_EVALUATED plans continue
        through the existing exact-fit and distinct-teacher checks.  Once a plan
        is FEASIBLE, every proposal must preserve the cheap invariants here.
        The later lifecycle-gate bullet makes FEASIBLE mandatory for all entry
        points; witness persistence then adds the bounded repair result.
        """
        plan = session.exec(
            select(TeachingPlan).where(TeachingPlan.assignment_process_id == process_id)
        ).first()
        if plan is None or plan.feasibility_status != FeasibilityStatus.FEASIBLE:
            return None
        state = build_remaining_assignment_state(session, process_id)
        result = compute_fast_feasibility_checks(
            state,
            proposed_slot_id=str(requirement.id),
            proposed_participant_id=str(process_teacher.id),
        )
        if result.findings:
            AssignmentController._raise_fast_guard(result.findings[0])
        repair = FeasibilityWitnessService.repair_for_selection(
            session,
            process_id=process_id,
            proposed_slot_id=requirement.id,
            proposed_participant_id=process_teacher.id,
        )
        if repair.code != WitnessRepairCode.REPAIRED or repair.witness is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Selection is blocked because the deterministic witness "
                    f"could not be repaired ({repair.code.value}); administrative "
                    "feasibility evaluation is required. Go to the Planning page, "
                    "run the feasibility evaluation again, and return to the "
                    "board — nothing is broken and nothing is lost."
                ),
            )
        return repair.witness

    @staticmethod
    def _raise_fast_guard(finding: FastGuardFinding) -> None:
        """Raise a stable conflict without exposing a full provisional reparto."""
        related = f" ({', '.join(finding.related_ids)})" if finding.related_ids else ""
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Selection would strand the remaining assignment state: "
                f"{finding.code.value}{related}. Administrative feasibility "
                "evaluation is required."
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
        # Identity/validation read only; the row is locked in canonical order
        # by ``_lock_selection_state`` once occupancy actually begins (§20.5).
        statement = select(HourRequirement).where(HourRequirement.id == requirement_id)
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
