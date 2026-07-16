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
    """Deterministic diff summary between two three-stage snapshots (plan §10.3).

    The comparison surfaces the plan §10.3 dimensions of the three-stage domain:
    a changed leadership allocation, changed group / teacher balances, a changed
    subject category, an added/removed activity or group link, a changed
    teacher-position count, a changed participant base/extra target and a changed
    requirement generation — plus a small set of signed count/hour deltas and the
    names of the top-level snapshot sections that differ. Every hour delta is a
    canonical two-place decimal string (plan §3.9); an allocation delta is
    ``None`` when either side has no current allocation.
    """

    left_version_id: uuid.UUID
    right_version_id: uuid.UUID
    changed_sections: list[str]
    # ── plan §10.3 change flags ───────────────────────────────────────────────
    allocation_changed: bool
    group_hours_changed: bool
    teacher_load_changed: bool
    subject_category_changed: bool
    activity_added_or_removed: bool
    group_link_added_or_removed: bool
    teacher_position_count_changed: bool
    participant_target_changed: bool
    requirement_generation_changed: bool
    # ── signed deltas ─────────────────────────────────────────────────────────
    allocation_delta: Optional[str]
    group_load_delta: str
    teacher_load_delta: str
    participant_target_total_delta: str
    generation_number_delta: int
    teacher_count_delta: int
    activity_count_delta: int
    requirement_count_delta: int
