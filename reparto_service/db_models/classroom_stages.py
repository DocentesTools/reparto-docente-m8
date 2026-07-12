"""Global classroom-stage table and API schemas."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import Field, field_validator, model_validator
from sqlalchemy import UniqueConstraint
from sqlmodel import Column, Field as SQLField, Relationship, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables

if TYPE_CHECKING:
    from reparto_service.db_models.teaching_groups import TeachingGroup


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


class ClassroomStageFields(SQLModel):
    """Mutable fields shared by classroom-stage schemas."""

    stage: str = Field(min_length=1, max_length=100)
    min_grade: int = Field(gt=0)
    max_grade: int = Field(gt=0)
    label: str = Field(min_length=1, max_length=30)

    @field_validator("stage", "label", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        """Trim and collapse whitespace before length validation."""
        return _normalize_text(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_grade_range(self) -> "ClassroomStageFields":
        """Require an ascending inclusive grade range."""
        if self.min_grade > self.max_grade:
            raise ValueError("min_grade must be less than or equal to max_grade")
        return self


class ClassroomStageCreate(ClassroomStageFields):
    """Create payload for a global classroom stage."""


class ClassroomStageUpdate(SQLModel):
    """Partial update payload for a classroom stage."""

    stage: str | None = Field(default=None, min_length=1, max_length=100)
    min_grade: int | None = Field(default=None, gt=0)
    max_grade: int | None = Field(default=None, gt=0)
    label: str | None = Field(default=None, min_length=1, max_length=30)

    @field_validator("stage", "label", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        """Normalize supplied text while preserving omitted fields."""
        return _normalize_text(value) if isinstance(value, str) else value


class ClassroomStage(TimestampMixin, ClassroomStageFields, SQLModel, table=True):
    """Global educational stage used by every teaching group."""

    __tablename__ = prefixed_tables("classroom_stage")
    __table_args__ = (
        UniqueConstraint("stage", name="uq_reparto_classroom_stage_stage"),
    )

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
    )
    classrooms: list["TeachingGroup"] = Relationship(back_populates="classroom_stage")


class ClassroomStagePublic(ClassroomStageFields):
    """Public classroom-stage representation."""

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ClassroomStageSummary(ClassroomStageFields):
    """Nested stage representation returned with a teaching group."""

    id: uuid.UUID


class ClassroomStagesPublic(SQLModel):
    """List wrapper for global classroom stages."""

    data: list[ClassroomStagePublic]
    count: int


__all__ = [
    "ClassroomStage",
    "ClassroomStageCreate",
    "ClassroomStagePublic",
    "ClassroomStageSummary",
    "ClassroomStagesPublic",
    "ClassroomStageUpdate",
]
