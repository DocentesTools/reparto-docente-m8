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
§20.12): this module therefore exposes read schemas only, plus the
generation-preview / generate result schemas
(:class:`RequirementGenerationPreview` / :class:`RequirementGenerationResult`)
consumed by the plan §7.5 generation flow on
:class:`~reparto_service.controllers.hour_requirements.HourRequirementController`.
The reconciliation-preview / reconcile flow (plan §7.5) is its own later task.

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


# ── Generation preview / apply schemas (plan §7.5, §20.8) ─────────────────────


class RequirementSlotPlan(SQLModel):
    """One planned teacher-position slot in a generation preview (plan §7.5).

    Describes a slot the generation *would* create for the logical identity
    ``(teaching_activity_id, position_index)`` (plan §20.8) with the indivisible
    hours derived from the activity's ``teacher_weekly_hours_per_position``.
    """

    teaching_activity_id: uuid.UUID = Field(
        description="Activity the new slot belongs to."
    )
    position_index: int = Field(
        ge=0, description="Zero-based teacher-position index (plan §3.7)."
    )
    required_teacher_hours: float = Field(
        ge=0, description="Indivisible weekly hours of the planned slot."
    )


class RequirementGenerationPreview(SQLModel):
    """Dry-run diff of a requirement generation (plan §7.5, §20.8).

    Computed without mutating any row: it reports what a subsequent
    ``generate`` would do against the plan's current live requirement slots.
    Each existing slot is classified by the §20.8 identity model — an unchanged
    slot is *preserved* (keeps its id and assignment), a new logical position is
    *created*, an unassigned position that no longer exists is *retired*, and a
    change that would affect an **assigned** slot is a *conflict* that must go
    through the reconciliation flow instead (``requires_reconciliation``), so
    ``generate`` never silently overwrites or deletes an assignment (plan §7.5,
    §9). A value change to an *unassigned* slot is represented as a retire of the
    old row plus a create of the new one.
    """

    next_generation_number: int = Field(
        ge=1, description="Generation number a subsequent generate would assign."
    )
    to_create: list[RequirementSlotPlan] = Field(
        description="New teacher-position slots, ordered by (activity, position)."
    )
    create_count: int = Field(ge=0, description="Number of slots to create.")
    preserve_ids: list[uuid.UUID] = Field(
        description="Unchanged live slots kept with their assignment (plan §20.8)."
    )
    preserve_count: int = Field(ge=0, description="Number of slots preserved.")
    retire_ids: list[uuid.UUID] = Field(
        description="Unassigned live slots that would be retired (plan §20.8)."
    )
    retire_count: int = Field(ge=0, description="Number of slots retired.")
    conflict_ids: list[uuid.UUID] = Field(
        description=(
            "Assigned live slots a change would affect; these require "
            "reconciliation and block a plain generate (plan §7.5, §9)."
        )
    )
    conflict_count: int = Field(ge=0, description="Number of conflicting slots.")
    requires_reconciliation: bool = Field(
        description="True when any conflict exists (generate is blocked)."
    )
    is_noop: bool = Field(
        description="True when nothing would change (no create/retire/conflict)."
    )


class RequirementGenerationResult(SQLModel):
    """Outcome of an applied requirement generation (plan §7.5, §20.8).

    ``created`` lists the freshly generated slots; ``data``/``count`` is the full
    set of live slots after the run, ordered by (activity, position), so a caller
    gets both the delta and the resulting state in one response.
    """

    generation_number: int = Field(
        ge=1, description="Generation number assigned to this run (plan §20.8)."
    )
    created: list[HourRequirementPublic] = Field(
        description="Newly generated teacher-position slots."
    )
    created_count: int = Field(ge=0, description="Number of slots created.")
    preserved_count: int = Field(
        ge=0, description="Unchanged slots re-validated into this generation."
    )
    retired_count: int = Field(ge=0, description="Unassigned slots retired this run.")
    data: list[HourRequirementPublic] = Field(
        description="All live requirement slots after generation."
    )
    count: int = Field(ge=0, description="Total live slot count after generation.")


__all__ = [
    "HourRequirement",
    "HourRequirementPublic",
    "HourRequirementsPublic",
    "RequirementGenerationPreview",
    "RequirementGenerationResult",
    "RequirementSlotPlan",
]
