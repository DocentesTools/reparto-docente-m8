"""AssignmentProcess table model and request/response schemas.

The annual departmental assignment process is the unit of work for the
department head. It binds one academic year, one school and one
department, and owns the process teachers, subjects, teaching groups,
hour requirements and assignments for that year.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field
from sqlmodel import Column, Field as SQLField, SQLModel
from sqlalchemy import DateTime

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables
from reparto_service.enums import (
    AssignmentProcessStatus,
    SelectionOrderMode,
)


# ── Base, Create, Update schemas ──────────────────────────────────────────────


class AssignmentProcessBase(SQLModel):
    """Shared fields for assignment process schemas."""

    academic_year_id: uuid.UUID = Field(description="Owning academic year ID.")
    school_id: uuid.UUID = Field(description="Owning school ID.")
    department_id: uuid.UUID = Field(
        description="Owning department ID inside the school."
    )
    status: AssignmentProcessStatus = Field(
        default=AssignmentProcessStatus.DRAFT,
        description="Lifecycle status (plan 8.4).",
    )
    default_teacher_hours_reference: Optional[float] = Field(
        default=None,
        ge=0,
        description=(
            "Reference available hours per teacher for the meeting "
            "(information only; the authoritative value is per process teacher)."
        ),
    )
    selection_order_enabled: bool = Field(
        default=False,
        description="Whether the process configures a turn order.",
    )
    selection_order_mode: SelectionOrderMode = Field(
        default=SelectionOrderMode.NONE,
        description="How the configured turn order is enforced during a meeting.",
    )
    direct_teacher_selection_enabled: bool = Field(
        default=False,
        description=(
            "Whether teachers can choose their own assignments directly "
            "during an open LAN meeting (Phase 4 feature)."
        ),
    )
    lan_access_enabled: bool = Field(
        default=False,
        description=(
            "Whether teachers can read this process from the LAN while "
            "a meeting session is open (Phase 2 feature)."
        ),
    )
    created_from_process_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Source process id when this process was copied.",
    )


class AssignmentProcessCreate(AssignmentProcessBase):
    """Schema for creating a new assignment process."""


class AssignmentProcessUpdate(SQLModel):
    """Partial update schema — every field is optional."""

    status: Optional[AssignmentProcessStatus] = Field(default=None)
    default_teacher_hours_reference: Optional[float] = Field(default=None, ge=0)
    selection_order_enabled: Optional[bool] = Field(default=None)
    selection_order_mode: Optional[SelectionOrderMode] = Field(default=None)
    direct_teacher_selection_enabled: Optional[bool] = Field(default=None)
    lan_access_enabled: Optional[bool] = Field(default=None)


# ── Database model ───────────────────────────────────────────────────────────


class AssignmentProcess(TimestampMixin, AssignmentProcessBase, SQLModel, table=True):
    """SQLModel table for an assignment process."""

    __tablename__ = prefixed_tables("assignment_process")

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Assignment process ID.",
    )
    academic_year_id: uuid.UUID = SQLField(
        sa_column=Column("academic_year_id", UUIDString(), nullable=False, index=True),
        description="Owning academic year ID.",
    )
    school_id: uuid.UUID = SQLField(
        sa_column=Column("school_id", UUIDString(), nullable=False, index=True),
        description="Owning school ID.",
    )
    department_id: uuid.UUID = SQLField(
        sa_column=Column("department_id", UUIDString(), nullable=False, index=True),
        description="Owning department ID inside the school.",
    )
    created_from_process_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column(
            "created_from_process_id", UUIDString(), nullable=True, index=True
        ),
        description="Source process id when this process was copied.",
    )
    closed_at: Optional[datetime] = SQLField(
        default=None,
        sa_column=Column("closed_at", DateTime(timezone=True), nullable=True),
        description="When the process was closed (final status).",
    )
    closed_by_user_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column("closed_by_user_id", UUIDString(), nullable=True),
        description="Auth user who closed the process.",
    )
    created_by_user_id: uuid.UUID = SQLField(
        sa_column=Column(
            "created_by_user_id", UUIDString(), nullable=False, index=True
        ),
        description="Auth user who created the process.",
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class AssignmentProcessPublic(AssignmentProcessBase, SQLModel):
    """Public representation of an assignment process."""

    id: uuid.UUID = Field(description="Assignment process ID.")
    closed_at: Optional[datetime] = Field(default=None)
    closed_by_user_id: Optional[uuid.UUID] = Field(default=None)
    created_by_user_id: uuid.UUID = Field(description="Creator user ID.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class AssignmentProcessesPublic(SQLModel):
    """List wrapper for public assignment processes."""

    data: list[AssignmentProcessPublic] = Field(
        description="List of assignment processes."
    )
    count: int = Field(description="Total assignment processes count.")
