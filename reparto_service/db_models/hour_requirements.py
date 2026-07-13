"""HourRequirement table model and request/response schemas.

An hour requirement is the row of input the department head receives from
school leadership: "group 1 ESO A needs 4 hours of Mathematics per week".
The same group/subject pair can carry multiple requirement rows of
different types (e.g. one ordinary and one reinforcement).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field
from sqlmodel import Column, Field as SQLField, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables
from reparto_service.enums import RequirementType


# ── Base, Create, Update schemas ──────────────────────────────────────────────


class HourRequirementBase(SQLModel):
    """Shared fields for hour requirement schemas."""

    assignment_process_id: uuid.UUID = Field(
        description="Owning assignment process ID."
    )
    teaching_group_id: uuid.UUID = Field(description="Target teaching group ID.")
    subject_id: uuid.UUID = Field(description="Target subject ID.")
    required_hours: float = Field(
        ge=0.0001,
        description="Required weekly hours. Must be strictly positive.",
    )
    requirement_type: RequirementType = Field(
        default=RequirementType.ORDINARY,
        description="Type of requirement (plan 8.9).",
    )
    flags: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Optional flags expressed as a comma-separated string.",
    )
    notes: Optional[str] = Field(default=None, description="Free-form notes.")


class HourRequirementCreate(HourRequirementBase):
    """Schema for creating a new hour requirement."""


class HourRequirementUpdate(SQLModel):
    """Partial update schema — every field is optional."""

    required_hours: Optional[float] = Field(default=None, ge=0.0001)
    requirement_type: Optional[RequirementType] = Field(default=None)
    flags: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None)


# ── Database model ───────────────────────────────────────────────────────────


class HourRequirement(TimestampMixin, HourRequirementBase, SQLModel, table=True):
    """SQLModel table for an hour requirement."""

    __tablename__ = prefixed_tables("hour_requirement")

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Hour requirement ID.",
    )
    assignment_process_id: uuid.UUID = SQLField(
        sa_column=Column(
            "assignment_process_id", UUIDString(), nullable=False, index=True
        ),
        description="Owning assignment process ID.",
    )
    teaching_group_id: uuid.UUID = SQLField(
        sa_column=Column("teaching_group_id", UUIDString(), nullable=False, index=True),
        description="Target teaching group ID.",
    )
    subject_id: uuid.UUID = SQLField(
        sa_column=Column("subject_id", UUIDString(), nullable=False, index=True),
        description="Target subject ID.",
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class HourRequirementPublic(HourRequirementBase, SQLModel):
    """Public representation of an hour requirement."""

    id: uuid.UUID = Field(description="Hour requirement ID.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class HourRequirementsPublic(SQLModel):
    """List wrapper for public hour requirements."""

    data: list[HourRequirementPublic] = Field(description="List of hour requirements.")
    count: int = Field(description="Total hour requirements count.")
