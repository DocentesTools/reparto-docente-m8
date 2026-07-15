"""Dual planning and assignment calculation services (plan §6.1, §6.2).

These stateless services are the single home for every hour formula in the
three-stage domain. They replace the old single-global-balance
``SummaryService`` (which modelled required-hours / partial coverage /
over-assignment overrides that no longer exist) with the two independent
balances of plan §3.1:

* :class:`PlanningCalculationService` — the *planning* stage math (plan §6.1):
  per-activity group and teacher loads, the two plan-wide totals, their targets
  (current allocation; participant target total) and the balanced/exact result.
* :class:`AssignmentCalculationService` — the *assignment* stage math
  (plan §6.2): per-participant assigned/remaining hours, the derived participant
  state, per-requirement coverage state and the process assignment summary.

Design rules honoured here:

* **No controller-side arithmetic** (plan §6): controllers and routes call these
  methods; they never add hours themselves.
* **Decimal, two-place, non-binary comparisons** (plan §3.9): the model hour
  columns are still ``float`` today (the column sweep is a dedicated later task),
  so every value is lifted into a two-place :class:`~decimal.Decimal` via its
  string form before any arithmetic or equality check — a balance is "exact"
  only when the quantized difference is exactly ``Decimal("0.00")``.
* **Live rows only**: retired activities (``retired_at``) and retired requirement
  slots (``retired_generation``) are excluded from every total, and only
  ``ACTIVE`` assignments count (plan §5.10).

The feasibility third invariant (plan §20.1) and the blocking/warning validation
*messages* (plan §6.3/§6.4) are deliberately out of scope here — they are their
own later tasks ("Implement planning validations", "Implement assignment
validations", §20.20 feasibility items). This module owns the numbers only.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func
from sqlmodel import Session, col, select

from reparto_service.core.decimals import quantize_hours
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.department_hour_allocation_revisions import (
    DepartmentHourAllocationRevision,
)
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.db_models.teaching_activities import (
    TeachingActivity,
    TeachingActivityGroup,
)
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    AssignmentStatus,
    HourRequirementStatus,
    ParticipantBalanceState,
    ProcessTeacherStatus,
)
from reparto_service.schemas.planning import (
    AssignmentSummary,
    GroupBalance,
    ParticipantBalance,
    PlanBalance,
    TeacherLoadBalance,
)

#: The canonical two-place zero every "is exact/balanced" check compares against.
_ZERO = Decimal("0.00")


def _dec(value: float | int | Decimal) -> Decimal:
    """Lift a stored (``float``) hour value into a two-place ``Decimal``.

    Uses the string form so the binary-float representation never leaks into a
    domain decision (plan §3.9); the column-level ``Decimal`` migration will make
    this conversion a no-op. ``str(Decimal(...))`` is a safe round-trip, so a
    value that is already a ``Decimal`` passes through unchanged too.
    """
    return quantize_hours(Decimal(str(value)))


class PlanningCalculationService:
    """Planning-stage calculations (plan §6.1). All methods are pure of session state."""

    # ── Per-activity formulas ────────────────────────────────────────────────

    @staticmethod
    def compute_activity_group_load(
        activity: TeachingActivity, linked_group_count: int
    ) -> Decimal:
        """group_weekly_hours_per_group × linked_group_count (plan §3.4, §6.1)."""
        return quantize_hours(
            _dec(activity.group_weekly_hours_per_group) * linked_group_count
        )

    @staticmethod
    def compute_activity_teacher_load(activity: TeachingActivity) -> Decimal:
        """teacher_weekly_hours_per_position × required_teacher_count (plan §3.1)."""
        return quantize_hours(
            _dec(activity.teacher_weekly_hours_per_position)
            * activity.required_teacher_count
        )

    # ── Plan-wide loads ──────────────────────────────────────────────────────

    @staticmethod
    def _live_activities_with_counts(
        session: Session, plan: TeachingPlan
    ) -> list[tuple[TeachingActivity, int]]:
        """Return live activities of the plan with their linked-group counts."""
        activities = list(
            session.exec(
                select(TeachingActivity)
                .where(TeachingActivity.teaching_plan_id == plan.id)
                .where(col(TeachingActivity.retired_at).is_(None))
            ).all()
        )
        counts = dict(
            session.exec(
                select(
                    TeachingActivityGroup.teaching_activity_id,
                    func.count(col(TeachingActivityGroup.id)),
                ).group_by(col(TeachingActivityGroup.teaching_activity_id))
            ).all()
        )
        return [(activity, int(counts.get(activity.id, 0))) for activity in activities]

    @staticmethod
    def compute_total_group_load(session: Session, plan: TeachingPlan) -> Decimal:
        """SUM of every live activity's group load (plan §3.1, §6.1)."""
        total = _ZERO
        for activity, linked in PlanningCalculationService._live_activities_with_counts(
            session, plan
        ):
            total += PlanningCalculationService.compute_activity_group_load(
                activity, linked
            )
        return quantize_hours(total)

    @staticmethod
    def compute_total_teacher_load(session: Session, plan: TeachingPlan) -> Decimal:
        """SUM of every live activity's teacher load (plan §3.1, §6.1)."""
        total = _ZERO
        for activity, _ in PlanningCalculationService._live_activities_with_counts(
            session, plan
        ):
            total += PlanningCalculationService.compute_activity_teacher_load(activity)
        return quantize_hours(total)

    # ── Targets and differences ──────────────────────────────────────────────

    @staticmethod
    def compute_current_allocation(
        session: Session, process_id: uuid.UUID
    ) -> Decimal | None:
        """Current (non-superseded) allocated group hours, or ``None`` if unset."""
        revision = session.exec(
            select(DepartmentHourAllocationRevision)
            .where(DepartmentHourAllocationRevision.assignment_process_id == process_id)
            .where(col(DepartmentHourAllocationRevision.superseded_at).is_(None))
        ).first()
        if revision is None:
            return None
        return _dec(revision.allocated_group_weekly_hours)

    @staticmethod
    def compute_group_allocation_difference(
        session: Session, plan: TeachingPlan
    ) -> Decimal | None:
        """total_group_load − current allocation; ``None`` when no allocation."""
        allocation = PlanningCalculationService.compute_current_allocation(
            session, plan.assignment_process_id
        )
        if allocation is None:
            return None
        total = PlanningCalculationService.compute_total_group_load(session, plan)
        return quantize_hours(total - allocation)

    @staticmethod
    def compute_participant_target_total(
        session: Session, process_id: uuid.UUID
    ) -> Decimal:
        """SUM(base + extra) over ACTIVE participants (plan §3.1, §3.8)."""
        teachers = session.exec(
            select(ProcessTeacher)
            .where(ProcessTeacher.assignment_process_id == process_id)
            .where(ProcessTeacher.status == ProcessTeacherStatus.ACTIVE)
        ).all()
        total = _ZERO
        for teacher in teachers:
            total += _dec(teacher.base_weekly_hours) + _dec(teacher.extra_weekly_hours)
        return quantize_hours(total)

    @staticmethod
    def compute_teacher_load_difference(
        session: Session, plan: TeachingPlan
    ) -> Decimal:
        """total_teacher_load − participant_target_total (signed; plan §6.1)."""
        total = PlanningCalculationService.compute_total_teacher_load(session, plan)
        target = PlanningCalculationService.compute_participant_target_total(
            session, plan.assignment_process_id
        )
        return quantize_hours(total - target)

    # ── Composite balance ────────────────────────────────────────────────────

    @staticmethod
    def compute_plan_balance(session: Session, plan: TeachingPlan) -> PlanBalance:
        """Both independent balances for the plan (plan §3.1, §3.10, §6.1)."""
        total_group = PlanningCalculationService.compute_total_group_load(session, plan)
        allocation = PlanningCalculationService.compute_current_allocation(
            session, plan.assignment_process_id
        )
        group_difference = (
            None if allocation is None else quantize_hours(total_group - allocation)
        )
        group = GroupBalance(
            total_group_load=total_group,
            allocated_group_weekly_hours=allocation,
            allocation_difference=group_difference,
            is_balanced=group_difference is not None and group_difference == _ZERO,
        )

        total_teacher = PlanningCalculationService.compute_total_teacher_load(
            session, plan
        )
        target = PlanningCalculationService.compute_participant_target_total(
            session, plan.assignment_process_id
        )
        teacher_difference = quantize_hours(total_teacher - target)
        teacher = TeacherLoadBalance(
            total_teacher_load=total_teacher,
            participant_target_total=target,
            teacher_load_difference=teacher_difference,
            is_balanced=teacher_difference == _ZERO,
        )

        return PlanBalance(
            teaching_plan_id=plan.id,
            assignment_process_id=plan.assignment_process_id,
            group=group,
            teacher=teacher,
            is_exact=group.is_balanced and teacher.is_balanced,
        )


class AssignmentCalculationService:
    """Assignment-stage calculations (plan §6.2). Pure of session state."""

    # ── Per-participant hours ────────────────────────────────────────────────

    @staticmethod
    def compute_participant_assigned_hours(
        session: Session, process_teacher: ProcessTeacher
    ) -> Decimal:
        """SUM of the hours of the participant's ACTIVE slot occupancies.

        A slot is indivisible, so an assignment contributes its requirement's
        full ``required_teacher_hours`` (plan §3.6, §5.10).
        """
        rows = session.exec(
            select(HourRequirement.required_teacher_hours)
            .where(Assignment.process_teacher_id == process_teacher.id)
            .where(Assignment.status == AssignmentStatus.ACTIVE)
            .where(Assignment.hour_requirement_id == HourRequirement.id)
        ).all()
        total = _ZERO
        for hours in rows:
            total += _dec(hours)
        return quantize_hours(total)

    @staticmethod
    def compute_participant_remaining_hours(
        session: Session, process_teacher: ProcessTeacher
    ) -> Decimal:
        """target − assigned (signed; plan §6.2)."""
        assigned = AssignmentCalculationService.compute_participant_assigned_hours(
            session, process_teacher
        )
        target = _dec(process_teacher.target_weekly_hours)
        return quantize_hours(target - assigned)

    @staticmethod
    def compute_participant_state(
        process_teacher: ProcessTeacher, remaining: Decimal
    ) -> ParticipantBalanceState:
        """Classify a participant (plan §6.2).

        Precedence: INACTIVE → NOT_PARTICIPATING → OVERLOADED_AUTHORIZED →
        PENDING/BALANCED. ``OVERLOADED_AUTHORIZED`` identifies
        ``extra_weekly_hours > 0`` (authorized overload), not assigned > target.
        """
        if process_teacher.status != ProcessTeacherStatus.ACTIVE:
            return ParticipantBalanceState.INACTIVE
        if not process_teacher.participates_in_selection:
            return ParticipantBalanceState.NOT_PARTICIPATING
        if _dec(process_teacher.extra_weekly_hours) > _ZERO:
            return ParticipantBalanceState.OVERLOADED_AUTHORIZED
        if quantize_hours(remaining) > _ZERO:
            return ParticipantBalanceState.PENDING
        return ParticipantBalanceState.BALANCED

    # ── Per-requirement coverage ─────────────────────────────────────────────

    @staticmethod
    def compute_requirement_state(
        session: Session, requirement: HourRequirement
    ) -> HourRequirementStatus:
        """Effective coverage state of one slot (plan §6.2).

        Generation states (``STALE`` / ``RECONCILIATION_REQUIRED``) are reported
        verbatim; otherwise the slot is ``ASSIGNED`` when a live assignment
        occupies it and ``AVAILABLE`` when it is free (plan §5.9 — a slot has no
        partial-coverage state).
        """
        if requirement.status in {
            HourRequirementStatus.STALE,
            HourRequirementStatus.RECONCILIATION_REQUIRED,
        }:
            return requirement.status
        occupied = session.exec(
            select(Assignment.id)
            .where(Assignment.hour_requirement_id == requirement.id)
            .where(Assignment.status == AssignmentStatus.ACTIVE)
        ).first()
        return (
            HourRequirementStatus.ASSIGNED
            if occupied is not None
            else HourRequirementStatus.AVAILABLE
        )

    # ── Process assignment summary ───────────────────────────────────────────

    @staticmethod
    def compute_assignment_summary(
        session: Session, process: AssignmentProcess
    ) -> AssignmentSummary:
        """Aggregate per-participant and slot view for the process (plan §6.2)."""
        process_id = process.id

        teacher_rows = list(
            session.exec(
                select(ProcessTeacher, TeacherProfile)
                .where(ProcessTeacher.assignment_process_id == process_id)
                .where(ProcessTeacher.teacher_profile_id == TeacherProfile.id)
            ).all()
        )

        # Active assignment hours grouped by participant (one query).
        assigned_by_teacher: dict[uuid.UUID, Decimal] = {}
        count_by_teacher: dict[uuid.UUID, int] = {}
        assignment_rows = session.exec(
            select(
                Assignment.process_teacher_id, HourRequirement.required_teacher_hours
            )
            .where(Assignment.assignment_process_id == process_id)
            .where(Assignment.status == AssignmentStatus.ACTIVE)
            .where(Assignment.hour_requirement_id == HourRequirement.id)
        ).all()
        for teacher_id, hours in assignment_rows:
            assigned_by_teacher[teacher_id] = quantize_hours(
                assigned_by_teacher.get(teacher_id, _ZERO) + _dec(hours)
            )
            count_by_teacher[teacher_id] = count_by_teacher.get(teacher_id, 0) + 1

        participants: list[ParticipantBalance] = []
        total_target = _ZERO
        for process_teacher, profile in teacher_rows:
            assigned = assigned_by_teacher.get(process_teacher.id, _ZERO)
            target = _dec(process_teacher.target_weekly_hours)
            remaining = quantize_hours(target - assigned)
            if process_teacher.status == ProcessTeacherStatus.ACTIVE:
                total_target += target
            participants.append(
                ParticipantBalance(
                    process_teacher_id=process_teacher.id,
                    teacher_profile_id=profile.id,
                    display_name=profile.display_name,
                    base_weekly_hours=_dec(process_teacher.base_weekly_hours),
                    extra_weekly_hours=_dec(process_teacher.extra_weekly_hours),
                    target_weekly_hours=target,
                    assigned_weekly_hours=assigned,
                    remaining_weekly_hours=remaining,
                    is_overloaded=_dec(process_teacher.extra_weekly_hours) > _ZERO,
                    assignment_count=count_by_teacher.get(process_teacher.id, 0),
                    state=AssignmentCalculationService.compute_participant_state(
                        process_teacher, remaining
                    ),
                )
            )
        participants.sort(
            key=lambda p: (p.display_name.casefold(), str(p.process_teacher_id))
        )

        total_target = quantize_hours(total_target)
        total_assigned = quantize_hours(sum(assigned_by_teacher.values(), _ZERO))

        # Live requirement slots and how many are occupied.
        live_requirements = list(
            session.exec(
                select(HourRequirement.id)
                .where(HourRequirement.assignment_process_id == process_id)
                .where(col(HourRequirement.retired_generation).is_(None))
            ).all()
        )
        occupied_ids = set(
            session.exec(
                select(Assignment.hour_requirement_id)
                .where(Assignment.assignment_process_id == process_id)
                .where(Assignment.status == AssignmentStatus.ACTIVE)
            ).all()
        )
        total_slots = len(live_requirements)
        assigned_slots = sum(1 for rid in live_requirements if rid in occupied_ids)

        return AssignmentSummary(
            assignment_process_id=process_id,
            total_target_hours=total_target,
            total_assigned_hours=total_assigned,
            total_remaining_hours=quantize_hours(total_target - total_assigned),
            total_slots=total_slots,
            assigned_slots=assigned_slots,
            available_slots=total_slots - assigned_slots,
            participants=participants,
        )


__all__ = [
    "AssignmentCalculationService",
    "PlanningCalculationService",
]
