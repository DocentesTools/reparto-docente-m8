"""AcademicYear table model and request/response schemas.

Represents one academic year (e.g. 2026/2027) and the optional link to the
previous year's record. School-scoped uniqueness is enforced by a composite
unique constraint in the table (the MVP only ever holds one school, but
the model leaves the door open for multi-school installations).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import Field
from sqlalchemy import UniqueConstraint
from sqlmodel import Column, Field as SQLField, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables
from reparto_service.enums import AcademicYearStatus


# ── Base, Create, Update schemas ──────────────────────────────────────────────


class AcademicYearBase(SQLModel):
    """Shared fields for academic year schemas."""

    label: str = Field(
        min_length=1,
        max_length=20,
        description="Display label, e.g. '2026/2027'.",
    )
    start_date: date = Field(description="First day of the academic year.")
    end_date: date = Field(description="Last day of the academic year.")
    status: AcademicYearStatus = Field(
        default=AcademicYearStatus.ACTIVE,
        description="Lifecycle status of the academic year record.",
    )
    previous_academic_year_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Link to the prior academic year for history navigation.",
    )
    school_id: Optional[uuid.UUID] = Field(
        default=None,
        description=(
            "School scope for the label uniqueness constraint. Optional in the "
            "schema; the table enforces non-null at the database level for MVP."
        ),
    )


class AcademicYearCreate(AcademicYearBase):
    """Schema for creating a new academic year."""


class AcademicYearUpdate(SQLModel):
    """Partial update schema — every field is optional."""

    label: Optional[str] = Field(default=None, min_length=1, max_length=20)
    start_date: Optional[date] = Field(default=None)
    end_date: Optional[date] = Field(default=None)
    status: Optional[AcademicYearStatus] = Field(default=None)
    previous_academic_year_id: Optional[uuid.UUID] = Field(default=None)
    school_id: Optional[uuid.UUID] = Field(default=None)


# ── Database model ───────────────────────────────────────────────────────────


class AcademicYear(TimestampMixin, AcademicYearBase, SQLModel, table=True):
    """SQLModel table for an academic year."""

    __tablename__ = prefixed_tables("academic_year")
    __table_args__ = (
        UniqueConstraint(
            "school_id", "label", name="uq_reparto_academic_year_school_label"
        ),
    )

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Academic year ID.",
    )
    previous_academic_year_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column(
            "previous_academic_year_id", UUIDString(), nullable=True, index=True
        ),
        description="Link to the prior academic year for history navigation.",
    )
    school_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column("school_id", UUIDString(), nullable=True, index=True),
        description="Owning school ID.",
    )
    created_by_user_id: uuid.UUID = SQLField(
        sa_column=Column(
            "created_by_user_id", UUIDString(), nullable=False, index=True
        ),
        description="Auth user who created the academic year.",
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class AcademicYearPublic(AcademicYearBase, SQLModel):
    """Public representation of an academic year."""

    id: uuid.UUID = Field(description="Academic year ID.")
    created_by_user_id: uuid.UUID = Field(description="Creator user ID.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class AcademicYearsPublic(SQLModel):
    """List wrapper for public academic years."""

    data: list[AcademicYearPublic] = Field(description="List of academic years.")
    count: int = Field(description="Total academic years count.")
