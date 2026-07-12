"""Process-scoped teaching groups exposed as classrooms in the UI."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from pydantic import Field, field_validator
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlmodel import Column, Field as SQLField, Relationship, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables
from reparto_service.db_models.classroom_stages import ClassroomStageSummary

if TYPE_CHECKING:
    from reparto_service.db_models.classroom_stages import ClassroomStage


class TeachingGroupBase(SQLModel):
    """Shared fields for process-scoped classroom schemas."""

    assignment_process_id: uuid.UUID
    classroom_stage_id: uuid.UUID
    grade: int = Field(gt=0)
    group_code: str = Field(min_length=1, max_length=10)
    notes: Optional[str] = None

    @field_validator("group_code", mode="before")
    @classmethod
    def normalize_group_code(cls, value: object) -> object:
        """Store group codes trimmed and uppercase."""
        return value.strip().upper() if isinstance(value, str) else value


class TeachingGroupCreate(TeachingGroupBase):
    """Create payload; an omitted or blank label is generated server-side."""

    label: Optional[str] = Field(default=None, max_length=100)


class TeachingGroupUpdate(SQLModel):
    """Partial update payload for a teaching group."""

    classroom_stage_id: Optional[uuid.UUID] = None
    grade: Optional[int] = Field(default=None, gt=0)
    group_code: Optional[str] = Field(default=None, min_length=1, max_length=10)
    label: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = None

    @field_validator("group_code", mode="before")
    @classmethod
    def normalize_group_code(cls, value: object) -> object:
        """Normalize a supplied group code."""
        return value.strip().upper() if isinstance(value, str) else value


class TeachingGroupBulkCreate(SQLModel):
    """Atomic inclusive group-range creation payload."""

    classroom_stage_id: uuid.UUID
    grade: int = Field(gt=0)
    group_start: str = Field(min_length=1, max_length=1)
    group_end: str = Field(min_length=1, max_length=1)

    @field_validator("group_start", "group_end", mode="before")
    @classmethod
    def normalize_group_code(cls, value: object) -> object:
        """Normalize bulk range endpoints."""
        return value.strip().upper() if isinstance(value, str) else value


class TeachingGroup(TimestampMixin, TeachingGroupBase, SQLModel, table=True):
    """Database teaching group with one mandatory global stage."""

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
    )
    assignment_process_id: uuid.UUID = SQLField(
        sa_column=Column(
            "assignment_process_id", UUIDString(), nullable=False, index=True
        )
    )
    classroom_stage_id: uuid.UUID = SQLField(
        sa_column=Column(
            "classroom_stage_id",
            UUIDString(),
            ForeignKey(
                f"{prefixed_tables('classroom_stage')}.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
            index=True,
        )
    )
    label: str = SQLField(min_length=1, max_length=100)
    classroom_stage: "ClassroomStage" = Relationship(back_populates="classrooms")


class TeachingGroupPublic(TeachingGroupBase):
    """Public teaching-group representation with nested stage data."""

    id: uuid.UUID
    label: str
    classroom_stage: ClassroomStageSummary
    created_at: datetime
    updated_at: datetime


class TeachingGroupsPublic(SQLModel):
    """List wrapper for teaching groups."""

    data: list[TeachingGroupPublic]
    count: int


__all__ = [
    "TeachingGroup",
    "TeachingGroupBulkCreate",
    "TeachingGroupCreate",
    "TeachingGroupPublic",
    "TeachingGroupsPublic",
    "TeachingGroupUpdate",
]
