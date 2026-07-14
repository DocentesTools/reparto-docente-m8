"""DepartmentHourAllocationRevision table model and request/response schemas.

School leadership communicates a weekly group-hour allocation to each
department. That figure may change at any time during the process, so it is
never overwritten in place: every value is stored as an immutable revision
(plan §5.1, §3.11). Exactly one revision per process is *current* (not
superseded); creating a new revision supersedes the previous one
transactionally and increments the per-process ``revision_number``.

``allocated_group_weekly_hours`` is a weekly-hour value. Like every other hour
field in the service today it is stored as ``float``; the fleet-wide switch to
``Decimal`` / ``NUMERIC(..., 2)`` and canonical decimal-string serialisation is
a dedicated later task (plan §3.9, "Implement decimal normalization utilities")
that converts all hour fields uniformly.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field
from sqlalchemy import DateTime, UniqueConstraint
from sqlmodel import Column, Field as SQLField, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables
from reparto_service.enums import DepartmentHourAllocationSource


# ── Base, Create schemas ──────────────────────────────────────────────────────


class DepartmentHourAllocationRevisionBase(SQLModel):
    """Client-writable fields shared by the create and read schemas.

    The server owns ``assignment_process_id`` (from the URL),
    ``revision_number``, ``created_by_user_id`` and ``superseded_at``; those are
    never accepted from the request body, so a client cannot forge a revision
    number or resurrect a superseded revision.
    """

    allocated_group_weekly_hours: float = Field(
        gt=0,
        description=(
            "Weekly group-teaching hours allocated to the department by school "
            "leadership. Must be strictly positive (plan §5.1)."
        ),
    )
    reason: str = Field(
        min_length=1,
        max_length=500,
        description="Mandatory justification recorded with the revision.",
    )
    source: DepartmentHourAllocationSource = Field(
        default=DepartmentHourAllocationSource.MANUAL_TRANSCRIPTION,
        description="How the revision entered the system (plan §20.16).",
    )
    source_reference: Optional[str] = Field(
        default=None,
        max_length=500,
        description=(
            "Free-text reference to the leadership communication (e.g. an email "
            "subject or document id). Escaped on render; may carry PII "
            "(plan §20.16)."
        ),
    )
    received_at: Optional[datetime] = Field(
        default=None,
        description="When the allocation was received from leadership (plan §20.16).",
    )


class DepartmentHourAllocationRevisionCreate(DepartmentHourAllocationRevisionBase):
    """Schema for creating a new allocation revision."""


# ── Database model ───────────────────────────────────────────────────────────


class DepartmentHourAllocationRevision(
    TimestampMixin, DepartmentHourAllocationRevisionBase, SQLModel, table=True
):
    """SQLModel table for one immutable school-leadership allocation revision."""

    __tablename__ = prefixed_tables("department_hour_allocation_revision")
    __table_args__ = (
        UniqueConstraint(
            "assignment_process_id",
            "revision_number",
            name="uq_reparto_alloc_revision_process_number",
        ),
    )

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Allocation revision ID.",
    )
    assignment_process_id: uuid.UUID = SQLField(
        sa_column=Column(
            "assignment_process_id", UUIDString(), nullable=False, index=True
        ),
        description="Owning assignment process ID.",
    )
    revision_number: int = SQLField(
        ge=1,
        description="Per-process 1-based revision counter (unique within a process).",
    )
    created_by_user_id: uuid.UUID = SQLField(
        sa_column=Column(
            "created_by_user_id", UUIDString(), nullable=False, index=True
        ),
        description="Auth user who transcribed/imported the revision (plan §20.16).",
    )
    superseded_at: Optional[datetime] = SQLField(
        default=None,
        sa_column=Column("superseded_at", DateTime(timezone=True), nullable=True),
        description="When a later revision superseded this one; NULL while current.",
    )
    received_at: Optional[datetime] = SQLField(
        default=None,
        sa_column=Column("received_at", DateTime(timezone=True), nullable=True),
        description="When the allocation was received from leadership (plan §20.16).",
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class DepartmentHourAllocationRevisionPublic(
    DepartmentHourAllocationRevisionBase, SQLModel
):
    """Public representation of an allocation revision."""

    id: uuid.UUID = Field(description="Allocation revision ID.")
    assignment_process_id: uuid.UUID = Field(description="Owning process ID.")
    revision_number: int = Field(description="Per-process revision counter.")
    created_by_user_id: uuid.UUID = Field(description="User who entered the revision.")
    superseded_at: Optional[datetime] = Field(
        default=None,
        description="Supersession timestamp; NULL for the current revision.",
    )
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class DepartmentHourAllocationRevisionsPublic(SQLModel):
    """List wrapper for allocation revisions."""

    data: list[DepartmentHourAllocationRevisionPublic] = Field(
        description="Allocation revisions, ordered oldest-first."
    )
    count: int = Field(description="Total allocation-revision count.")
