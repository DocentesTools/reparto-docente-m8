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

``get_summary`` and ``get_validations`` are the plan's read surface (plan §7.3):
both delegate to the services that own the numbers and the findings, and both
are solver-free. Locking evaluates the exact intended requirement generation,
fails closed unless it is feasible, and persists the witness that generation
must match. This controller also exposes the administrator-only feasibility
evaluation, witness retrieval and diagnostics operations from §7.3 and §20.20.
``mark_stale`` is the concrete allocation-change side effect (plan §3.11, §9)
exposed for that wiring.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from fastapi_m8 import UserModel
from sqlmodel import Session, select

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.teaching_plans import (
    TeachingPlan,
    TeachingPlanPublic,
)
from reparto_service.db_models.feasibility_witnesses import (
    FeasibilityDiagnosticsPublic,
    FeasibilityEvaluationPublic,
    FeasibilityWitnessPublic,
)
from reparto_service.enums import (
    AuditEventType,
    FeasibilityStatus,
    SseEventType,
    TeachingPlanStatus,
)
from reparto_service.schemas.planning import PlanBalance, PlanValidationReport
from reparto_service.services.calculations import PlanningCalculationService
from reparto_service.services.planning_lifecycle import (
    TEACHING_PLAN_LIFECYCLE,
    IllegalStateTransitionError,
)
from reparto_service.services.validations import PlanValidationService
from reparto_service.services.feasibility_witnesses import FeasibilityWitnessService


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
    def get_summary(session: Session, process_id: uuid.UUID) -> PlanBalance:
        """Return both independent plan balances (plan §3.1, §6.1, §7.3).

        The planning-stage numbers only: the group teaching-hour balance against
        the current leadership allocation and the teacher workload balance
        against the participant target total. They are reported on separate axes
        and never summed — plan §3.2's co-teaching case is 120 group hours /
        124 teacher-load hours, and both are correct.
        """
        DomainController.get_process_or_404(session, process_id)
        plan = TeachingPlanController._plan_row(session, process_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No teaching plan for process {process_id}.",
            )
        return PlanningCalculationService.compute_plan_balance(session, plan)

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
    def evaluate_feasibility(
        session: Session, process_id: uuid.UUID
    ) -> FeasibilityEvaluationPublic:
        """Run or reuse the bounded solver for the current exact fingerprint."""

        DomainController.get_process_or_404(session, process_id)
        return FeasibilityWitnessService.evaluate(session, process_id)

    @staticmethod
    def get_feasibility_witness(
        session: Session, process_id: uuid.UUID
    ) -> FeasibilityWitnessPublic:
        """Return the current complete witness to an authorized caller."""

        DomainController.get_process_or_404(session, process_id)
        return FeasibilityWitnessService.get_witness(session, process_id)

    @staticmethod
    def get_feasibility_diagnostics(
        session: Session, process_id: uuid.UUID
    ) -> FeasibilityDiagnosticsPublic:
        """Return the latest evaluation's findings to an authorized caller."""

        DomainController.get_process_or_404(session, process_id)
        return FeasibilityWitnessService.get_diagnostics(session, process_id)

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
        TeachingPlanController._publish_plan_event(
            session, plan, SseEventType.TEACHING_PLAN_UPDATED
        )
        return TeachingPlanPublic.model_validate(plan)

    @staticmethod
    def lock_plan(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
    ) -> TeachingPlanPublic:
        """Lock an exact plan only after its intended reparto is FEASIBLE."""

        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        plan = TeachingPlanController._plan_row(session, process_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No teaching plan for process {process_id}.",
            )
        if plan.status != TeachingPlanStatus.BALANCED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot lock the teaching plan while it is {plan.status.value}; "
                    "both planning balances must be exact first."
                ),
            )
        FeasibilityWitnessService.require_intended_feasible(
            session, process_id, operation="lock the teaching plan"
        )
        before = TeachingPlan.model_validate(plan.model_dump())
        TeachingPlanController.apply_status_transition(plan, TeachingPlanStatus.LOCKED)
        plan.locked_at = datetime.now(tz=timezone.utc)
        plan.locked_by_user_id = uuid.UUID(str(current_user.id))
        session.add(plan)
        TeachingPlanController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type=AuditEventType.TEACHING_PLAN_LOCKED,
            entity_type="teaching_plan",
            entity_id=plan.id,
            before=before,
            after=plan,
        )
        session.commit()
        session.refresh(plan)
        TeachingPlanController._publish_plan_event(
            session, plan, SseEventType.TEACHING_PLAN_LOCKED
        )
        return TeachingPlanPublic.model_validate(plan)

    @staticmethod
    def unlock_plan(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
    ) -> TeachingPlanPublic:
        """Return a locked pre-generation plan to balanced editing."""

        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        plan = TeachingPlanController._plan_row(session, process_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No teaching plan for process {process_id}.",
            )
        if plan.status != TeachingPlanStatus.LOCKED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot unlock the teaching plan while it is {plan.status.value}; "
                    "only a locked pre-generation plan can be unlocked."
                ),
            )
        before = TeachingPlan.model_validate(plan.model_dump())
        TeachingPlanController.apply_status_transition(
            plan, TeachingPlanStatus.BALANCED
        )
        plan.locked_at = None
        plan.locked_by_user_id = None
        session.add(plan)
        TeachingPlanController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type=AuditEventType.TEACHING_PLAN_UNLOCKED,
            entity_type="teaching_plan",
            entity_id=plan.id,
            before=before,
            after=plan,
        )
        session.commit()
        session.refresh(plan)
        TeachingPlanController._publish_plan_event(
            session, plan, SseEventType.TEACHING_PLAN_UPDATED
        )
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
        # Read before the transition: moving to STALE resets the stored status
        # itself, so asking `invalidate` afterwards would always answer "nothing
        # was dropped" and the transition would never reach a subscriber.
        invalidated = plan.feasibility_status != FeasibilityStatus.NOT_EVALUATED
        TeachingPlanController.apply_status_transition(
            plan, TeachingPlanStatus.STALE, stale_reason=reason
        )
        FeasibilityWitnessService.invalidate(session, process_id)
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
        TeachingPlanController._publish_plan_event(
            session, plan, SseEventType.TEACHING_PLAN_STALE, reason=reason
        )
        if invalidated:
            TeachingPlanController.publish_feasibility_invalidated(session, process_id)
        return TeachingPlanPublic.model_validate(plan)

    # ── SSE emission (plan §11) ──────────────────────────────────────────────

    @staticmethod
    def _publish_plan_event(
        session: Session,
        plan: TeachingPlan,
        event_type: SseEventType,
        *,
        reason: str | None = None,
    ) -> None:
        """Fan a committed plan-status change out to subscribers (plan §11).

        Shared by every plan emit site so each one publishes the same shape.
        ``status`` is department-head-only by construction: the teacher and
        shared-screen tiers receive the coarse readiness the projection derives
        instead (plan §20.25), never the raw planning stage.
        """
        TeachingPlanController.publish_event(
            session,
            process_id=plan.assignment_process_id,
            event_type=event_type,
            payload={
                "teaching_plan_id": str(plan.id),
                "status": plan.status.value,
                "current_generation_number": plan.current_generation_number,
                "feasibility_status": plan.feasibility_status.value,
                "reason": reason,
            },
        )

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
