"""Assignment table model and request/response schemas.

Redesigned for the three-stage adaptation (plan §5.10, amended by §20.9). An
``Assignment`` is now the department decision that **one** teacher occupies
**one complete, indivisible** teacher-position slot
(:class:`~reparto_service.db_models.hour_requirements.HourRequirement`):
"teacher X takes requirement slot S in full". The obsolete two-stage semantics
are gone (plan §3.6, §5.10):

* ``assigned_hours`` — a slot is indivisible, so an assignment always covers the
  requirement's ``required_teacher_hours`` in full; there is nothing to edit.
* ``assignment_type`` (``MAIN``/``SHARED``/…) and every shared-assignment field —
  a requirement can never be split across teachers.
* ``override_reason`` / ``overridden_by_user_id`` — there is no over-assignment
  override; the department head raises ``extra_weekly_hours`` instead (plan §3.8).

New / DB-enforced invariants (plan §20.9):

* ``teaching_activity_id`` is **denormalised** from the requirement so the
  distinct-teacher rule can be enforced by the database. A composite foreign key
  ``(hour_requirement_id, teaching_activity_id) -> HourRequirement(id,
  teaching_activity_id)`` guarantees the denormalised value always matches the
  requirement's own activity.
* Active partial unique ``(hour_requirement_id) WHERE status = 'ACTIVE'`` — at
  most one live assignment per slot ("assignment existence means full coverage",
  plan §5.10).
* Active partial unique ``(teaching_activity_id, process_teacher_id) WHERE status
  = 'ACTIVE'`` — a teacher can never occupy two positions of the same activity
  (plan §3.7, §20.9); the database is the final barrier against concurrent
  sibling-slot double assignment.

Hour values elsewhere in the service stay ``float`` today; the fleet-wide
``Decimal`` / ``NUMERIC(..., 2)`` sweep is a dedicated later task (plan §3.9).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field
from sqlalchemy import ForeignKeyConstraint, Index, text
from sqlmodel import Column, Field as SQLField, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables
from reparto_service.enums import AssignmentSource, AssignmentStatus


# ── Base / shared schema ──────────────────────────────────────────────────────


class AssignmentBase(SQLModel):
    """Shared fields for assignment schemas."""

    assignment_process_id: uuid.UUID = Field(
        description="Owning assignment process ID."
    )
    hour_requirement_id: uuid.UUID = Field(
        description="Requirement slot this assignment covers in full."
    )
    teaching_activity_id: uuid.UUID = Field(
        description=(
            "Teaching activity of the requirement slot, denormalised from the "
            "requirement so the distinct-teacher rule is DB-enforced (plan §20.9)."
        ),
    )
    process_teacher_id: uuid.UUID = Field(
        description="Process teacher occupying the slot."
    )
    source: AssignmentSource = Field(
        default=AssignmentSource.DEPARTMENT_HEAD,
        description="Origin of the assignment record (plan §5.10).",
    )
    status: AssignmentStatus = Field(
        default=AssignmentStatus.ACTIVE,
        description="Lifecycle status: ACTIVE occupancy or CANCELLED (plan §5.10).",
    )
    chosen_by_user_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Auth user who originally chose the assignment.",
    )
    confirmed_by_user_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Auth user who confirmed the assignment.",
    )
    notes: Optional[str] = Field(default=None, description="Free-form notes.")


# ── Request schemas ───────────────────────────────────────────────────────────


class AssignmentCreate(SQLModel):
    """Payload for a manual (department-head) assignment (plan §7.7).

    Only the slot and the teacher are supplied. The activity is derived from the
    requirement server-side (never trusted from the client, plan §20.9) and the
    assignment always covers the slot in full — there is no hour or share input.
    """

    hour_requirement_id: uuid.UUID = Field(
        description="Requirement slot to assign in full."
    )
    process_teacher_id: uuid.UUID = Field(
        description="Process teacher to occupy the slot."
    )
    notes: Optional[str] = Field(default=None, max_length=1000)


class AssignmentDirectChoice(SQLModel):
    """Teacher LAN direct-choice payload (plan §7.7)."""

    meeting_session_id: uuid.UUID = Field(description="Active meeting session ID.")
    hour_requirement_id: uuid.UUID = Field(description="Requirement slot to choose.")
    notes: Optional[str] = Field(default=None, max_length=1000)


class AssignmentUpdate(SQLModel):
    """Partial update schema.

    Hours, type and override are gone (plan §5.10); only free-form ``notes`` can
    be edited in place. Cancellation and reassignment are their own actions.
    """

    notes: Optional[str] = Field(default=None)


# ── Database model ───────────────────────────────────────────────────────────


class Assignment(TimestampMixin, AssignmentBase, SQLModel, table=True):
    """SQLModel table for a complete, indivisible slot occupancy."""

    __tablename__ = prefixed_tables("assignment")
    __table_args__ = (
        # Denormalisation guard (plan §20.9): the assignment's activity must be
        # the requirement's own activity. Backed by HourRequirement's
        # UNIQUE (id, teaching_activity_id).
        ForeignKeyConstraint(
            ["hour_requirement_id", "teaching_activity_id"],
            [
                f"{prefixed_tables('hour_requirement')}.id",
                f"{prefixed_tables('hour_requirement')}.teaching_activity_id",
            ],
            name="fk_reparto_assignment_requirement_activity",
        ),
        # At most one ACTIVE assignment per requirement slot (plan §5.10):
        # assignment existence means full, single-teacher coverage.
        Index(
            "uq_reparto_assignment_active_requirement",
            "hour_requirement_id",
            unique=True,
            sqlite_where=text("status = 'ACTIVE'"),
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        # A teacher may never hold two ACTIVE positions of the same activity
        # (plan §3.7, §20.9). The DB is the final barrier against concurrent
        # sibling-slot double assignment.
        Index(
            "uq_reparto_assignment_active_activity_teacher",
            "teaching_activity_id",
            "process_teacher_id",
            unique=True,
            sqlite_where=text("status = 'ACTIVE'"),
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Assignment ID.",
    )
    assignment_process_id: uuid.UUID = SQLField(
        sa_column=Column(
            "assignment_process_id", UUIDString(), nullable=False, index=True
        ),
        description="Owning assignment process ID.",
    )
    hour_requirement_id: uuid.UUID = SQLField(
        sa_column=Column(
            "hour_requirement_id", UUIDString(), nullable=False, index=True
        ),
        description="Requirement slot this assignment covers in full.",
    )
    teaching_activity_id: uuid.UUID = SQLField(
        sa_column=Column(
            "teaching_activity_id", UUIDString(), nullable=False, index=True
        ),
        description="Denormalised activity of the requirement slot (plan §20.9).",
    )
    process_teacher_id: uuid.UUID = SQLField(
        sa_column=Column(
            "process_teacher_id", UUIDString(), nullable=False, index=True
        ),
        description="Process teacher occupying the slot.",
    )
    chosen_by_user_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column("chosen_by_user_id", UUIDString(), nullable=True, index=True),
        description="Auth user who originally chose the assignment.",
    )
    confirmed_by_user_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column("confirmed_by_user_id", UUIDString(), nullable=True),
        description="Auth user who confirmed the assignment.",
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class AssignmentPublic(AssignmentBase, SQLModel):
    """Public representation of an assignment."""

    id: uuid.UUID = Field(description="Assignment ID.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class AssignmentsPublic(SQLModel):
    """List wrapper for public assignments."""

    data: list[AssignmentPublic] = Field(description="List of assignments.")
    count: int = Field(description="Total assignments count.")


__all__ = [
    "Assignment",
    "AssignmentBase",
    "AssignmentCreate",
    "AssignmentDirectChoice",
    "AssignmentUpdate",
    "AssignmentPublic",
    "AssignmentsPublic",
]
