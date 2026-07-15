"""TeachingActivity / TeachingActivityGroup table models and schemas.

A :class:`TeachingActivity` is one concrete item in a department teaching plan
(plan §5.6): a main-subject block, a tutoring slot, a co-teaching activity, a
department-level activity, etc. It carries the *actual* planning values used by
the two balances (plan §3.1): ``group_weekly_hours_per_group`` feeds the group
balance once per linked group, while ``teacher_weekly_hours_per_position`` ×
``required_teacher_count`` feeds the teacher-load balance.

A :class:`TeachingActivityGroup` links an activity to one
:class:`~reparto_service.db_models.group_subjects.GroupSubject` cell (plan §5.7).
Every linked cell must reference the activity's own ``subject_id`` (a grouped
activity is single-subject); the group hours count once per linked group.

§20 amendments applied here (read §20 before editing):

* ``requires_distinct_teachers`` is intentionally absent (plan §20.9): the
  "one teacher may never hold two positions of the same activity" rule is
  absolute and enforced at the database level on ``Assignment`` (a later task),
  never a configurable per-activity flag.
* ``source_group_subject_id`` records the single ``GroupSubject`` a
  ``MAIN_GENERATED`` activity was materialised from (plan §20.10). Main
  activities are one-to-one with a source cell and therefore single-group; the
  partial unique index below keeps at most one live main activity per source
  cell. Multi-group activities are ``SECONDARY_MANUAL`` only.
* the generic ``status`` field named in §5.6 is realised by the two concrete
  mechanisms §20 defines instead of a bespoke enum: ``sync_state``
  (IN_SYNC / OUT_OF_SYNC vs the source cell — plan §20.10) and the
  ``retired_at`` timestamp (activities are RETIRED at a timestamp — plan §20.18).

Hour values stay ``float`` like every other hour field in the service today; the
fleet-wide ``Decimal`` / ``NUMERIC(..., 2)`` sweep is a dedicated later task
(plan §3.9).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field
from sqlalchemy import DateTime, Index, UniqueConstraint, text
from sqlmodel import Column, Field as SQLField, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables
from reparto_service.enums import (
    ActivityType,
    SubjectAllocationCategory,
    TeachingActivitySource,
    TeachingActivitySyncState,
)


# ── Base, Create, Update schemas ──────────────────────────────────────────────


class TeachingActivityBase(SQLModel):
    """Shared planning-value fields for teaching-activity schemas.

    ``teaching_plan_id`` is deliberately absent: the API is process-scoped and
    the owning plan is resolved server-side (plan §7.4). The hour fields carry
    *actual* planning values (not overrides), so both are required.
    """

    subject_id: uuid.UUID = Field(description="Subject this activity teaches.")
    allocation_category: SubjectAllocationCategory = Field(
        default=SubjectAllocationCategory.SECONDARY,
        description=(
            "Whether the activity is a MAIN or SECONDARY planning item "
            "(plan §5.6). Manual activities are SECONDARY by default."
        ),
    )
    activity_type: ActivityType = Field(
        default=ActivityType.ORDINARY,
        description=(
            "Descriptive category only (plan §20.17): controls labels, filters "
            "and reports. No domain behaviour may branch on this value."
        ),
    )
    group_weekly_hours_per_group: float = Field(
        ge=0,
        description=(
            "Actual weekly group hours this activity delivers to each linked "
            "group (plan §5.6, §3.1). Counted once per linked group."
        ),
    )
    teacher_weekly_hours_per_position: float = Field(
        ge=0,
        description=(
            "Actual weekly teacher-load hours per teacher position (plan §5.6, §3.1)."
        ),
    )
    required_teacher_count: int = Field(
        default=1,
        ge=1,
        description=(
            "Number of teacher positions this activity generates (plan §5.6, "
            "always >= 1). Co-teaching normally requires at least two."
        ),
    )
    notes: Optional[str] = Field(default=None, description="Free-form notes.")


class TeachingActivityCreate(TeachingActivityBase):
    """Create payload for a manual (SECONDARY) teaching activity.

    ``group_subject_ids`` is the set of group-subject cells the activity links
    (plan §5.7). Every referenced cell must live in the same process and match
    ``subject_id``. Link count is policy-checked against the subject's
    ``allows_zero_groups`` / ``allows_multiple_groups`` flags. ``MAIN_GENERATED``
    activities are not created here — they come from the materialisation flow
    (plan §5.6, §20.10), which is its own later task.
    """

    source: TeachingActivitySource = Field(
        default=TeachingActivitySource.SECONDARY_MANUAL,
        description=(
            "Origin of the activity. Only SECONDARY_MANUAL is accepted on this "
            "endpoint; MAIN_GENERATED is reserved for materialisation "
            "(plan §20.10)."
        ),
    )
    group_subject_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Group-subject cells this activity links (plan §5.7).",
    )


class TeachingActivityUpdate(SQLModel):
    """Partial update payload.

    Identity fields (``subject_id``, ``source``, ``source_group_subject_id``)
    are immutable. Supplying ``group_subject_ids`` replaces the full link set;
    omitting it leaves the existing links untouched.
    """

    allocation_category: Optional[SubjectAllocationCategory] = Field(default=None)
    activity_type: Optional[ActivityType] = Field(default=None)
    group_weekly_hours_per_group: Optional[float] = Field(default=None, ge=0)
    teacher_weekly_hours_per_position: Optional[float] = Field(default=None, ge=0)
    required_teacher_count: Optional[int] = Field(default=None, ge=1)
    notes: Optional[str] = Field(default=None)
    group_subject_ids: Optional[list[uuid.UUID]] = Field(default=None)


# ── Database models ──────────────────────────────────────────────────────────


class TeachingActivity(TimestampMixin, TeachingActivityBase, SQLModel, table=True):
    """SQLModel table for one concrete teaching-plan activity."""

    __tablename__ = prefixed_tables("teaching_activity")
    __table_args__ = (
        # At most one live MAIN_GENERATED activity per source GroupSubject cell
        # (plan §20.10): main activities are one-to-one with their source cell.
        # Retired rows and non-main activities are excluded from the constraint.
        Index(
            "uq_reparto_teaching_activity_main_source",
            "teaching_plan_id",
            "source_group_subject_id",
            unique=True,
            sqlite_where=text("source = 'MAIN_GENERATED' AND retired_at IS NULL"),
            postgresql_where=text("source = 'MAIN_GENERATED' AND retired_at IS NULL"),
        ),
    )

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Teaching activity ID.",
    )
    teaching_plan_id: uuid.UUID = SQLField(
        sa_column=Column("teaching_plan_id", UUIDString(), nullable=False, index=True),
        description="Owning teaching plan ID.",
    )
    subject_id: uuid.UUID = SQLField(
        sa_column=Column("subject_id", UUIDString(), nullable=False, index=True),
        description="Subject this activity teaches.",
    )
    source_group_subject_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column(
            "source_group_subject_id", UUIDString(), nullable=True, index=True
        ),
        description=(
            "Source GroupSubject cell for a MAIN_GENERATED activity "
            "(plan §20.10); NULL for manual/secondary activities."
        ),
    )
    source: TeachingActivitySource = SQLField(
        default=TeachingActivitySource.SECONDARY_MANUAL,
        description="Origin of the activity (plan §5.6).",
    )
    sync_state: TeachingActivitySyncState = SQLField(
        default=TeachingActivitySyncState.IN_SYNC,
        description=(
            "Sync state of a MAIN_GENERATED activity vs its source cell "
            "(plan §20.10). Always IN_SYNC for manual activities."
        ),
    )
    retired_at: Optional[datetime] = SQLField(
        default=None,
        sa_column=Column("retired_at", DateTime(timezone=True), nullable=True),
        description="When the activity was retired; NULL while live (plan §20.18).",
    )


class TeachingActivityGroup(SQLModel, table=True):
    """SQLModel table linking an activity to one group-subject cell (plan §5.7)."""

    __tablename__ = prefixed_tables("teaching_activity_group")
    __table_args__ = (
        UniqueConstraint(
            "teaching_activity_id",
            "group_subject_id",
            name="uq_reparto_teaching_activity_group_activity_cell",
        ),
    )

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Activity-group link ID.",
    )
    teaching_activity_id: uuid.UUID = SQLField(
        sa_column=Column(
            "teaching_activity_id", UUIDString(), nullable=False, index=True
        ),
        description="Linked teaching activity ID.",
    )
    group_subject_id: uuid.UUID = SQLField(
        sa_column=Column("group_subject_id", UUIDString(), nullable=False, index=True),
        description="Linked group-subject cell ID.",
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class TeachingActivityGroupPublic(SQLModel):
    """Public representation of one activity-group link."""

    id: uuid.UUID = Field(description="Activity-group link ID.")
    teaching_activity_id: uuid.UUID = Field(description="Linked teaching activity ID.")
    group_subject_id: uuid.UUID = Field(description="Linked group-subject cell ID.")


class TeachingActivityPublic(TeachingActivityBase, SQLModel):
    """Public representation of a teaching activity with its linked cells."""

    id: uuid.UUID = Field(description="Teaching activity ID.")
    teaching_plan_id: uuid.UUID = Field(description="Owning teaching plan ID.")
    source: TeachingActivitySource = Field(description="Origin of the activity.")
    source_group_subject_id: Optional[uuid.UUID] = Field(
        default=None, description="Source GroupSubject cell for a main activity."
    )
    sync_state: TeachingActivitySyncState = Field(
        description="Sync state vs the source cell (plan §20.10)."
    )
    retired_at: Optional[datetime] = Field(
        default=None, description="Retirement timestamp; NULL while live."
    )
    group_subject_ids: list[uuid.UUID] = Field(
        description="Group-subject cells this activity links (plan §5.7)."
    )
    linked_group_count: int = Field(
        description="Number of linked group-subject cells (plan §3.4)."
    )
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class TeachingActivitiesPublic(SQLModel):
    """List wrapper for public teaching activities."""

    data: list[TeachingActivityPublic] = Field(description="Teaching activities.")
    count: int = Field(description="Total teaching-activity count.")


__all__ = [
    "TeachingActivitiesPublic",
    "TeachingActivity",
    "TeachingActivityCreate",
    "TeachingActivityGroup",
    "TeachingActivityGroupPublic",
    "TeachingActivityPublic",
    "TeachingActivityUpdate",
]
