"""Plan-readiness gates for meeting and assignment-stage operations.

Centralises the plan §3.10 / §3.11 / §9 rule that operational steps are gated on
the teaching plan being *ready*, so every gated operation enforces one plan-status
contract exactly once (mirroring how
:mod:`reparto_service.services.process_lifecycle` centralises the process state
machine).

Two complementary gates are exposed:

* :meth:`PlanReadinessGate.ensure_ready_for_assignment_stage` — the strict
  **stage-entry** gate (plan §3.10). Opening a meeting requires a balanced,
  locked, generated plan (``REQUIREMENTS_GENERATED``): only then do the
  indivisible teacher-position slots exist to be assigned. Any earlier status
  (``DRAFT``/``UNBALANCED``/``BALANCED``/``LOCKED``), a plan invalidated to
  ``STALE``/``RECONCILIATION_REQUIRED`` by an allocation change, or no plan at
  all is refused with 409.
* :meth:`PlanReadinessGate.ensure_assignments_unblocked` — the lenient
  **mid-flight** gate (plan §3.11.9, §9.7). A new assignment operation is blocked
  only while an allocation change leaves the generated plan ``STALE`` or
  ``RECONCILIATION_REQUIRED`` (assignments are never silently overwritten — the
  head must reconcile first). A plan that is still ``REQUIREMENTS_GENERATED`` (or
  absent — the slot lookup then 404s on its own) is allowed through, so this gate
  guards the meeting-time hot path without re-imposing the full stage-entry
  contract that opening the meeting already enforced.

Feasibility (the third invariant, plan §20.1) is intentionally *not* consulted
here: wiring ``feasibility_status != FEASIBLE`` into these same gates is the
separate plan §20.20 "Wire feasibility into lifecycle gates" task. This module
gates on plan **status** only.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, select

from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import TeachingPlanStatus

# The only plan status in which generated indivisible slots exist and may be
# assigned (plan §5.2, §5.9): a balanced, locked and generated plan.
ASSIGNMENT_READY_PLAN_STATUSES: frozenset[TeachingPlanStatus] = frozenset(
    {TeachingPlanStatus.REQUIREMENTS_GENERATED}
)

# An allocation change (plan §3.11, §9) drives a generated plan to STALE or
# RECONCILIATION_REQUIRED; while it sits in either, every new assignment
# operation is blocked until the head reconciles.
ASSIGNMENT_BLOCKING_PLAN_STATUSES: frozenset[TeachingPlanStatus] = frozenset(
    {TeachingPlanStatus.STALE, TeachingPlanStatus.RECONCILIATION_REQUIRED}
)


class PlanReadinessGate:
    """Stateless plan-status guards for gated lifecycle operations."""

    @staticmethod
    def ensure_ready_for_assignment_stage(
        session: Session, process_id: uuid.UUID, *, operation: str
    ) -> TeachingPlan:
        """Refuse a stage-entry operation unless the plan is assignment-ready.

        Returns the ready plan so callers can reuse it. Raises 409 when the
        process has no plan, or its plan is not ``REQUIREMENTS_GENERATED`` — an
        inexact, unlocked, un-generated, stale or reconciliation-required plan
        (plan §3.10).
        """
        plan = PlanReadinessGate._plan_row(session, process_id)
        if plan is None:
            raise PlanReadinessGate._conflict(
                operation,
                "no teaching plan exists yet; complete planning and generate "
                "requirement slots first",
            )
        if plan.status not in ASSIGNMENT_READY_PLAN_STATUSES:
            raise PlanReadinessGate._conflict(
                operation, PlanReadinessGate._not_ready_reason(plan.status)
            )
        return plan

    @staticmethod
    def ensure_assignments_unblocked(
        session: Session, process_id: uuid.UUID, *, operation: str
    ) -> None:
        """Refuse a new assignment operation while reconciliation is pending.

        Blocks only when the plan is ``STALE`` or ``RECONCILIATION_REQUIRED``
        (plan §3.11.9, §9.7). A missing plan or a still-generated plan is allowed
        through — a missing/ungenerated plan simply has no slots to assign, and a
        generated plan already passed the stage-entry gate when the meeting
        opened.
        """
        plan = PlanReadinessGate._plan_row(session, process_id)
        if plan is not None and plan.status in ASSIGNMENT_BLOCKING_PLAN_STATUSES:
            raise PlanReadinessGate._conflict(
                operation, PlanReadinessGate._not_ready_reason(plan.status)
            )

    @staticmethod
    def _not_ready_reason(plan_status: TeachingPlanStatus) -> str:
        """Human-readable reason a plan in ``plan_status`` blocks an operation."""
        if plan_status == TeachingPlanStatus.STALE:
            return (
                "the teaching plan is stale after an allocation change and must "
                "be reconciled"
            )
        if plan_status == TeachingPlanStatus.RECONCILIATION_REQUIRED:
            return "the teaching plan has affected assignments awaiting reconciliation"
        return (
            f"the teaching plan is {plan_status.value}; it must be balanced, "
            "locked and have generated requirement slots (requirements_generated)"
        )

    @staticmethod
    def _conflict(operation: str, reason: str) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot {operation}: {reason}.",
        )

    @staticmethod
    def _plan_row(session: Session, process_id: uuid.UUID) -> TeachingPlan | None:
        return session.exec(
            select(TeachingPlan).where(TeachingPlan.assignment_process_id == process_id)
        ).first()


__all__ = [
    "ASSIGNMENT_BLOCKING_PLAN_STATUSES",
    "ASSIGNMENT_READY_PLAN_STATUSES",
    "PlanReadinessGate",
]
