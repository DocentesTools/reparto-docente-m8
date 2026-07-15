"""ProcessTeacher table model and request/response schemas.

ProcessTeacher binds a teacher profile to one assignment process and
carries the per-process data: the participant target hours (base plus
authorized extra), selection-order position and flags. The model is the
join object between the auth-side identity (``TeacherProfile`` / auth
user) and the process-side data.

The participant target (plan §3.8 / §5.8) is the exact number of weekly
hours a participant must reach before final close::

    target_weekly_hours = base_weekly_hours + extra_weekly_hours

``extra_weekly_hours`` is department-head authorized overload. Every
change to it requires a reason and an audit event, so it is mutated only
through the dedicated ``/extra-hours`` action (plan §7.6), never through
the generic ``PATCH`` body.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field, computed_field
from sqlalchemy import UniqueConstraint
from sqlmodel import Column, Field as SQLField, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables
from reparto_service.enums import ProcessTeacherStatus


# ── Base, Create, Update schemas ──────────────────────────────────────────────


class ProcessTeacherBase(SQLModel):
    """Shared fields for process teacher schemas."""

    assignment_process_id: uuid.UUID = Field(
        description="Owning assignment process ID."
    )
    teacher_profile_id: uuid.UUID = Field(description="Linked teacher profile ID.")
    base_weekly_hours: float = Field(
        default=0,
        ge=0,
        description=(
            "Contractual weekly teaching hours for this process — the product "
            "of the weekly schedule and the contract percentage. Part of the "
            "participant target (plan §3.8)."
        ),
    )
    extra_weekly_hours: float = Field(
        default=0,
        ge=0,
        description=(
            "Department-head authorized extra weekly hours (overload). A value "
            "greater than zero flags the participant as overloaded. Changed "
            "only through the audited /extra-hours action (plan §3.8/§7.6)."
        ),
    )
    participates_in_selection: bool = Field(
        default=True,
        description=(
            "Whether the teacher is included in the configured selection order."
        ),
    )
    selection_position: Optional[int] = Field(
        default=None,
        ge=0,
        description="Position in the selection order, when one is configured.",
    )
    selection_points: Optional[float] = Field(
        default=None,
        ge=0,
        description=(
            "Informational points shown next to the teacher (seniority, etc.). "
            "The MVP does not interpret the points value."
        ),
    )
    selection_criteria_label: Optional[str] = Field(
        default=None,
        max_length=150,
        description="Free-form label describing the selection criterion.",
    )
    selection_notes: Optional[str] = Field(
        default=None, description="Free-form notes about the selection entry."
    )
    order_locked: bool = Field(
        default=False,
        description="Whether the selection order is locked for this teacher.",
    )
    status: ProcessTeacherStatus = Field(
        default=ProcessTeacherStatus.ACTIVE,
        description="Whether the teacher is active in the process.",
    )


class ProcessTeacherCreate(ProcessTeacherBase):
    """Schema for creating a new process teacher record."""


class ProcessTeacherUpdate(SQLModel):
    """Partial update schema — every field is optional.

    ``extra_weekly_hours`` is deliberately absent: changing authorized
    overload requires a reason and an audit event, so it is mutated only
    through the dedicated ``/extra-hours`` action (plan §7.6). This keeps a
    generic ``PATCH`` from bypassing the audit requirement.
    """

    base_weekly_hours: Optional[float] = Field(default=None, ge=0)
    participates_in_selection: Optional[bool] = Field(default=None)
    selection_position: Optional[int] = Field(default=None, ge=0)
    selection_points: Optional[float] = Field(default=None, ge=0)
    selection_criteria_label: Optional[str] = Field(default=None, max_length=150)
    selection_notes: Optional[str] = Field(default=None)
    order_locked: Optional[bool] = Field(default=None)
    status: Optional[ProcessTeacherStatus] = Field(default=None)


class ProcessTeacherExtraHoursUpdate(SQLModel):
    """Payload for the dedicated audited extra-hours action (plan §7.6)."""

    extra_weekly_hours: float = Field(
        ge=0, description="New authorized extra weekly hours (non-negative)."
    )
    reason: str = Field(
        min_length=1,
        max_length=500,
        description="Mandatory justification for the extra-hours change.",
    )


# ── Database model ───────────────────────────────────────────────────────────


class ProcessTeacher(TimestampMixin, ProcessTeacherBase, SQLModel, table=True):
    """SQLModel table for a process teacher."""

    __tablename__ = prefixed_tables("process_teacher")
    __table_args__ = (
        UniqueConstraint(
            "assignment_process_id",
            "teacher_profile_id",
            name="uq_reparto_process_teacher_process_profile",
        ),
    )

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Process teacher ID.",
    )
    assignment_process_id: uuid.UUID = SQLField(
        sa_column=Column(
            "assignment_process_id", UUIDString(), nullable=False, index=True
        ),
        description="Owning assignment process ID.",
    )
    teacher_profile_id: uuid.UUID = SQLField(
        sa_column=Column(
            "teacher_profile_id", UUIDString(), nullable=False, index=True
        ),
        description="Linked teacher profile ID.",
    )
    extra_hours_reason: Optional[str] = SQLField(
        default=None,
        description="Reason recorded for the last authorized extra-hours change.",
    )
    extra_hours_updated_by_user_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column("extra_hours_updated_by_user_id", UUIDString(), nullable=True),
        description="User who last changed the authorized extra hours.",
    )
    extra_hours_updated_at: Optional[datetime] = SQLField(
        default=None,
        description="Timestamp of the last authorized extra-hours change (UTC).",
    )

    @property
    def target_weekly_hours(self) -> float:
        """Exact participant target: base plus authorized extra (plan §3.8).

        Used by the balance/summary service; ``is_overloaded`` is exposed on
        the public schema only, where it is actually serialized.
        """
        return self.base_weekly_hours + self.extra_weekly_hours


# ── Public/read schemas ──────────────────────────────────────────────────────


class ProcessTeacherPublic(ProcessTeacherBase, SQLModel):
    """Public representation of a process teacher."""

    id: uuid.UUID = Field(description="Process teacher ID.")
    extra_hours_reason: Optional[str] = Field(default=None)
    extra_hours_updated_by_user_id: Optional[uuid.UUID] = Field(default=None)
    extra_hours_updated_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def target_weekly_hours(self) -> float:
        """base_weekly_hours + extra_weekly_hours (plan §3.8)."""
        return self.base_weekly_hours + self.extra_weekly_hours

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_overloaded(self) -> bool:
        """True when extra_weekly_hours > 0 (plan §3.8 authorized overload)."""
        return self.extra_weekly_hours > 0


class ProcessTeachersPublic(SQLModel):
    """List wrapper for public process teachers."""

    data: list[ProcessTeacherPublic] = Field(description="List of process teachers.")
    count: int = Field(description="Total process teachers count.")
