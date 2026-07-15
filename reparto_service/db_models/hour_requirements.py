"""HourRequirement table model and response schemas.

Redesigned for the three-stage adaptation (plan §5.9, amended by §20.8): an
``HourRequirement`` is no longer a leadership *input* row (group + subject +
required hours). It is now one **generated, indivisible teacher-position slot**
produced from a :class:`~reparto_service.db_models.teaching_activities.TeachingActivity`
(plan §3.6, §3.7). A co-teaching activity with ``required_teacher_count = 2``
generates two slots (``position_index`` 0 and 1), each of
``required_teacher_hours = teacher_weekly_hours_per_position``; every slot is
assigned to exactly one teacher and can never be split.

Identity and generation lineage (plan §20.8, authoritative over the pre-§20
"generation as identity" wording in §5.9):

* ``id`` is **stable slot identity**; the logical slot is
  ``(teaching_activity_id, position_index)``.
* Active uniqueness is ``(teaching_activity_id, position_index)`` restricted to
  live rows (``retired_generation IS NULL``): a retired slot never blocks its
  successor.
* ``UNIQUE (id, teaching_activity_id)`` backs the composite foreign key the
  redesigned :class:`~reparto_service.db_models.assignments.Assignment` adds
  (plan §20.9) so the DB — not just the application — guarantees an assignment's
  denormalised ``teaching_activity_id`` matches its requirement.
* ``created_generation`` / ``last_validated_generation`` track the plan-wide
  processing generation (``TeachingPlan.current_generation_number``) a slot was
  born in and last re-validated in; ``retired_generation`` (nullable) marks the
  generation that retired it; ``superseded_by_requirement_id`` (nullable) links
  a reconciled assigned slot to the new row that replaced it (plan §20.8).

Requirements are **generated, never manually created or deleted** (plan §5.9,
§20.12): this module therefore exposes read schemas only. The
generation-preview / generate / reconciliation-preview / reconcile flows
(plan §7.5) and the composite FK on ``Assignment`` (plan §20.9) are their own
later tasks.

Hour values stay ``float`` like every other hour field in the service today; the
fleet-wide ``Decimal`` / ``NUMERIC(..., 2)`` sweep is a dedicated later task
(plan §3.9).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field
from sqlalchemy import Index, UniqueConstraint, text
from sqlmodel import Column, Field as SQLField, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables
from reparto_service.enums import HourRequirementStatus


# ── Database model ───────────────────────────────────────────────────────────


class HourRequirement(TimestampMixin, SQLModel, table=True):
    """SQLModel table for one generated, indivisible teacher-position slot."""

    __tablename__ = prefixed_tables("hour_requirement")
    __table_args__ = (
        # Composite-FK target for Assignment's denormalised teaching_activity_id
        # (plan §20.9). ``id`` is already the primary key; this pair is what the
        # later Assignment composite FK references.
        UniqueConstraint(
            "id",
            "teaching_activity_id",
            name="uq_reparto_hour_requirement_id_activity",
        ),
        # Active-slot uniqueness (plan §20.8): at most one live row per logical
        # slot ``(teaching_activity_id, position_index)``. Retired rows are
        # excluded so a superseded slot never blocks its replacement.
        Index(
            "uq_reparto_hour_requirement_active_slot",
            "teaching_activity_id",
            "position_index",
            unique=True,
            sqlite_where=text("retired_generation IS NULL"),
            postgresql_where=text("retired_generation IS NULL"),
        ),
    )

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Stable slot identity (plan §20.8).",
    )
    assignment_process_id: uuid.UUID = SQLField(
        sa_column=Column(
            "assignment_process_id", UUIDString(), nullable=False, index=True
        ),
        description="Owning assignment process ID.",
    )
    teaching_activity_id: uuid.UUID = SQLField(
        sa_column=Column(
            "teaching_activity_id", UUIDString(), nullable=False, index=True
        ),
        description="Teaching activity this slot was generated from (plan §5.9).",
    )
    position_index: int = SQLField(
        ge=0,
        description=(
            "Zero-based teacher-position index within the activity "
            "(plan §3.7). Slots 0..required_teacher_count-1."
        ),
    )
    required_teacher_hours: float = SQLField(
        ge=0,
        description=(
            "Indivisible weekly hours of this slot, derived from the activity's "
            "teacher_weekly_hours_per_position (plan §5.9, §3.6)."
        ),
    )
    created_generation: int = SQLField(
        ge=0,
        description=(
            "Plan generation the slot was created in "
            "(TeachingPlan.current_generation_number; plan §20.8)."
        ),
    )
    last_validated_generation: int = SQLField(
        ge=0,
        description=(
            "Latest plan generation this slot was re-validated as unchanged "
            "(plan §20.8)."
        ),
    )
    retired_generation: Optional[int] = SQLField(
        default=None,
        ge=0,
        nullable=True,
        description=(
            "Plan generation that retired the slot; NULL while live (plan §20.8)."
        ),
    )
    superseded_by_requirement_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column("superseded_by_requirement_id", UUIDString(), nullable=True),
        description=(
            "Row that replaced this slot during reconciliation; NULL otherwise "
            "(plan §20.8)."
        ),
    )
    status: HourRequirementStatus = SQLField(
        default=HourRequirementStatus.AVAILABLE,
        description=(
            "Slot lifecycle state (plan §5.9). A slot is AVAILABLE or fully "
            "ASSIGNED — there is no partial-coverage state."
        ),
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class HourRequirementPublic(SQLModel):
    """Public representation of one generated teacher-position slot."""

    id: uuid.UUID = Field(description="Stable slot identity.")
    assignment_process_id: uuid.UUID = Field(
        description="Owning assignment process ID."
    )
    teaching_activity_id: uuid.UUID = Field(
        description="Teaching activity this slot was generated from."
    )
    position_index: int = Field(description="Zero-based teacher-position index.")
    required_teacher_hours: float = Field(
        ge=0, description="Indivisible weekly hours of this slot."
    )
    status: HourRequirementStatus = Field(description="Slot lifecycle state.")
    created_generation: int = Field(description="Generation the slot was created in.")
    last_validated_generation: int = Field(
        description="Latest generation the slot was re-validated in."
    )
    retired_generation: Optional[int] = Field(
        default=None, description="Generation that retired the slot; NULL while live."
    )
    superseded_by_requirement_id: Optional[uuid.UUID] = Field(
        default=None, description="Row that replaced this slot on reconciliation."
    )
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class HourRequirementsPublic(SQLModel):
    """List wrapper for public hour requirements."""

    data: list[HourRequirementPublic] = Field(description="List of hour requirements.")
    count: int = Field(description="Total hour requirements count.")


__all__ = [
    "HourRequirement",
    "HourRequirementPublic",
    "HourRequirementsPublic",
]
