"""Read schemas for the process summary, dashboard and teacher LAN endpoints.

The three-stage adaptation replaces the obsolete single-global-balance payloads
of ``reparto_service.schemas.summary`` (deleted with the ``SummaryService`` that
produced them). That model reported one aggregate ``required vs assigned`` axis
plus per-requirement *partial coverage* and per-teacher *override* flags — three
concepts that no longer exist (plan §3.6, §5.10): a slot is indivisible, an
assignment carries no hours of its own, and an over-target assignment cannot be
overridden, only authorized in advance as extra hours (plan §3.8).

What replaces it is the plan §3.1 split into two sections, each reading the
service that owns it:

* the **planning** section — both independent balances (group teaching hours vs
  the leadership allocation; teacher load vs the participant target total) and
  the planning validations;
* the **assignment** section — the per-participant slot occupancy view and the
  assignment validations.

The two sections are reported side by side and are never summed into one number:
plan §3.2's co-teaching example is 120 group hours / 124 teacher-load hours and
both are correct. Every hour figure is a canonical two-place decimal string in
JSON (plan §3.9), inherited from the :mod:`reparto_service.schemas.planning`
fields these schemas embed.

All values are derived at request time from the latest database state; nothing is
cached. Every composition here is solver-free (plan §20.23) — the stored
feasibility status is reported, never evaluated.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from reparto_service.enums import (
    PlanReadiness,
    SelectionTurnStatus,
    TeachingPlanStatus,
)
from reparto_service.schemas.planning import (
    AssignmentSummary,
    AssignmentValidationReport,
    ParticipantBalance,
    PlanBalance,
    PlanValidationReport,
)


# ── Current turn ─────────────────────────────────────────────────────────────


class CurrentTurnSummary(BaseModel):
    """Current active turn exposed to dashboard, summary and LAN clients."""

    model_config = ConfigDict(from_attributes=True)

    meeting_session_id: uuid.UUID = Field(description="Meeting session ID.")
    selection_turn_id: uuid.UUID = Field(description="Active selection turn ID.")
    process_teacher_id: uuid.UUID = Field(description="Teacher whose turn is active.")
    position: int = Field(ge=0, description="Turn order position.")
    status: SelectionTurnStatus = Field(description="Turn status.")
    started_at: Optional[datetime] = Field(default=None)


# ── Stage sections ───────────────────────────────────────────────────────────


class PlanningSection(BaseModel):
    """Planning-stage view of a process (plan §3.1, §6.1, §6.3).

    ``teaching_plan_id``/``status``/``balance``/``validations`` are all ``None``
    when the process has no teaching plan yet: the planning stage has not been
    entered, which is a legitimate state for a process still in setup, not an
    error. A client renders the section as "not started" rather than 404ing the
    whole dashboard.
    """

    teaching_plan_id: Optional[uuid.UUID] = Field(
        default=None, description="Teaching plan ID; NULL when no plan exists yet."
    )
    status: Optional[TeachingPlanStatus] = Field(
        default=None, description="Plan lifecycle status; NULL when no plan exists."
    )
    balance: Optional[PlanBalance] = Field(
        default=None,
        description="Both independent balances (plan §3.1); NULL when no plan.",
    )
    validations: Optional[PlanValidationReport] = Field(
        default=None,
        description="Planning blocking/warning findings; NULL when no plan.",
    )


class AssignmentSection(BaseModel):
    """Assignment-stage view of a process (plan §3.6, §6.2, §6.3).

    Always present: the participant rows and slot counts are meaningful even
    before requirements are generated (every count is simply zero).
    """

    summary: AssignmentSummary = Field(
        description="Per-participant slot occupancy and live-slot counts."
    )
    validations: AssignmentValidationReport = Field(
        description="Assignment blocking/warning findings."
    )


# ── Process payloads ─────────────────────────────────────────────────────────


class ProcessDashboard(BaseModel):
    """Full dashboard payload for one process (department-head view).

    One round trip after a mutation: both stages, their validations and the
    current turn. ``blocking_validation_count`` sums the blocking findings of
    both sections, so a head can tell at a glance whether anything blocks
    without walking either message list.
    """

    process_id: uuid.UUID = Field(description="Assignment process ID.")
    generated_at: datetime = Field(
        description="When this dashboard payload was computed (UTC)."
    )
    readiness: PlanReadiness = Field(
        description="Coarse plan readiness, derived from the lifecycle gates."
    )
    planning: PlanningSection = Field(description="Planning-stage section.")
    assignment: AssignmentSection = Field(description="Assignment-stage section.")
    current_turn: Optional[CurrentTurnSummary] = Field(default=None)
    blocking_validation_count: int = Field(
        ge=0, description="Blocking findings across both sections."
    )


class ProcessSummary(BaseModel):
    """Lightweight summary returned by ``GET /assignment-processes/{id}/summary``.

    The dashboard without the message lists and the per-participant rows: the
    balances, the counts and the turn. Suited to a header or a poll.
    """

    model_config = ConfigDict(from_attributes=True)

    process_id: uuid.UUID = Field(description="Assignment process ID.")
    generated_at: datetime = Field(
        description="When this summary payload was computed (UTC)."
    )
    readiness: PlanReadiness = Field(
        description="Coarse plan readiness, derived from the lifecycle gates."
    )
    plan_status: Optional[TeachingPlanStatus] = Field(
        default=None, description="Plan lifecycle status; NULL when no plan exists."
    )
    plan_balance: Optional[PlanBalance] = Field(
        default=None,
        description="Both independent balances (plan §3.1); NULL when no plan.",
    )
    total_slots: int = Field(ge=0, description="Live requirement slots.")
    assigned_slots: int = Field(ge=0, description="Live slots with an active teacher.")
    available_slots: int = Field(ge=0, description="Live slots still unassigned.")
    current_turn: Optional[CurrentTurnSummary] = Field(default=None)
    blocking_validation_count: int = Field(
        ge=0, description="Blocking findings across both stages."
    )


class TeacherLanSummary(BaseModel):
    """LAN read payload for the authenticated teacher (plan §8.6, §20.25).

    Carries the caller's **own** participation only. The process-wide
    ``plan_balance`` is aggregate — it names no teacher — and the shared screen
    shows the same two figures (plan §8.7), so it is LAN-safe; another
    participant's target, assigned or remaining hours never appear here, exactly
    as the SSE teacher tier redacts them (plan §20.25).

    ``selection_blocked`` and ``readiness`` come from the same lifecycle-gate
    status sets the write path consults, so what a teacher is *shown* can never
    disagree with what the gates let them *do*.
    """

    process_id: uuid.UUID = Field(description="Assignment process ID.")
    teacher_profile_id: uuid.UUID = Field(description="Linked teacher profile ID.")
    process_teacher_id: uuid.UUID = Field(description="Process teacher ID.")
    generated_at: datetime = Field(
        description="When this LAN payload was computed (UTC)."
    )
    readiness: PlanReadiness = Field(
        description="Coarse plan readiness: ready / not ready / recalculation."
    )
    selection_blocked: bool = Field(
        description="True when an allocation change blocks assignment writes."
    )
    plan_balance: Optional[PlanBalance] = Field(
        default=None,
        description="Aggregate, identifier-free balances; NULL when no plan.",
    )
    participant: ParticipantBalance = Field(
        description="Only the caller's own row: base, extra, target, assigned, remaining."
    )
    available_slots: int = Field(
        ge=0, description="Live slots still unassigned in the process."
    )
    current_turn: Optional[CurrentTurnSummary] = Field(default=None)


__all__ = [
    "AssignmentSection",
    "CurrentTurnSummary",
    "PlanningSection",
    "ProcessDashboard",
    "ProcessSummary",
    "TeacherLanSummary",
]
