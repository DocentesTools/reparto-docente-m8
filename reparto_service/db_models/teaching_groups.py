"""TeachingGroup table model and request/response schemas.

A teaching group represents the teaching group/class context (e.g.
'1 ESO A' = stage 'ESO', grade 1, group code 'A').
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


class TeachingGroupBase(SQLModel):
    """Shared fields for teaching group schemas."""

    assignment_process_id: uuid.UUID = Field(
        description="Owning assignment process ID."
    )
    stage: str = Field(
        min_length=1,
        max_length=50,
        description="Educational stage, e.g. 'ESO', 'Bachillerato'.",
    )
    grade: int = Field(
        ge=0,
        le=20,
        description="Grade within the stage, e.g. 1, 2, 3, 4.",
    )
    group_code: str = Field(
        min_length=1,
        max_length=10,
        description="Group code inside the grade, e.g. 'A', 'B'.",
    )
    label: str = Field(
        min_length=1,
        max_length=100,
        description="Human-readable label, e.g. '1 ESO A'.",
    )
    notes: Optional[str] = Field(default=None, description="Free-form notes.")


class TeachingGroupCreate(TeachingGroupBase):
    """Schema for creating a new teaching group."""


class TeachingGroupUpdate(SQLModel):
    """Partial update schema — every field is optional."""

    stage: Optional[str] = Field(default=None, min_length=1, max_length=50)
    grade: Optional[int] = Field(default=None, ge=0, le=20)
    group_code: Optional[str] = Field(default=None, min_length=1, max_length=10)
    label: Optional[str] = Field(default=None, min_length=1, max_length=100)
    notes: Optional[str] = Field(default=None)


# ── Database model ───────────────────────────────────────────────────────────


class TeachingGroup(TimestampMixin, TeachingGroupBase, SQLModel, table=True):
    """SQLModel table for a teaching group."""

    __tablename__ = prefixed_tables("teaching_group")
    __table_args__ = (
        UniqueConstraint(
            "assignment_process_id",
            "label",
            name="uq_reparto_teaching_group_process_label",
        ),
    )

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Teaching group ID.",
    )
    assignment_process_id: uuid.UUID = SQLField(
        sa_column=Column(
            "assignment_process_id", UUIDString(), nullable=False, index=True
        ),
        description="Owning assignment process ID.",
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class TeachingGroupPublic(TeachingGroupBase, SQLModel):
    """Public representation of a teaching group."""

    id: uuid.UUID = Field(description="Teaching group ID.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class TeachingGroupsPublic(SQLModel):
    """List wrapper for public teaching groups."""

    data: list[TeachingGroupPublic] = Field(description="List of teaching groups.")
    count: int = Field(description="Total teaching groups count.")
