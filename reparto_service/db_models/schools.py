"""School table model and request/response schemas.

A school represents the institute context for an assignment process. The
MVP targets a single-school local install, but the model leaves room for
multi-school expansion by treating every downstream object as
school-scoped.
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


class SchoolBase(SQLModel):
    """Shared fields for school schemas."""

    name: str = Field(
        min_length=1,
        max_length=200,
        description="Official name of the school.",
    )
    locality: Optional[str] = Field(
        default=None, max_length=100, description="Town or city."
    )
    province: Optional[str] = Field(
        default=None, max_length=100, description="Province."
    )
    region: str = Field(
        default="Andalucía",
        max_length=100,
        description="Autonomous community / region.",
    )
    address: Optional[str] = Field(default=None, max_length=300, description="Address.")
    notes: Optional[str] = Field(default=None, description="Free-form notes.")


class SchoolCreate(SchoolBase):
    """Schema for creating a new school."""


class SchoolUpdate(SQLModel):
    """Partial update schema — every field is optional."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    locality: Optional[str] = Field(default=None, max_length=100)
    province: Optional[str] = Field(default=None, max_length=100)
    region: Optional[str] = Field(default=None, max_length=100)
    address: Optional[str] = Field(default=None, max_length=300)
    notes: Optional[str] = Field(default=None)


# ── Database model ───────────────────────────────────────────────────────────


class School(TimestampMixin, SchoolBase, SQLModel, table=True):
    """SQLModel table for a school."""

    __tablename__ = prefixed_tables("school")

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="School ID.",
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class SchoolPublic(SchoolBase, SQLModel):
    """Public representation of a school."""

    id: uuid.UUID = Field(description="School ID.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class SchoolsPublic(SQLModel):
    """List wrapper for public schools."""

    data: list[SchoolPublic] = Field(description="List of schools.")
    count: int = Field(description="Total schools count.")
