"""TeachingPlan controller.

Owns the one-to-one plan-per-process invariant and the operational lifecycle
guard for the intermediate teaching plan (plan §5.2, §9, §20.14):

* exactly one plan exists per assignment process; a second create is rejected;
* the plan is created in ``DRAFT`` with generation ``0`` and feasibility
  ``NOT_EVALUATED``;
* every status change is validated against
  :data:`~reparto_service.services.planning_lifecycle.TEACHING_PLAN_LIFECYCLE`
  before it is applied, so no controller can drive an illegal edge;
* marking the plan stale resets feasibility to ``NOT_EVALUATED`` (plan §20.14).

The balance, lock, requirement-generation and feasibility-evaluation *behaviour*
that drives these transitions lives in the dedicated later tasks (plan §13.1
"Replace SummaryService…", "Build plan lock…", §20.20 feasibility items); this
controller establishes the model, the ownership invariant and the reusable
lifecycle guard those tasks build on. ``mark_stale`` is the concrete
allocation-change side effect (plan §3.11, §9) exposed for that wiring.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.teaching_plans import (
    TeachingPlan,
    TeachingPlanPublic,
)
from reparto_service.enums import AuditEventType, FeasibilityStatus, TeachingPlanStatus
from reparto_service.schemas.planning import PlanValidationReport
from reparto_service.services.planning_lifecycle import (
    TEACHING_PLAN_LIFECYCLE,
    IllegalStateTransitionError,
)
from reparto_service.services.validations import PlanValidationService


class TeachingPlanController(DomainController):
    """Read, create and lifecycle-guard logic for the per-process teaching plan."""

    @staticmethod
    def get_plan(session: Session, process_id: uuid.UUID) -> TeachingPlanPublic:
        DomainController.get_process_or_404(session, process_id)
        plan = TeachingPlanController._plan_row(session, process_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No teaching plan for process {process_id}.",
            )
        return TeachingPlanPublic.model_validate(plan)

    @staticmethod
    def get_validations(
        session: Session, process_id: uuid.UUID
    ) -> PlanValidationReport:
        """Return the plan's blocking/warning findings (plan §6.3, §6.4, §7.3).

        Read-only and solver-free (plan §20.23): it reports the stored
        feasibility status but never triggers an evaluation.
        """
        DomainController.get_process_or_404(session, process_id)
        plan = TeachingPlanController._plan_row(session, process_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No teaching plan for process {process_id}.",
            )
        return PlanValidationService.compute_plan_validations(session, plan)

    @staticmethod
    def create_plan(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
    ) -> TeachingPlanPublic:
        """Create the single plan for a process, enforcing one-per-process.

        A ``final``/``archived`` process must be reopened first (plan §8.4); a
        second create attempt on a process that already owns a plan is a 409.
        """
        process = DomainController.get_process_or_404(session, process_id)
        DomainController.ensure_process_mutable(process)

        if TeachingPlanController._plan_row(session, process_id) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Process {process_id} already has a teaching plan.",
            )

        plan = TeachingPlan(
            assignment_process_id=process_id,
            status=TeachingPlanStatus.DRAFT,
            current_generation_number=0,
            feasibility_status=FeasibilityStatus.NOT_EVALUATED,
        )
        session.add(plan)
        TeachingPlanController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type=AuditEventType.TEACHING_PLAN_CREATED,
            entity_type="teaching_plan",
            entity_id=plan.id,
            before=None,
            after=plan,
        )
        session.commit()
        session.refresh(plan)
        return TeachingPlanPublic.model_validate(plan)

    @staticmethod
    def mark_stale(
        session: Session,
        process_id: uuid.UUID,
        reason: str,
        current_user: UserModel,
    ) -> TeachingPlanPublic:
        """Mark the plan stale after an allocation change (plan §3.11, §9, §20.14).

        Only a ``LOCKED`` or ``REQUIREMENTS_GENERATED`` plan can go stale (an
        unlocked plan recalculates in place instead — plan §20.14); the
        lifecycle guard raises 409 for any other current status. Feasibility is
        reset to ``NOT_EVALUATED`` because the inputs changed (plan §20.14).
        """
        plan = TeachingPlanController._plan_row(session, process_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No teaching plan for process {process_id}.",
            )
        TeachingPlanController.apply_status_transition(
            plan, TeachingPlanStatus.STALE, stale_reason=reason
        )
        session.add(plan)
        TeachingPlanController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type=AuditEventType.TEACHING_PLAN_STALE,
            entity_type="teaching_plan",
            entity_id=plan.id,
            before=None,
            after=plan,
            reason=reason,
        )
        session.commit()
        session.refresh(plan)
        return TeachingPlanPublic.model_validate(plan)

    # ── Lifecycle guard (reused by later balance/lock/generation tasks) ──────

    @staticmethod
    def apply_status_transition(
        plan: TeachingPlan,
        target: TeachingPlanStatus,
        *,
        stale_reason: str | None = None,
    ) -> None:
        """Validate and apply a plan status change against the lifecycle table.

        Raises 409 on an illegal edge. Moving to ``STALE`` records the reason
        and resets feasibility to ``NOT_EVALUATED`` (plan §20.14); leaving
        ``STALE`` clears the reason. Does not commit — the caller owns the
        transaction.
        """
        try:
            TEACHING_PLAN_LIFECYCLE.assert_allowed(plan.status, target)
        except IllegalStateTransitionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        plan.status = target
        if target == TeachingPlanStatus.STALE:
            plan.stale_reason = stale_reason
            TeachingPlanController._reset_feasibility(plan)
        else:
            plan.stale_reason = None

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _reset_feasibility(plan: TeachingPlan) -> None:
        """Drop any stored feasibility result and its provenance (plan §20.14)."""
        plan.feasibility_status = FeasibilityStatus.NOT_EVALUATED
        plan.feasibility_generation = None
        plan.feasibility_checked_at = None
        plan.feasibility_input_fingerprint = None
        plan.feasibility_solver_version = None
        plan.feasibility_diagnostics_ref = None

    @staticmethod
    def _plan_row(session: Session, process_id: uuid.UUID) -> TeachingPlan | None:
        """Return the process's single plan, or ``None`` if it has none."""
        return session.exec(
            select(TeachingPlan).where(TeachingPlan.assignment_process_id == process_id)
        ).first()


__all__ = ["TeachingPlanController"]
