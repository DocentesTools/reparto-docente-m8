"""SelectionTurn table model and request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field
from sqlalchemy import DateTime, UniqueConstraint
from sqlmodel import Column, Field as SQLField, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables
from reparto_service.db_models.assignments import AssignmentCreate
from reparto_service.enums import SelectionTurnStatus


class SelectionTurnBase(SQLModel):
    """Shared fields for selection turn schemas."""

    meeting_session_id: uuid.UUID = Field(description="Owning meeting session ID.")
    process_teacher_id: uuid.UUID = Field(description="Teacher taking this turn.")
    position: int = Field(ge=0, description="Zero-based position in turn order.")
    status: SelectionTurnStatus = Field(default=SelectionTurnStatus.PENDING)
    skip_reason: Optional[str] = Field(default=None, max_length=500)
    forced_by_user_id: Optional[uuid.UUID] = Field(default=None)
    notes: Optional[str] = Field(default=None, max_length=1000)


class SelectionTurnCreate(SelectionTurnBase):
    """Schema for manually creating a selection turn."""


class SelectionTurnAction(SQLModel):
    """Action body used for skip and override operations."""

    reason: str = Field(min_length=1, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=1000)


class SelectionTurnComplete(SQLModel):
    """Completion body, optionally carrying the assignment to record."""

    assignment: Optional[AssignmentCreate] = Field(default=None)
    notes: Optional[str] = Field(default=None, max_length=1000)


class SelectionTurn(TimestampMixin, SelectionTurnBase, SQLModel, table=True):
    """SQLModel table for a selection turn."""

    __tablename__ = prefixed_tables("selection_turn")
    __table_args__ = (
        UniqueConstraint(
            "meeting_session_id",
            "position",
            name="uq_reparto_selection_turn_session_position",
        ),
        UniqueConstraint(
            "meeting_session_id",
            "process_teacher_id",
            name="uq_reparto_selection_turn_session_teacher",
        ),
    )

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
    )
    meeting_session_id: uuid.UUID = SQLField(
        sa_column=Column("meeting_session_id", UUIDString(), nullable=False, index=True)
    )
    process_teacher_id: uuid.UUID = SQLField(
        sa_column=Column("process_teacher_id", UUIDString(), nullable=False, index=True)
    )
    started_at: Optional[datetime] = SQLField(
        default=None,
        sa_column=Column("started_at", DateTime(timezone=True), nullable=True),
    )
    completed_at: Optional[datetime] = SQLField(
        default=None,
        sa_column=Column("completed_at", DateTime(timezone=True), nullable=True),
    )
    skipped_at: Optional[datetime] = SQLField(
        default=None,
        sa_column=Column("skipped_at", DateTime(timezone=True), nullable=True),
    )
    forced_by_user_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column("forced_by_user_id", UUIDString(), nullable=True),
    )


class SelectionTurnPublic(SelectionTurnBase, SQLModel):
    """Public representation of a selection turn."""

    id: uuid.UUID = Field(description="Selection turn ID.")
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    skipped_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class SelectionTurnsPublic(SQLModel):
    """List wrapper for public selection turns."""

    data: list[SelectionTurnPublic] = Field(description="List of selection turns.")
    count: int = Field(description="Total selection turns count.")
