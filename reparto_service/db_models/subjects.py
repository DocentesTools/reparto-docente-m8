"""Subject table model and request/response schemas.

A subject is owned by one assignment process — it represents a subject
managed by the department for that year, e.g. 'Mathematics', 'Mathematics I'
or 'Reinforcement Mathematics'.
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


class SubjectBase(SQLModel):
    """Shared fields for subject schemas."""

    assignment_process_id: uuid.UUID = Field(
        description="Owning assignment process ID."
    )
    name: str = Field(
        min_length=1,
        max_length=150,
        description="Subject name, e.g. 'Mathematics'.",
    )
    stage: Optional[str] = Field(
        default=None,
        max_length=50,
        description="Educational stage, e.g. 'ESO', 'Bachillerato'.",
    )
    notes: Optional[str] = Field(default=None, description="Free-form notes.")


class SubjectCreate(SubjectBase):
    """Schema for creating a new subject."""


class SubjectUpdate(SQLModel):
    """Partial update schema — every field is optional."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    stage: Optional[str] = Field(default=None, max_length=50)
    notes: Optional[str] = Field(default=None)


# ── Database model ───────────────────────────────────────────────────────────


class Subject(TimestampMixin, SubjectBase, SQLModel, table=True):
    """SQLModel table for a subject."""

    __tablename__ = prefixed_tables("subject")
    __table_args__ = (
        UniqueConstraint(
            "assignment_process_id", "name", name="uq_reparto_subject_process_name"
        ),
    )

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Subject ID.",
    )
    assignment_process_id: uuid.UUID = SQLField(
        sa_column=Column(
            "assignment_process_id", UUIDString(), nullable=False, index=True
        ),
        description="Owning assignment process ID.",
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class SubjectPublic(SubjectBase, SQLModel):
    """Public representation of a subject."""

    id: uuid.UUID = Field(description="Subject ID.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class SubjectsPublic(SQLModel):
    """List wrapper for public subjects."""

    data: list[SubjectPublic] = Field(description="List of subjects.")
    count: int = Field(description="Total subjects count.")
