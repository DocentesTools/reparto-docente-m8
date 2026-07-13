"""Department table model and request/response schemas.

A department represents one teaching department inside a school. The MVP
expects one department per assignment process; the relationship is
many-to-one from the process's side.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field, model_validator
from sqlalchemy import UniqueConstraint
from sqlmodel import Column, Field as SQLField, SQLModel
from slugify import slugify

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables


# ── Base, Create, Update schemas ──────────────────────────────────────────────


class DepartmentBase(SQLModel):
    """Shared fields for department schemas."""

    school_id: uuid.UUID = Field(description="Owning school ID.")
    name: str = Field(
        min_length=1,
        max_length=150,
        description="Department name, e.g. 'Matemáticas'.",
    )
    slug: str = Field(
        min_length=1,
        max_length=150,
        description="URL-friendly identifier, auto-generated from ``name``.",
    )
    department_head_user_id: Optional[uuid.UUID] = Field(
        default=None, description="Auth user id of the department head."
    )
    notes: Optional[str] = Field(default=None, description="Free-form notes.")


class DepartmentGenerators(DepartmentBase):
    """Schema that auto-derives ``slug`` from ``name`` if missing."""

    @model_validator(mode="before")
    @classmethod
    def _generate_slug(cls, values: dict[str, object]) -> dict[str, object]:
        name = values.get("name")
        existing = values.get("slug")
        if isinstance(name, str) and (not existing or existing == ""):
            values["slug"] = slugify(name)
        return values


class DepartmentCreate(DepartmentGenerators):
    """Schema for creating a new department."""


class DepartmentUpdate(SQLModel):
    """Partial update schema — every field is optional."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    slug: Optional[str] = Field(default=None, min_length=1, max_length=150)
    department_head_user_id: Optional[uuid.UUID] = Field(default=None)
    notes: Optional[str] = Field(default=None)


# ── Database model ───────────────────────────────────────────────────────────


class Department(TimestampMixin, DepartmentBase, SQLModel, table=True):
    """SQLModel table for a department."""

    __tablename__ = prefixed_tables("department")
    __table_args__ = (
        UniqueConstraint("school_id", "slug", name="uq_reparto_department_school_slug"),
    )

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Department ID.",
    )
    school_id: uuid.UUID = SQLField(
        sa_column=Column("school_id", UUIDString(), nullable=False, index=True),
        description="Owning school ID.",
    )
    department_head_user_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column(
            "department_head_user_id", UUIDString(), nullable=True, index=True
        ),
        description="Auth user id of the department head.",
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class DepartmentPublic(DepartmentBase, SQLModel):
    """Public representation of a department."""

    id: uuid.UUID = Field(description="Department ID.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class DepartmentsPublic(SQLModel):
    """List wrapper for public departments."""

    data: list[DepartmentPublic] = Field(description="List of departments.")
    count: int = Field(description="Total departments count.")
