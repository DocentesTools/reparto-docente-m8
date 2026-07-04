"""MeetingSession table model and request/response schemas.

Meeting sessions represent the LAN-facing meeting window for one
assignment process. Phase 2 uses them for read mode: teachers may read
the process while the session is open, but direct selection remains
disabled until Phase 4.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field, model_validator
from sqlalchemy import DateTime
from sqlmodel import Column, Field as SQLField, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables
from reparto_service.enums import MeetingSessionStatus, SelectionOrderMode


class MeetingSessionBase(SQLModel):
    """Shared fields for meeting session schemas."""

    assignment_process_id: uuid.UUID = Field(
        description="Owning assignment process ID."
    )
    status: MeetingSessionStatus = Field(
        default=MeetingSessionStatus.PREPARED,
        description="Current meeting session status.",
    )
    lan_access_enabled: bool = Field(
        default=True,
        description="Whether the session is visible to authenticated LAN clients.",
    )
    direct_teacher_selection_enabled: bool = Field(
        default=False,
        description="Whether teachers may choose directly from LAN clients.",
    )
    selection_mode: SelectionOrderMode = Field(
        default=SelectionOrderMode.NONE,
        description="Selection-order enforcement mode for this session.",
    )
    notes: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Optional operational notes for the meeting.",
    )

    @model_validator(mode="after")
    def validate_direct_selection_requires_lan(self) -> "MeetingSessionBase":
        """Reject direct selection when LAN access is disabled."""
        if self.direct_teacher_selection_enabled and not self.lan_access_enabled:
            raise ValueError("Direct teacher selection requires LAN access.")
        return self


class MeetingSessionCreate(MeetingSessionBase):
    """Schema for creating a meeting session."""


class MeetingSessionUpdate(SQLModel):
    """Partial update schema for a meeting session."""

    status: Optional[MeetingSessionStatus] = Field(default=None)
    lan_access_enabled: Optional[bool] = Field(default=None)
    direct_teacher_selection_enabled: Optional[bool] = Field(default=None)
    selection_mode: Optional[SelectionOrderMode] = Field(default=None)
    notes: Optional[str] = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_direct_selection_requires_lan(self) -> "MeetingSessionUpdate":
        """Reject updates that explicitly disable LAN while enabling direct mode."""
        if self.direct_teacher_selection_enabled and self.lan_access_enabled is False:
            raise ValueError("Direct teacher selection requires LAN access.")
        return self


class MeetingSession(TimestampMixin, MeetingSessionBase, SQLModel, table=True):
    """SQLModel table for a meeting session."""

    __tablename__ = prefixed_tables("meeting_session")

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Meeting session ID.",
    )
    assignment_process_id: uuid.UUID = SQLField(
        sa_column=Column(
            "assignment_process_id", UUIDString(), nullable=False, index=True
        ),
        description="Owning assignment process ID.",
    )
    started_at: Optional[datetime] = SQLField(
        default=None,
        sa_column=Column("started_at", DateTime(timezone=True), nullable=True),
        description="When the meeting was opened.",
    )
    started_by_user_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column("started_by_user_id", UUIDString(), nullable=True),
        description="Auth user who opened the meeting.",
    )
    paused_at: Optional[datetime] = SQLField(
        default=None,
        sa_column=Column("paused_at", DateTime(timezone=True), nullable=True),
        description="When the meeting was paused.",
    )
    closed_at: Optional[datetime] = SQLField(
        default=None,
        sa_column=Column("closed_at", DateTime(timezone=True), nullable=True),
        description="When the meeting was closed.",
    )


class MeetingSessionPublic(MeetingSessionBase, SQLModel):
    """Public representation of a meeting session."""

    id: uuid.UUID = Field(description="Meeting session ID.")
    started_at: Optional[datetime] = Field(default=None)
    started_by_user_id: Optional[uuid.UUID] = Field(default=None)
    paused_at: Optional[datetime] = Field(default=None)
    closed_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class MeetingSessionsPublic(SQLModel):
    """List wrapper for public meeting sessions."""

    data: list[MeetingSessionPublic] = Field(description="List of meeting sessions.")
    count: int = Field(description="Total meeting sessions count.")
