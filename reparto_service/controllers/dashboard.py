"""Dashboard controller.

A read-only composition over the three-stage services — it computes nothing of
its own (plan §6: no controller-side arithmetic) and mutates no state. Each
figure comes from the service that owns it:

* :class:`~reparto_service.services.calculations.PlanningCalculationService` —
  the two independent balances (plan §6.1);
* :class:`~reparto_service.services.calculations.AssignmentCalculationService` —
  the per-participant slot occupancy view (plan §6.2);
* :class:`~reparto_service.services.validations.PlanValidationService` /
  :class:`~reparto_service.services.validations.AssignmentValidationService` —
  the blocking/warning findings (plan §6.3, §6.4);
* :func:`~reparto_service.services.sse.current_readiness` — the coarse readiness,
  derived from the lifecycle-gate status sets so a rendered dashboard can never
  disagree with the gate deciding what the viewer may do.

Everything here is solver-free (plan §20.23): the stored feasibility status is
reported through the validation reports, never evaluated. This is the read path
that replaces the deleted ``SummaryService``, whose single global balance,
partial-coverage states and override flags no longer model anything real
(plan §3.6, §3.8, §5.10).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlmodel import Session, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.selection_turns import SelectionTurn
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import SelectionTurnStatus
from reparto_service.schemas.dashboard import (
    AssignmentSection,
    CurrentTurnSummary,
    PlanningSection,
    ProcessDashboard,
    ProcessSummary,
    TeacherLanSummary,
)
from reparto_service.services.calculations import (
    AssignmentCalculationService,
    PlanningCalculationService,
)
from reparto_service.services.sse import current_readiness
from reparto_service.services.validations import (
    AssignmentValidationService,
    PlanValidationService,
)


class DashboardController(DomainController):
    """Read-only controller for the department-head and LAN read endpoints."""

    # ── Department-head reads ────────────────────────────────────────────────

    @staticmethod
    def get_dashboard(session: Session, process_id: uuid.UUID) -> ProcessDashboard:
        """Both stages, their validations and the current turn in one round trip."""
        process = DomainController.get_process_or_404(session, process_id)
        planning = DashboardController._planning_section(session, process_id)
        assignment = DashboardController._assignment_section(session, process)
        readiness, _ = current_readiness(session, process_id)
        return ProcessDashboard(
            process_id=process_id,
            generated_at=datetime.now(tz=timezone.utc),
            readiness=readiness,
            planning=planning,
            assignment=assignment,
            current_turn=DashboardController._current_turn(session, process_id),
            blocking_validation_count=DashboardController._blocking_count(
                planning, assignment
            ),
        )

    @staticmethod
    def get_summary(session: Session, process_id: uuid.UUID) -> ProcessSummary:
        """The dashboard without the message lists or the per-participant rows."""
        process = DomainController.get_process_or_404(session, process_id)
        planning = DashboardController._planning_section(session, process_id)
        assignment = DashboardController._assignment_section(session, process)
        readiness, _ = current_readiness(session, process_id)
        return ProcessSummary(
            process_id=process_id,
            generated_at=datetime.now(tz=timezone.utc),
            readiness=readiness,
            plan_status=planning.status,
            plan_balance=planning.balance,
            total_slots=assignment.summary.total_slots,
            assigned_slots=assignment.summary.assigned_slots,
            available_slots=assignment.summary.available_slots,
            current_turn=DashboardController._current_turn(session, process_id),
            blocking_validation_count=DashboardController._blocking_count(
                planning, assignment
            ),
        )

    # ── Teacher LAN read ─────────────────────────────────────────────────────

    @staticmethod
    def get_teacher_lan_summary(
        session: Session, process_id: uuid.UUID, current_user: UserModel
    ) -> TeacherLanSummary:
        """The caller's own participation only (plan §8.6, §20.25).

        The per-participant rows are computed once for the process and the
        caller's row is selected out of them, so a teacher's figures are the
        same numbers the head sees — never a separately derived variant that
        could drift. No other participant's row leaves this method.
        """
        process = DomainController.get_process_or_404(session, process_id)
        process_teacher, profile = DashboardController._linked_participant_or_404(
            session, process_id, current_user
        )
        summary = AssignmentCalculationService.compute_assignment_summary(
            session, process
        )
        # The summary rows and the lookup above resolve the same participant
        # join, so the caller's row is always present.
        participant = {row.process_teacher_id: row for row in summary.participants}[
            process_teacher.id
        ]
        plan = DashboardController._plan_row(session, process_id)
        readiness, selection_blocked = current_readiness(session, process_id)
        return TeacherLanSummary(
            process_id=process_id,
            teacher_profile_id=profile.id,
            process_teacher_id=process_teacher.id,
            generated_at=datetime.now(tz=timezone.utc),
            readiness=readiness,
            selection_blocked=selection_blocked,
            plan_balance=(
                None
                if plan is None
                else PlanningCalculationService.compute_plan_balance(session, plan)
            ),
            participant=participant,
            available_slots=summary.available_slots,
            current_turn=DashboardController._current_turn(session, process_id),
        )

    # ── Section builders ─────────────────────────────────────────────────────

    @staticmethod
    def _planning_section(session: Session, process_id: uuid.UUID) -> PlanningSection:
        """Build the planning section; an absent plan yields an empty section."""
        plan = DashboardController._plan_row(session, process_id)
        if plan is None:
            return PlanningSection()
        return PlanningSection(
            teaching_plan_id=plan.id,
            status=plan.status,
            balance=PlanningCalculationService.compute_plan_balance(session, plan),
            validations=PlanValidationService.compute_plan_validations(session, plan),
        )

    @staticmethod
    def _assignment_section(
        session: Session, process: AssignmentProcess
    ) -> AssignmentSection:
        return AssignmentSection(
            summary=AssignmentCalculationService.compute_assignment_summary(
                session, process
            ),
            validations=AssignmentValidationService.compute_assignment_validations(
                session, process
            ),
        )

    @staticmethod
    def _blocking_count(
        planning: PlanningSection, assignment: AssignmentSection
    ) -> int:
        """Blocking findings across both stages (0 for a stage with no plan)."""
        planning_blocking = (
            0 if planning.validations is None else planning.validations.blocking_count
        )
        return planning_blocking + assignment.validations.blocking_count

    # ── Internal lookups ─────────────────────────────────────────────────────

    @staticmethod
    def _current_turn(
        session: Session, process_id: uuid.UUID
    ) -> CurrentTurnSummary | None:
        """Return the process's active selection turn, if one exists."""
        statement = (
            select(SelectionTurn)
            .where(SelectionTurn.process_teacher_id == ProcessTeacher.id)
            .where(ProcessTeacher.assignment_process_id == process_id)
            .where(SelectionTurn.status == SelectionTurnStatus.ACTIVE)
        )
        turn = session.exec(statement).first()
        if turn is None:
            return None
        # Built field by field: the row's primary key is ``id``, which the
        # payload exposes as ``selection_turn_id``, so attribute validation
        # cannot map it.
        return CurrentTurnSummary(
            meeting_session_id=turn.meeting_session_id,
            selection_turn_id=turn.id,
            process_teacher_id=turn.process_teacher_id,
            position=turn.position,
            status=turn.status,
            started_at=turn.started_at,
        )

    @staticmethod
    def _linked_participant_or_404(
        session: Session, process_id: uuid.UUID, current_user: UserModel
    ) -> tuple[ProcessTeacher, TeacherProfile]:
        user_id = uuid.UUID(str(current_user.id))
        statement = (
            select(ProcessTeacher, TeacherProfile)
            .where(ProcessTeacher.assignment_process_id == process_id)
            .where(ProcessTeacher.teacher_profile_id == TeacherProfile.id)
            .where(TeacherProfile.user_id == user_id)
        )
        row = session.exec(statement).first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No teacher profile is linked to this auth user.",
            )
        return row

    @staticmethod
    def _plan_row(session: Session, process_id: uuid.UUID) -> TeachingPlan | None:
        statement = select(TeachingPlan).where(
            TeachingPlan.assignment_process_id == process_id
        )
        return session.exec(statement).first()


__all__ = ["DashboardController"]
