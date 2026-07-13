"""ProcessTeacher table model and request/response schemas.

ProcessTeacher binds a teacher profile to one assignment process and
carries the per-process data: available hours, selection-order position
and flags. The model is the join object between the auth-side identity
(``TeacherProfile`` / auth user) and the process-side data.
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
from reparto_service.enums import ProcessTeacherStatus


# ── Base, Create, Update schemas ──────────────────────────────────────────────


class ProcessTeacherBase(SQLModel):
    """Shared fields for process teacher schemas."""

    assignment_process_id: uuid.UUID = Field(
        description="Owning assignment process ID."
    )
    teacher_profile_id: uuid.UUID = Field(description="Linked teacher profile ID.")
    available_hours: float = Field(
        default=0,
        ge=0,
        description=(
            "Total hours the teacher is available for this process. "
            "The product of the weekly schedule and the contract percentage."
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
    """Partial update schema — every field is optional."""

    available_hours: Optional[float] = Field(default=None, ge=0)
    participates_in_selection: Optional[bool] = Field(default=None)
    selection_position: Optional[int] = Field(default=None, ge=0)
    selection_points: Optional[float] = Field(default=None, ge=0)
    selection_criteria_label: Optional[str] = Field(default=None, max_length=150)
    selection_notes: Optional[str] = Field(default=None)
    order_locked: Optional[bool] = Field(default=None)
    status: Optional[ProcessTeacherStatus] = Field(default=None)


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


# ── Public/read schemas ──────────────────────────────────────────────────────


class ProcessTeacherPublic(ProcessTeacherBase, SQLModel):
    """Public representation of a process teacher."""

    id: uuid.UUID = Field(description="Process teacher ID.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class ProcessTeachersPublic(SQLModel):
    """List wrapper for public process teachers."""

    data: list[ProcessTeacherPublic] = Field(description="List of process teachers.")
    count: int = Field(description="Total process teachers count.")
