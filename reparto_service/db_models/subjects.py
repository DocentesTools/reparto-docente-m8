"""Subject table model and request/response schemas.

A subject is owned by one assignment process — it represents a subject
managed by the department for that year, e.g. 'Mathematics', 'Mathematics I'
or 'Reinforcement Mathematics'.

Since the three-stage adaptation (plan §5.3) a subject also declares its
allocation category (``MAIN``/``SECONDARY``, an extensible enum rather than a
boolean ``is_main`` — plan §3.5), a descriptive ``activity_type`` (labels/
filters/defaults only, never a behaviour switch — plan §20.17) and a set of
*suggested* planning defaults. The defaults are suggestions, not immutable
values: the actual per-group hours live on ``GroupSubject`` and the actual
planning values on ``TeachingActivity`` (plan §5.3, §5.5, §5.6). Editing a
subject default here NEVER retroactively rewrites an already-materialised
``GroupSubject`` or ``TeachingActivity`` — defaults only seed future rows unless
an explicit sync action runs (plan §20.14).

Hour defaults are stored as ``float`` like every other hour field in the service
today; the fleet-wide switch to ``Decimal`` / ``NUMERIC(..., 2)`` and canonical
decimal-string serialisation is its own §3.9 task.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field
from sqlalchemy import UniqueConstraint
from sqlmodel import Column, Field as SQLField, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables
from reparto_service.enums import ActivityType, SubjectAllocationCategory


# ── Base, Create, Update schemas ──────────────────────────────────────────────


class SubjectBase(SQLModel):
    """Shared fields for subject schemas."""

    assignment_process_id: uuid.UUID = Field(
        description="Owning assignment process ID."
    )
    name: str = Field(
        min_length=1,
        max_length=150,
        description="Subject name, e.g. 'Mathematics'.",
    )
    allocation_category: SubjectAllocationCategory = Field(
        default=SubjectAllocationCategory.MAIN,
        description=(
            "Whether the subject is a mandatory MAIN planning input or an "
            "optional SECONDARY one (plan §3.5). Never a boolean ``is_main``."
        ),
    )
    activity_type: ActivityType = Field(
        default=ActivityType.ORDINARY,
        description=(
            "Descriptive activity category (plan §5.3). Controls only labels, "
            "filters, defaults, reports and analytics — no behaviour may branch "
            "on it (plan §20.17)."
        ),
    )
    default_group_weekly_hours: Optional[float] = Field(
        default=None,
        ge=0,
        description=(
            "Suggested weekly group hours seeded onto a new GroupSubject/"
            "activity. A suggestion, not an immutable value (plan §5.3)."
        ),
    )
    default_teacher_weekly_hours_per_position: Optional[float] = Field(
        default=None,
        ge=0,
        description=(
            "Suggested weekly teacher-load hours per teacher position. A "
            "suggestion, not an immutable value (plan §5.3)."
        ),
    )
    default_required_teacher_count: int = Field(
        default=1,
        ge=1,
        description=(
            "Suggested number of teacher positions the activity requires "
            "(plan §5.3, always >= 1). Co-teaching subjects should normally "
            "default to at least two."
        ),
    )
    allows_multiple_groups: bool = Field(
        default=False,
        description=(
            "Whether activities of this subject may link more than one group "
            "(plan §5.3, §5.6)."
        ),
    )
    allows_zero_groups: bool = Field(
        default=False,
        description=(
            "Whether activities of this subject may link zero groups "
            "(department-level activities — plan §5.3, §5.6)."
        ),
    )
    notes: Optional[str] = Field(default=None, description="Free-form notes.")


class SubjectCreate(SubjectBase):
    """Schema for creating a new subject."""


class SubjectUpdate(SQLModel):
    """Partial update schema — every field is optional."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    allocation_category: Optional[SubjectAllocationCategory] = Field(default=None)
    activity_type: Optional[ActivityType] = Field(default=None)
    default_group_weekly_hours: Optional[float] = Field(default=None, ge=0)
    default_teacher_weekly_hours_per_position: Optional[float] = Field(
        default=None, ge=0
    )
    default_required_teacher_count: Optional[int] = Field(default=None, ge=1)
    allows_multiple_groups: Optional[bool] = Field(default=None)
    allows_zero_groups: Optional[bool] = Field(default=None)
    notes: Optional[str] = Field(default=None)


# ── Database model ───────────────────────────────────────────────────────────


class Subject(TimestampMixin, SubjectBase, SQLModel, table=True):
    """SQLModel table for a subject."""

    __tablename__ = prefixed_tables("subject")
    __table_args__ = (
        UniqueConstraint(
            "assignment_process_id", "name", name="uq_reparto_subject_process_name"
        ),
    )

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Subject ID.",
    )
    assignment_process_id: uuid.UUID = SQLField(
        sa_column=Column(
            "assignment_process_id", UUIDString(), nullable=False, index=True
        ),
        description="Owning assignment process ID.",
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class SubjectPublic(SubjectBase, SQLModel):
    """Public representation of a subject."""

    id: uuid.UUID = Field(description="Subject ID.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class SubjectsPublic(SQLModel):
    """List wrapper for public subjects."""

    data: list[SubjectPublic] = Field(description="List of subjects.")
    count: int = Field(description="Total subjects count.")
