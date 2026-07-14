"""GroupSubject table model and request/response schemas.

A ``GroupSubject`` is one cell of the intermediate group-subject matrix
(plan §5.5): it declares that a given subject applies to a given teaching
group inside one assignment process, together with the *actual* per-group
planning values used when the department teaching plan is materialised.

The two hour fields (``group_weekly_hours`` and
``teacher_weekly_hours_per_position``) are optional overrides: when left
``NULL`` the corresponding ``Subject`` default is inherited at materialisation
time; when present they override that default for this one group (plan §5.5,
"hours may override subject defaults"). ``required_teacher_count`` is a concrete
per-cell count (``>= 1``) seeded from the subject default. Editing a subject
default NEVER retroactively rewrites an existing ``GroupSubject`` row — defaults
only seed future rows unless an explicit sync action runs (plan §20.14).

Uniqueness is enforced per process on ``(assignment_process_id,
teaching_group_id, subject_id)`` so a group/subject pair has exactly one
configuration cell (plan §5.5).

Hour values are stored as ``float`` like every other hour field in the service
today; the fleet-wide switch to ``Decimal`` / ``NUMERIC(..., 2)`` and canonical
decimal-string serialisation is a dedicated later task (plan §3.9).
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


# ── Base, Create, Update schemas ──────────────────────────────────────────────


class GroupSubjectBase(SQLModel):
    """Shared fields for group-subject schemas."""

    assignment_process_id: uuid.UUID = Field(
        description="Owning assignment process ID."
    )
    teaching_group_id: uuid.UUID = Field(description="Configured teaching group ID.")
    subject_id: uuid.UUID = Field(description="Configured subject ID.")
    group_weekly_hours: Optional[float] = Field(
        default=None,
        ge=0,
        description=(
            "Actual weekly group hours for this group/subject cell. NULL "
            "inherits the subject default; a value overrides it (plan §5.5)."
        ),
    )
    teacher_weekly_hours_per_position: Optional[float] = Field(
        default=None,
        ge=0,
        description=(
            "Actual weekly teacher-load hours per position. NULL inherits the "
            "subject default; a value overrides it (plan §5.5)."
        ),
    )
    required_teacher_count: int = Field(
        default=1,
        ge=1,
        description=(
            "Number of teacher positions this cell requires (plan §5.5, "
            "always >= 1). Seeded from the subject default."
        ),
    )
    active: bool = Field(
        default=True,
        description=("Whether this cell is an active planning candidate (plan §5.5)."),
    )
    notes: Optional[str] = Field(default=None, description="Free-form notes.")


class GroupSubjectCreate(GroupSubjectBase):
    """Schema for creating a new group-subject cell."""


class GroupSubjectUpdate(SQLModel):
    """Partial update schema — every field is optional.

    ``teaching_group_id``/``subject_id`` are immutable identity of the cell and
    are intentionally not updatable; change them by deleting and recreating.
    """

    group_weekly_hours: Optional[float] = Field(default=None, ge=0)
    teacher_weekly_hours_per_position: Optional[float] = Field(default=None, ge=0)
    required_teacher_count: Optional[int] = Field(default=None, ge=1)
    active: Optional[bool] = Field(default=None)
    notes: Optional[str] = Field(default=None)


# ── Database model ───────────────────────────────────────────────────────────


class GroupSubject(TimestampMixin, GroupSubjectBase, SQLModel, table=True):
    """SQLModel table for one group-subject configuration cell."""

    __tablename__ = prefixed_tables("group_subject")
    __table_args__ = (
        UniqueConstraint(
            "assignment_process_id",
            "teaching_group_id",
            "subject_id",
            name="uq_reparto_group_subject_process_group_subject",
        ),
    )

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Group-subject ID.",
    )
    assignment_process_id: uuid.UUID = SQLField(
        sa_column=Column(
            "assignment_process_id", UUIDString(), nullable=False, index=True
        ),
        description="Owning assignment process ID.",
    )
    teaching_group_id: uuid.UUID = SQLField(
        sa_column=Column("teaching_group_id", UUIDString(), nullable=False, index=True),
        description="Configured teaching group ID.",
    )
    subject_id: uuid.UUID = SQLField(
        sa_column=Column("subject_id", UUIDString(), nullable=False, index=True),
        description="Configured subject ID.",
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class GroupSubjectPublic(GroupSubjectBase, SQLModel):
    """Public representation of a group-subject cell."""

    id: uuid.UUID = Field(description="Group-subject ID.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class GroupSubjectsPublic(SQLModel):
    """List wrapper for public group-subject cells."""

    data: list[GroupSubjectPublic] = Field(description="List of group-subject cells.")
    count: int = Field(description="Total group-subject count.")


__all__ = [
    "GroupSubject",
    "GroupSubjectCreate",
    "GroupSubjectPublic",
    "GroupSubjectsPublic",
    "GroupSubjectUpdate",
]
