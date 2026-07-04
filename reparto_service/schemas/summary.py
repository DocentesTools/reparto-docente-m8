"""Schemas for process summary, balance and dashboard endpoints.

The summary service is the heart of the product — every dashboard panel
reads from the structures defined here (plan 9, 11.3). All values are
derived at request time from the latest database state; the API does not
cache anything. The frontend can poll the dashboard endpoint on each
mutation to keep the panels in sync.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from reparto_service.enums import (
    GlobalBalanceState,
    RequirementBalanceState,
    SelectionTurnStatus,
    TeacherBalanceState,
    ValidationSeverity,
)


# ── Balance records ──────────────────────────────────────────────────────────


class TeacherBalance(BaseModel):
    """Per-process-teacher balance (plan 9.2)."""

    model_config = ConfigDict(from_attributes=True)

    process_teacher_id: uuid.UUID = Field(description="Process teacher ID.")
    teacher_profile_id: uuid.UUID = Field(description="Linked teacher profile ID.")
    display_name: str = Field(description="Teacher display name.")
    available_hours: float = Field(
        ge=0, description="Total hours the teacher is available for."
    )
    assigned_hours: float = Field(
        ge=0, description="Total hours already assigned to the teacher."
    )
    remaining_hours: float = Field(
        description=("available_hours - assigned_hours. Negative means overloaded.")
    )
    excess_hours: float = Field(
        ge=0, description="max(0, assigned_hours - available_hours)."
    )
    assignment_count: int = Field(
        ge=0, description="Number of active assignments for this teacher."
    )
    has_override: bool = Field(
        description=(
            "True if at least one of the teacher's assignments carries a "
            "department head override that allows exceeding available hours."
        )
    )
    state: TeacherBalanceState = Field(description="Per-teacher balance state.")


class RequirementBalance(BaseModel):
    """Per-requirement balance (plan 9.3)."""

    model_config = ConfigDict(from_attributes=True)

    hour_requirement_id: uuid.UUID = Field(description="Hour requirement ID.")
    teaching_group_id: uuid.UUID = Field(description="Target teaching group ID.")
    teaching_group_label: str = Field(
        description="Human-readable teaching group label."
    )
    subject_id: uuid.UUID = Field(description="Target subject ID.")
    subject_name: str = Field(description="Subject display name.")
    required_hours: float = Field(ge=0, description="Hours required by leadership.")
    assigned_hours: float = Field(
        ge=0, description="Total hours already assigned to the requirement."
    )
    pending_hours: float = Field(
        description="required_hours - assigned_hours (clamped at 0)."
    )
    assignment_count: int = Field(
        ge=0, description="Number of active assignments for this requirement."
    )
    has_override: bool = Field(
        description=(
            "True if at least one assignment on this requirement carries a "
            "department head override that allows exceeding required hours."
        )
    )
    state: RequirementBalanceState = Field(description="Per-requirement balance state.")


class GlobalBalance(BaseModel):
    """Aggregate balance for one assignment process (plan 9.1)."""

    model_config = ConfigDict(from_attributes=True)

    total_required_hours: float = Field(
        ge=0, description="Sum of required hours for every requirement."
    )
    total_available_hours: float = Field(
        ge=0, description="Sum of available hours for every active process teacher."
    )
    total_assigned_hours: float = Field(
        ge=0,
        description=(
            "Sum of assigned hours for every non-cancelled assignment in the process."
        ),
    )
    pending_required_hours: float = Field(
        description=(
            "total_required_hours - total_assigned_hours. Negative when over-assigned."
        )
    )
    availability_difference: float = Field(
        description=(
            "total_available_hours - total_required_hours. Positive when the "
            "department has more capacity than required."
        )
    )
    uncovered_requirements: int = Field(
        ge=0, description="Number of requirements with zero hours assigned."
    )
    overloaded_teachers: int = Field(
        ge=0, description="Number of process teachers whose total exceeds available."
    )
    state: GlobalBalanceState = Field(description="Aggregate balance state.")


# ── Validation messages ──────────────────────────────────────────────────────


class ValidationMessage(BaseModel):
    """Single validation finding (plan 9.4)."""

    model_config = ConfigDict(from_attributes=True)

    severity: ValidationSeverity = Field(description="Severity bucket.")
    code: str = Field(
        max_length=80,
        description="Stable identifier (e.g. 'requirement.over_assigned').",
    )
    message: str = Field(description="Human-readable description.")
    entity_type: str = Field(
        max_length=50,
        description=(
            "Logical entity the message refers to "
            "(e.g. 'requirement', 'teacher', 'process')."
        ),
    )
    entity_id: Optional[uuid.UUID] = Field(
        default=None, description="ID of the related entity, when applicable."
    )


# ── Dashboard payload ────────────────────────────────────────────────────────


class CurrentTurnSummary(BaseModel):
    """Current active turn exposed to dashboard and summary clients."""

    model_config = ConfigDict(from_attributes=True)

    meeting_session_id: uuid.UUID = Field(description="Meeting session ID.")
    selection_turn_id: uuid.UUID = Field(description="Active selection turn ID.")
    process_teacher_id: uuid.UUID = Field(description="Teacher whose turn is active.")
    position: int = Field(ge=0, description="Turn order position.")
    status: SelectionTurnStatus = Field(description="Turn status.")
    started_at: Optional[datetime] = Field(default=None)


class ProcessDashboard(BaseModel):
    """Full dashboard payload for one assignment process.

    Combines the global, per-teacher and per-requirement balances with
    the validation findings so the department head can refresh the page
    in one round trip after a mutation.
    """

    process_id: uuid.UUID = Field(description="Assignment process ID.")
    generated_at: datetime = Field(
        description="When this dashboard payload was computed (UTC)."
    )
    global_balance: GlobalBalance = Field(description="Aggregate balance.")
    teacher_balances: list[TeacherBalance] = Field(
        description="Per-teacher balance rows."
    )
    requirement_balances: list[RequirementBalance] = Field(
        description="Per-requirement balance rows."
    )
    validations: list[ValidationMessage] = Field(
        description="Validation findings (plan 9.4)."
    )
    current_turn: Optional[CurrentTurnSummary] = Field(default=None)
    blocking_validation_count: int = Field(
        ge=0, description="Convenience: count of blocking validations."
    )


# ── Summary response (lighter than full dashboard) ───────────────────────────


class ProcessSummary(BaseModel):
    """Lightweight summary returned by ``GET /assignment-processes/{id}/summary``."""

    model_config = ConfigDict(from_attributes=True)

    process_id: uuid.UUID = Field(description="Assignment process ID.")
    global_balance: GlobalBalance = Field(description="Aggregate balance.")
    validations: list[ValidationMessage] = Field(description="Validation findings.")
    current_turn: Optional[CurrentTurnSummary] = Field(default=None)
    blocking_validation_count: int = Field(
        ge=0, description="Convenience: count of blocking validations."
    )


class TeacherLanSummary(BaseModel):
    """LAN-safe read payload for the authenticated teacher."""

    process_id: uuid.UUID = Field(description="Assignment process ID.")
    teacher_profile_id: uuid.UUID = Field(description="Linked teacher profile ID.")
    process_teacher_id: uuid.UUID = Field(description="Process teacher ID.")
    generated_at: datetime = Field(
        description="When this LAN payload was computed (UTC)."
    )
    global_balance: GlobalBalance = Field(description="Non-sensitive aggregate.")
    teacher_balance: TeacherBalance = Field(description="Only the caller's row.")
    current_turn: Optional[CurrentTurnSummary] = Field(default=None)
    blocking_validation_count: int = Field(
        ge=0, description="Convenience: count of blocking validations."
    )
