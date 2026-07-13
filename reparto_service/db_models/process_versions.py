"""Process version snapshot models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import Field
from sqlalchemy import JSON, Column
from sqlmodel import Field as SQLField, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables
from reparto_service.enums import AssignmentProcessStatus


class ProcessVersionBase(SQLModel):
    """Shared process-version fields."""

    assignment_process_id: uuid.UUID = Field(description="Owning process ID.")
    version_number: int = Field(ge=1, description="Monotonic version number.")
    status: AssignmentProcessStatus = Field(description="Process status snapshot.")
    reason: Optional[str] = Field(default=None, max_length=500)
    created_by_user_id: uuid.UUID = Field(description="Auth user that created it.")
    snapshot_json: dict[str, Any] = Field(description="Immutable process snapshot.")


class ProcessVersionCreate(SQLModel):
    """Request body for creating a process version."""

    reason: Optional[str] = Field(default=None, max_length=500)


class ProcessVersion(TimestampMixin, ProcessVersionBase, SQLModel, table=True):
    """SQLModel table for immutable process snapshots."""

    __tablename__ = prefixed_tables("process_version")

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
    )
    assignment_process_id: uuid.UUID = SQLField(
        sa_column=Column(
            "assignment_process_id", UUIDString(), nullable=False, index=True
        )
    )
    created_by_user_id: uuid.UUID = SQLField(
        sa_column=Column("created_by_user_id", UUIDString(), nullable=False)
    )
    snapshot_json: dict[str, Any] = SQLField(
        sa_column=Column("snapshot_json", JSON, nullable=False)
    )


class ProcessVersionPublic(ProcessVersionBase, SQLModel):
    """Public representation of a process version."""

    id: uuid.UUID = Field(description="Process version ID.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class ProcessVersionsPublic(SQLModel):
    """List wrapper for process versions."""

    data: list[ProcessVersionPublic]
    count: int


class VersionComparison(SQLModel):
    """Small deterministic diff summary between two snapshots."""

    left_version_id: uuid.UUID
    right_version_id: uuid.UUID
    changed_sections: list[str]
    required_hours_delta: float
    assigned_hours_delta: float
    teacher_count_delta: int
    requirement_count_delta: int
    assignment_count_delta: int
