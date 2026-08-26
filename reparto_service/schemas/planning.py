"""Schemas for the dual planning and assignment calculations (plan §6.1, §6.2).

The three-stage adaptation replaces the old single global balance with **two
independent balances** (plan §3.1):

* the *group teaching-hour balance* — the weekly teaching every linked group
  receives, whose target is the current school-leadership allocation;
* the *teacher workload balance* — the load generated for teachers, whose target
  is the sum of participant ``base + extra`` targets.

The two totals are related but are NOT required to be numerically equal
(plan §3.2: a co-teaching plan is 120 group hours / 124 teacher-load hours and
both are correct). These schemas carry each balance on its own axis, plus the
per-participant assignment view.

All hour figures are exchanged as canonical two-place decimal strings in JSON
(plan §3.9). A field typed :data:`HoursField` keeps a real :class:`~decimal.Decimal`
in Python mode (for arithmetic and comparison) and serialises ``"2.50"`` — or a
signed ``"-4.00"`` for a difference — in JSON mode. Feasibility (the third
assignment-readiness invariant, plan §20.1) is intentionally absent here; it is
owned by the dedicated feasibility services (plan §20.20).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

from reparto_service.core.decimals import quantize_hours
from reparto_service.enums import (
    HourRequirementStatus,
    ParticipantBalanceState,
    ValidationSeverity,
)


# ── Canonical decimal-hour output field ───────────────────────────────────────
#
# The model hour columns are still ``float`` today (the ``Decimal`` column sweep
# is a dedicated later task, plan §3.9), so the calculation service converts each
# value into a two-place ``Decimal`` before it reaches a schema. ``_coerce_hours``
# is a lenient normaliser for *output*: it quantizes any numeric input (including
# a signed difference) to two places without rejecting it — validation of request
# input lives at the request boundary (``core.decimals.HoursDecimal``), not here.


def _coerce_hours(value: Decimal | int | float | str) -> Decimal:
    """Quantize any numeric value to a canonical two-place ``Decimal``."""
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    return quantize_hours(decimal_value)


def _serialize_hours(value: Decimal) -> str:
    """Render a computed hour value as its canonical decimal string."""
    return str(quantize_hours(value))


HoursField = Annotated[
    Decimal,
    BeforeValidator(_coerce_hours),
    PlainSerializer(_serialize_hours, return_type=str, when_used="json"),
]
"""Computed decimal-hour output: canonical ``"2.50"`` / signed ``"-4.00"`` in JSON."""

OptionalHoursField = Annotated[
    Optional[Decimal],
    BeforeValidator(lambda v: None if v is None else _coerce_hours(v)),
    PlainSerializer(
        lambda v: None if v is None else _serialize_hours(v),
        return_type=Optional[str],
        when_used="json",
    ),
]
"""Computed decimal-hour output that may be absent (e.g. no current allocation)."""


# ── Planning balances (plan §6.1) ─────────────────────────────────────────────


class GroupBalance(BaseModel):
    """Group teaching-hour balance vs the current allocation (plan §3.1).

    ``allocated_group_weekly_hours`` / ``allocation_difference`` are ``None``
    when the process has no current allocation revision yet (plan §6.3 flags
    that as a blocking validation in a later task); the balance can never be
    ``is_balanced`` without a target.
    """

    total_group_load: HoursField = Field(
        description="SUM(group_weekly_hours_per_group × linked_group_count)."
    )
    allocated_group_weekly_hours: OptionalHoursField = Field(
        default=None,
        description="Current school-leadership allocation; NULL when unset.",
    )
    allocation_difference: OptionalHoursField = Field(
        default=None,
        description="total_group_load − allocation; NULL when no allocation.",
    )
    is_balanced: bool = Field(
        description="True only when an allocation exists and the difference is zero."
    )


class TeacherLoadBalance(BaseModel):
    """Teacher workload balance vs the participant target total (plan §3.1)."""

    total_teacher_load: HoursField = Field(
        description="SUM(teacher_weekly_hours_per_position × required_teacher_count)."
    )
    participant_target_total: HoursField = Field(
        description="SUM(base_weekly_hours + extra_weekly_hours) over active participants."
    )
    teacher_load_difference: HoursField = Field(
        description="total_teacher_load − participant_target_total (signed)."
    )
    is_balanced: bool = Field(description="True when the difference is zero.")


class PlanBalance(BaseModel):
    """Both independent balances for one teaching plan (plan §3.1, §3.10).

    ``is_exact`` mirrors the plan §3.10 numeric exactness (both balances equal
    their targets). It is NOT assignment-readiness: the third invariant,
    feasibility, is evaluated separately (plan §20.1).
    """

    teaching_plan_id: uuid.UUID = Field(description="Teaching plan ID.")
    assignment_process_id: uuid.UUID = Field(description="Owning process ID.")
    group: GroupBalance = Field(description="Group teaching-hour balance.")
    teacher: TeacherLoadBalance = Field(description="Teacher workload balance.")
    is_exact: bool = Field(
        description="True when both balances equal their targets (plan §3.10)."
    )


# ── Assignment calculations (plan §6.2) ───────────────────────────────────────


class ParticipantBalance(BaseModel):
    """Per-participant assignment view (plan §6.2).

    ``state`` is a single classification with precedence INACTIVE →
    NOT_PARTICIPATING → OVERLOADED_AUTHORIZED → PENDING/BALANCED.
    ``OVERLOADED_AUTHORIZED`` identifies ``extra_weekly_hours > 0`` and does NOT
    mean assigned hours exceed the target (plan §6.2); the numeric fields carry
    the assignment progress independently.
    """

    model_config = ConfigDict(from_attributes=True)

    process_teacher_id: uuid.UUID = Field(description="Process teacher ID.")
    teacher_profile_id: uuid.UUID = Field(description="Linked teacher profile ID.")
    display_name: str = Field(description="Teacher display name.")
    base_weekly_hours: HoursField = Field(description="Contractual base hours.")
    extra_weekly_hours: HoursField = Field(description="Authorized extra hours.")
    target_weekly_hours: HoursField = Field(description="base + extra (plan §3.8).")
    assigned_weekly_hours: HoursField = Field(
        description="SUM of the hours of the participant's active slots."
    )
    remaining_weekly_hours: HoursField = Field(
        description="target − assigned (signed; negative would mean over target)."
    )
    is_overloaded: bool = Field(description="extra_weekly_hours > 0 (plan §3.8).")
    assignment_count: int = Field(
        ge=0, description="Number of active assignments for this participant."
    )
    state: ParticipantBalanceState = Field(description="Per-participant state.")


class AssignmentSummary(BaseModel):
    """Aggregate assignment view for one process (plan §6.2).

    Totals are computed over active participants and their active slot
    occupancies; every process teacher is listed (so INACTIVE /
    NOT_PARTICIPATING rows stay visible) but only active participants feed the
    target/assigned/remaining totals.
    """

    assignment_process_id: uuid.UUID = Field(description="Owning process ID.")
    total_target_hours: HoursField = Field(
        description="Participant target total over active participants."
    )
    total_assigned_hours: HoursField = Field(
        description="Total hours of every active slot occupancy in the process."
    )
    total_remaining_hours: HoursField = Field(
        description="total_target − total_assigned (signed)."
    )
    total_slots: int = Field(ge=0, description="Live requirement slots in the process.")
    assigned_slots: int = Field(
        ge=0, description="Live slots with an active assignment."
    )
    available_slots: int = Field(ge=0, description="Live slots still unassigned.")
    participants: list[ParticipantBalance] = Field(
        description="Per-participant balances, ordered by display name."
    )


# ── Planning validations (plan §6.3, §6.4) ────────────────────────────────────


class PlanValidationMessage(BaseModel):
    """One planning **or** assignment blocking/warning finding (plan §6.3, §6.4).

    Shared by the planning-stage :class:`PlanValidationReport` and the
    assignment-stage :class:`AssignmentValidationReport`. ``code`` is a stable
    machine identifier (the frontend keys off it and never off the human
    ``message``); the ``CODE_*`` constants in
    :mod:`reparto_service.services.validations` are the single source of truth.
    ``entity_type``/``entity_id`` point at the concrete row a finding is about
    (a ``group_subject`` cell, a ``teaching_activity``, a ``teacher``,
    a requirement slot) or at the whole ``plan``/``assignment_process`` when the
    finding is process-wide.
    """

    severity: ValidationSeverity = Field(
        description="Severity bucket (plan §6.3/§6.4)."
    )
    code: str = Field(
        max_length=80,
        description="Stable identifier (e.g. 'plan.group_hours_imbalanced').",
    )
    message: str = Field(description="Human-readable description.")
    entity_type: str = Field(
        max_length=50,
        description="Entity the finding refers to ('plan', 'group_subject', …).",
    )
    entity_id: Optional[uuid.UUID] = Field(
        default=None, description="ID of the related entity, when applicable."
    )


class PlanValidationReport(BaseModel):
    """Aggregate planning-validation result for one teaching plan (plan §6.3/§6.4).

    ``is_assignment_ready`` is ``True`` only when there is **no** blocking
    finding; it mirrors the plan §3.10 gate for starting the assignment stage
    (the numeric balances plus the cheap structural checks). The exponential
    feasibility solver is deliberately NOT run here (plan §20.19/§20.23): this
    report reads the stored ``feasibility_status`` but never triggers a solve.
    """

    teaching_plan_id: uuid.UUID = Field(description="Teaching plan ID.")
    assignment_process_id: uuid.UUID = Field(description="Owning process ID.")
    is_assignment_ready: bool = Field(
        description="True when no blocking finding is present (plan §3.10)."
    )
    blocking_count: int = Field(ge=0, description="Number of blocking findings.")
    warning_count: int = Field(ge=0, description="Number of warning findings.")
    messages: list[PlanValidationMessage] = Field(
        description="Blocking findings first, then warnings; deterministic order."
    )


class AssignmentValidationReport(BaseModel):
    """Aggregate assignment-stage validation result for one process (plan §6.3/§6.4).

    The assignment-stage twin of :class:`PlanValidationReport`. It reuses
    :class:`PlanValidationMessage` and reports the §6.3 assignment blockers —
    unassigned indivisible slots, participants over their exact target (an
    overload that bypassed the extra-hours flow), and participants still below
    target — plus the §6.4 authorized-overload warning.

    ``is_final_ready`` is ``True`` only when there is **no** blocking finding; it
    mirrors the plan §3.10 gate for final closure / final assignment export
    (every live slot assigned and every active participant sitting exactly on
    their target). Like the planning report, it is cheap and solver-free
    (plan §20.23): it never triggers a feasibility evaluation.
    """

    assignment_process_id: uuid.UUID = Field(description="Owning process ID.")
    is_final_ready: bool = Field(
        description="True when no blocking finding is present (plan §3.10)."
    )
    blocking_count: int = Field(ge=0, description="Number of blocking findings.")
    warning_count: int = Field(ge=0, description="Number of warning findings.")
    messages: list[PlanValidationMessage] = Field(
        description="Blocking findings first, then warnings; deterministic order."
    )


__all__ = [
    "AssignmentSummary",
    "AssignmentValidationReport",
    "GroupBalance",
    "HoursField",
    "OptionalHoursField",
    "ParticipantBalance",
    "PlanBalance",
    "PlanValidationMessage",
    "PlanValidationReport",
    "TeacherLoadBalance",
    # re-exported for convenience of callers that switch on the derived state
    "HourRequirementStatus",
    "ParticipantBalanceState",
    "ValidationSeverity",
]
