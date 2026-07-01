"""TeacherProfile table model and request/response schemas.

Represents a teacher inside the local docentes domain. The profile is
intentionally minimal: a display name, an optional link to an auth user
(``user_id``) and operational flags. Personal data like DNI, address or
phone numbers is intentionally NOT stored (plan 8.5, 19).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field
from sqlmodel import Column, Field as SQLField, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables


# ── Base, Create, Update schemas ──────────────────────────────────────────────


class TeacherProfileBase(SQLModel):
    """Shared fields for teacher profile schemas."""

    display_name: str = Field(
        min_length=1,
        max_length=150,
        description="Display name shown to other users.",
    )
    user_id: Optional[uuid.UUID] = Field(
        default=None,
        description=(
            "Optional link to the auth-service user id. Unset until the "
            "department head binds the profile to a real account."
        ),
    )
    active: bool = Field(
        default=True,
        description="Whether the profile is still active in the department.",
    )
    notes: Optional[str] = Field(default=None, description="Free-form notes.")


class TeacherProfileCreate(TeacherProfileBase):
    """Schema for creating a new teacher profile."""


class TeacherProfileUpdate(SQLModel):
    """Partial update schema — every field is optional."""

    display_name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    user_id: Optional[uuid.UUID] = Field(default=None)
    active: Optional[bool] = Field(default=None)
    notes: Optional[str] = Field(default=None)


# ── Database model ───────────────────────────────────────────────────────────


class TeacherProfile(TimestampMixin, TeacherProfileBase, SQLModel, table=True):
    """SQLModel table for a teacher profile."""

    __tablename__ = prefixed_tables("teacher_profile")

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Teacher profile ID.",
    )
    user_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column("user_id", UUIDString(), nullable=True, index=True),
        description="Optional link to the auth-service user id.",
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class TeacherProfilePublic(TeacherProfileBase, SQLModel):
    """Public representation of a teacher profile."""

    id: uuid.UUID = Field(description="Teacher profile ID.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class TeacherProfilesPublic(SQLModel):
    """List wrapper for public teacher profiles."""

    data: list[TeacherProfilePublic] = Field(description="List of teacher profiles.")
    count: int = Field(description="Total teacher profiles count.")
