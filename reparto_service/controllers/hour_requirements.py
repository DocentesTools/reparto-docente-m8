"""HourRequirement controller (per process).

Redesigned for the three-stage adaptation (plan §5.9, §20.8, §20.12):
``HourRequirement`` rows are **generated** teacher-position slots, never manually
created, updated or deleted. This controller therefore exposes read access plus
the plan §7.5 *generation* flow (``generation-preview`` / ``generate``) that
produces and retires those rows.

Generation (plan §7.5, §20.8) is a deterministic diff of the plan's live
activities against its current live requirement slots. For every live activity
it wants one slot per teacher position — logical identity
``(teaching_activity_id, position_index)`` — with the indivisible hours taken
from the activity's ``teacher_weekly_hours_per_position``. Each existing live
slot is then classified by the §20.8 identity model:

* **unchanged** (same value fingerprint) → preserved: keeps its id and any
  assignment, only its ``last_validated_generation`` advances;
* **new** logical position → created with a fresh id at the new generation;
* **removed / value-changed but unassigned** → retired (``retired_generation``
  set, status ``STALE``); a value change also creates a replacement slot;
* **removed / value-changed but assigned** → a *conflict*: it must go through the
  reconciliation flow (plan §7.5, §9) — ``generate`` refuses (409) rather than
  ever silently overwriting or deleting an assignment.

``generation-preview`` is a pure dry-run; ``generate`` re-runs the identical plan
and applies it, so the two can never diverge (the same pattern the group-subject
bulk flow uses). The reconciliation-preview / reconcile flow (plan §7.5) that
resolves conflicts is its own later task.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.controllers.teaching_plans import TeachingPlanController
from reparto_service.core.decimals import quantize_hours
from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.hour_requirements import (
    HourRequirement,
    HourRequirementPublic,
    HourRequirementsPublic,
    RequirementGenerationPreview,
    RequirementGenerationResult,
    RequirementSlotPlan,
)
from reparto_service.db_models.teaching_activities import TeachingActivity
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    AssignmentStatus,
    HourRequirementStatus,
    TeachingPlanStatus,
)

# Plan statuses from which a generation may run (plan §20.8, §20.14): the plan is
# LOCKED (first generation of the just-locked plan) or STALE (regeneration of an
# invalidated plan with no assignment conflicts). A plan carrying assignments to
# reconcile is RECONCILIATION_REQUIRED and routes through the reconcile flow.
_GENERATABLE_PLAN_STATUSES: frozenset[TeachingPlanStatus] = frozenset(
    {
        TeachingPlanStatus.LOCKED,
        TeachingPlanStatus.STALE,
    }
)


@dataclass
class _GenerationPlan:
    """Pure, executable diff produced by :meth:`_plan_generation`."""

    next_generation_number: int
    to_create: list[tuple[uuid.UUID, int, float]] = field(default_factory=list)
    to_preserve: list[HourRequirement] = field(default_factory=list)
    to_retire: list[HourRequirement] = field(default_factory=list)
    conflicts: list[HourRequirement] = field(default_factory=list)

    @property
    def is_noop(self) -> bool:
        return not (self.to_create or self.to_retire or self.conflicts)


class HourRequirementController(DomainController):
    """Read and generate the requirement slots of one process (plan §5.9, §7.5)."""

    @staticmethod
    def list_requirements(
        session: Session, process_id: uuid.UUID
    ) -> HourRequirementsPublic:
        DomainController.get_process_or_404(session, process_id)
        statement = (
            select(HourRequirement)
            .where(HourRequirement.assignment_process_id == process_id)
            .order_by(
                col(HourRequirement.teaching_activity_id),
                col(HourRequirement.position_index),
            )
        )
        items = list(session.exec(statement).all())
        return HourRequirementsPublic(
            data=[HourRequirementPublic.model_validate(item) for item in items],
            count=len(items),
        )

    @staticmethod
    def get_requirement(
        session: Session, process_id: uuid.UUID, requirement_id: uuid.UUID
    ) -> HourRequirementPublic:
        requirement = HourRequirementController._get_or_404(
            session, process_id, requirement_id
        )
        return HourRequirementPublic.model_validate(requirement)

    # ── Generation (plan §7.5, §20.8) ────────────────────────────────────────

    @staticmethod
    def generation_preview(
        session: Session, process_id: uuid.UUID
    ) -> RequirementGenerationPreview:
        """Dry-run the next generation without mutating any row (plan §7.5).

        Requires a plan in a generatable state (LOCKED/STALE); reports the
        create/preserve/retire diff and any assigned-slot conflicts that would
        force reconciliation.
        """
        DomainController.get_process_or_404(session, process_id)
        plan = HourRequirementController._require_generatable_plan(session, process_id)
        generation = HourRequirementController._plan_generation(session, plan)
        return HourRequirementController._to_preview(generation)

    @staticmethod
    def generate(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
    ) -> RequirementGenerationResult:
        """Apply the next generation deterministically (plan §7.5, §20.8).

        One slot per teacher position of every live activity; unchanged slots
        keep their id and assignment, removed unassigned slots retire, and the
        plan advances to ``REQUIREMENTS_GENERATED`` at the new generation number.
        Refuses (409) when a change would touch an **assigned** slot — those
        route through reconciliation so an assignment is never silently dropped
        (plan §7.5, §9).
        """
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        plan = HourRequirementController._require_generatable_plan(session, process_id)
        generation = HourRequirementController._plan_generation(session, plan)

        if generation.conflicts:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"{len(generation.conflicts)} assigned requirement slot(s) would "
                    "change; resolve them through reconciliation before "
                    "regenerating (plan §7.5, §9)."
                ),
            )

        number = generation.next_generation_number
        for requirement in generation.to_preserve:
            requirement.last_validated_generation = number
            session.add(requirement)
        for requirement in generation.to_retire:
            requirement.retired_generation = number
            requirement.status = HourRequirementStatus.STALE
            session.add(requirement)
        # Flush retirements before the inserts so a re-created position does not
        # collide with the old row on the active-slot partial-unique index
        # (retired rows are excluded by ``retired_generation IS NULL``).
        if generation.to_retire:
            session.flush()

        created: list[HourRequirement] = []
        for activity_id, position_index, hours in generation.to_create:
            requirement = HourRequirement(
                assignment_process_id=process_id,
                teaching_activity_id=activity_id,
                position_index=position_index,
                required_teacher_hours=hours,
                created_generation=number,
                last_validated_generation=number,
                status=HourRequirementStatus.AVAILABLE,
            )
            session.add(requirement)
            created.append(requirement)

        plan.current_generation_number = number
        plan.requirements_generated_at = datetime.now(tz=timezone.utc)
        TeachingPlanController.apply_status_transition(
            plan, TeachingPlanStatus.REQUIREMENTS_GENERATED
        )
        session.add(plan)
        HourRequirementController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="requirements.generated",
            entity_type="teaching_plan",
            entity_id=plan.id,
            before=None,
            after=plan,
        )
        session.commit()

        for requirement in created:
            session.refresh(requirement)
        live = HourRequirementController._live_requirements(session, process_id)
        return RequirementGenerationResult(
            generation_number=number,
            created=[HourRequirementPublic.model_validate(r) for r in created],
            created_count=len(created),
            preserved_count=len(generation.to_preserve),
            retired_count=len(generation.to_retire),
            data=[HourRequirementPublic.model_validate(r) for r in live],
            count=len(live),
        )

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _plan_generation(session: Session, plan: TeachingPlan) -> _GenerationPlan:
        """Compute the deterministic generation diff (pure — no mutation).

        Shared by ``generation-preview`` and ``generate`` so the dry-run and the
        applied run can never diverge.
        """
        process_id = plan.assignment_process_id
        result = _GenerationPlan(
            next_generation_number=plan.current_generation_number + 1
        )

        # Target slots: one per teacher position of every live activity, keyed by
        # logical identity (activity, position), ordered by (activity id, index).
        activities = session.exec(
            select(TeachingActivity)
            .where(TeachingActivity.teaching_plan_id == plan.id)
            .where(col(TeachingActivity.retired_at).is_(None))
            .order_by(col(TeachingActivity.id))
        ).all()
        target: dict[tuple[uuid.UUID, int], float] = {}
        for activity in activities:
            for position in range(activity.required_teacher_count):
                target[(activity.id, position)] = (
                    activity.teacher_weekly_hours_per_position
                )

        # Current live slots (retired rows are already free), one per logical slot
        # (guaranteed by the active-slot partial-unique index).
        live = HourRequirementController._live_requirements(session, process_id)
        live_by_slot = {(r.teaching_activity_id, r.position_index): r for r in live}
        assigned_ids = HourRequirementController._active_assigned_ids(
            session, process_id
        )

        for slot in sorted(target, key=lambda s: (str(s[0]), s[1])):
            hours = target[slot]
            requirement = live_by_slot.get(slot)
            if requirement is None:
                result.to_create.append((slot[0], slot[1], hours))
            elif _hours_equal(requirement.required_teacher_hours, hours):
                result.to_preserve.append(requirement)
            elif requirement.id in assigned_ids:
                result.conflicts.append(requirement)
            else:
                # Value change on an unassigned slot: retire the old row and
                # generate a fresh one for the same logical position (plan §20.8).
                result.to_retire.append(requirement)
                result.to_create.append((slot[0], slot[1], hours))

        for slot in sorted(live_by_slot, key=lambda s: (str(s[0]), s[1])):
            if slot in target:
                continue
            requirement = live_by_slot[slot]
            if requirement.id in assigned_ids:
                result.conflicts.append(requirement)
            else:
                result.to_retire.append(requirement)

        return result

    @staticmethod
    def _to_preview(generation: _GenerationPlan) -> RequirementGenerationPreview:
        """Render a computed diff as the public preview schema."""
        return RequirementGenerationPreview(
            next_generation_number=generation.next_generation_number,
            to_create=[
                RequirementSlotPlan(
                    teaching_activity_id=activity_id,
                    position_index=position_index,
                    required_teacher_hours=hours,
                )
                for activity_id, position_index, hours in generation.to_create
            ],
            create_count=len(generation.to_create),
            preserve_ids=[r.id for r in generation.to_preserve],
            preserve_count=len(generation.to_preserve),
            retire_ids=[r.id for r in generation.to_retire],
            retire_count=len(generation.to_retire),
            conflict_ids=[r.id for r in generation.conflicts],
            conflict_count=len(generation.conflicts),
            requires_reconciliation=bool(generation.conflicts),
            is_noop=generation.is_noop,
        )

    @staticmethod
    def _require_generatable_plan(
        session: Session, process_id: uuid.UUID
    ) -> TeachingPlan:
        """Return the process's plan, or 400 when it is missing or not generatable."""
        plan = session.exec(
            select(TeachingPlan).where(TeachingPlan.assignment_process_id == process_id)
        ).first()
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Process {process_id} has no teaching plan; create and lock "
                    "one before generating requirements."
                ),
            )
        if plan.status not in _GENERATABLE_PLAN_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Teaching plan is {plan.status.value}; lock the plan "
                    "(or regenerate a stale plan) before generating requirements "
                    "(plan §7.5, §20.8)."
                ),
            )
        return plan

    @staticmethod
    def _live_requirements(
        session: Session, process_id: uuid.UUID
    ) -> list[HourRequirement]:
        """Live (non-retired) requirement slots, ordered by (activity, position)."""
        return list(
            session.exec(
                select(HourRequirement)
                .where(HourRequirement.assignment_process_id == process_id)
                .where(col(HourRequirement.retired_generation).is_(None))
                .order_by(
                    col(HourRequirement.teaching_activity_id),
                    col(HourRequirement.position_index),
                )
            ).all()
        )

    @staticmethod
    def _active_assigned_ids(session: Session, process_id: uuid.UUID) -> set[uuid.UUID]:
        """Requirement ids that currently carry an ACTIVE assignment."""
        return set(
            session.exec(
                select(Assignment.hour_requirement_id)
                .where(Assignment.assignment_process_id == process_id)
                .where(Assignment.status == AssignmentStatus.ACTIVE)
            ).all()
        )

    @staticmethod
    def _get_or_404(
        session: Session, process_id: uuid.UUID, requirement_id: uuid.UUID
    ) -> HourRequirement:
        DomainController.get_process_or_404(session, process_id)
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


def _hours_equal(left: float, right: float) -> bool:
    """Compare two hour values as canonical two-place decimals (plan §3.9)."""
    return quantize_hours(Decimal(str(left))) == quantize_hours(Decimal(str(right)))


__all__ = ["HourRequirementController"]
