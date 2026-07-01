"""Assignment table model and request/response schemas.

An assignment is the department decision for one hour requirement:
"teacher X is assigned 4 hours of Mathematics in 1 ESO A this year".
The same requirement can be split across multiple teachers (a shared
assignment) or absorbed by a single one (a main assignment). Department
overrides — required to exceed the required hours for a row — are
recorded on the assignment itself with a reason.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field
from sqlmodel import Column, Field as SQLField, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables
from reparto_service.enums import (
    AssignmentSource,
    AssignmentStatus,
    AssignmentType,
)


# ── Base, Create, Update schemas ──────────────────────────────────────────────


class AssignmentBase(SQLModel):
    """Shared fields for assignment schemas."""

    assignment_process_id: uuid.UUID = Field(
        description="Owning assignment process ID."
    )
    hour_requirement_id: uuid.UUID = Field(description="Source hour requirement ID.")
    process_teacher_id: uuid.UUID = Field(
        description="Process teacher receiving the hours."
    )
    assigned_hours: float = Field(
        gt=0,
        description=(
            "Hours assigned to the teacher for this requirement. Must be "
            "strictly positive."
        ),
    )
    assignment_type: AssignmentType = Field(
        default=AssignmentType.MAIN,
        description="Type of assignment (plan 8.10).",
    )
    source: AssignmentSource = Field(
        default=AssignmentSource.DEPARTMENT_HEAD,
        description="Origin of the assignment record (plan 8.10).",
    )
    status: AssignmentStatus = Field(
        default=AssignmentStatus.DRAFT,
        description="Lifecycle status of the assignment (plan 8.10).",
    )
    chosen_by_user_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Auth user who originally chose the assignment.",
    )
    confirmed_by_user_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Auth user who confirmed the assignment.",
    )
    override_reason: Optional[str] = Field(
        default=None,
        max_length=500,
        description=(
            "Required when the assignment would push the requirement total "
            "above the configured required hours (department head override)."
        ),
    )
    overridden_by_user_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Auth user who recorded the override.",
    )
    notes: Optional[str] = Field(default=None, description="Free-form notes.")


class AssignmentCreate(AssignmentBase):
    """Schema for creating a new assignment."""


class AssignmentUpdate(SQLModel):
    """Partial update schema — every field is optional."""

    assigned_hours: Optional[float] = Field(default=None, gt=0)
    assignment_type: Optional[AssignmentType] = Field(default=None)
    source: Optional[AssignmentSource] = Field(default=None)
    status: Optional[AssignmentStatus] = Field(default=None)
    confirmed_by_user_id: Optional[uuid.UUID] = Field(default=None)
    override_reason: Optional[str] = Field(default=None, max_length=500)
    overridden_by_user_id: Optional[uuid.UUID] = Field(default=None)
    notes: Optional[str] = Field(default=None)


# ── Database model ───────────────────────────────────────────────────────────


class Assignment(TimestampMixin, AssignmentBase, SQLModel, table=True):
    """SQLModel table for an assignment."""

    __tablename__ = prefixed_tables("assignment")

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Assignment ID.",
    )
    assignment_process_id: uuid.UUID = SQLField(
        sa_column=Column(
            "assignment_process_id", UUIDString(), nullable=False, index=True
        ),
        description="Owning assignment process ID.",
    )
    hour_requirement_id: uuid.UUID = SQLField(
        sa_column=Column(
            "hour_requirement_id", UUIDString(), nullable=False, index=True
        ),
        description="Source hour requirement ID.",
    )
    process_teacher_id: uuid.UUID = SQLField(
        sa_column=Column(
            "process_teacher_id", UUIDString(), nullable=False, index=True
        ),
        description="Process teacher receiving the hours.",
    )
    chosen_by_user_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column("chosen_by_user_id", UUIDString(), nullable=True, index=True),
        description="Auth user who originally chose the assignment.",
    )
    confirmed_by_user_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column("confirmed_by_user_id", UUIDString(), nullable=True),
        description="Auth user who confirmed the assignment.",
    )
    overridden_by_user_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column("overridden_by_user_id", UUIDString(), nullable=True),
        description="Auth user who recorded the override.",
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class AssignmentPublic(AssignmentBase, SQLModel):
    """Public representation of an assignment."""

    id: uuid.UUID = Field(description="Assignment ID.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class AssignmentsPublic(SQLModel):
    """List wrapper for public assignments."""

    data: list[AssignmentPublic] = Field(description="List of assignments.")
    count: int = Field(description="Total assignments count.")
