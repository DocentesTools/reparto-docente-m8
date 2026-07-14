"""TeachingPlan table model and response schemas.

The teaching plan owns the intermediate department teaching-load planning
lifecycle that sits between configuration and teacher assignment (plan §5.2).
Exactly one plan exists per assignment process (one-to-one ownership); it is
created in :class:`~reparto_service.enums.TeachingPlanStatus.DRAFT` and moves
through the operational lifecycle documented in
:data:`reparto_service.services.planning_lifecycle.TEACHING_PLAN_LIFECYCLE`.

Feasibility is stored on a SEPARATE axis from ``status`` (plan §20.1): the
``feasibility_*`` columns record the third assignment-readiness invariant and
reset to ``NOT_EVALUATED`` on any relevant change (plan §20.14). The serialized
feasibility *witness* is never a column here — it lives in a restricted
backend-only store (plan §20.24); only the status, fingerprint, solver version
and a department-head-only diagnostics reference are persisted on the plan.

Naming note: the plan-wide generation counter is ``current_generation_number``
(plan §20.8, authoritative over the ``generation_number`` label in §5.2). It is
a *processing* revision counter, not slot identity — ``HourRequirement`` owns
slot identity (plan §20.8).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field
from sqlalchemy import DateTime, UniqueConstraint
from sqlmodel import Column, Field as SQLField, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables
from reparto_service.enums import FeasibilityStatus, TeachingPlanStatus


# ── Database model ───────────────────────────────────────────────────────────


class TeachingPlan(TimestampMixin, SQLModel, table=True):
    """SQLModel table for the single teaching plan owned by a process."""

    __tablename__ = prefixed_tables("teaching_plan")
    __table_args__ = (
        UniqueConstraint(
            "assignment_process_id",
            name="uq_reparto_teaching_plan_process",
        ),
    )

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Teaching plan ID.",
    )
    assignment_process_id: uuid.UUID = SQLField(
        sa_column=Column(
            "assignment_process_id", UUIDString(), nullable=False, index=True
        ),
        description="Owning assignment process ID (unique — one plan per process).",
    )
    allocation_revision_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column("allocation_revision_id", UUIDString(), nullable=True),
        description=(
            "Current allocation revision the plan is balanced against (plan §9). "
            "Set by the balance/allocation-change wiring; NULL until then."
        ),
    )
    status: TeachingPlanStatus = SQLField(
        default=TeachingPlanStatus.DRAFT,
        description="Operational lifecycle stage (plan §5.2).",
    )
    current_generation_number: int = SQLField(
        default=0,
        ge=0,
        description=(
            "Plan-wide processing-revision counter (plan §20.8); a requirement "
            "regeneration increments it. Not slot identity."
        ),
    )
    locked_at: Optional[datetime] = SQLField(
        default=None,
        sa_column=Column("locked_at", DateTime(timezone=True), nullable=True),
        description="When the plan was locked; NULL while unlocked.",
    )
    locked_by_user_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column("locked_by_user_id", UUIDString(), nullable=True),
        description="Auth user who locked the plan.",
    )
    requirements_generated_at: Optional[datetime] = SQLField(
        default=None,
        sa_column=Column(
            "requirements_generated_at", DateTime(timezone=True), nullable=True
        ),
        description="When teacher-requirement slots were last generated.",
    )
    stale_reason: Optional[str] = SQLField(
        default=None,
        max_length=500,
        description="Why the plan was marked stale (plan §5.2, §20.14).",
    )
    feasibility_status: FeasibilityStatus = SQLField(
        default=FeasibilityStatus.NOT_EVALUATED,
        description=(
            "Assignment-partition feasibility — the third invariant, on its own "
            "axis (plan §20.1). Resets to NOT_EVALUATED on any relevant change."
        ),
    )
    feasibility_generation: Optional[int] = SQLField(
        default=None,
        ge=0,
        description="Generation the feasibility result was computed against (plan §20.1).",
    )
    feasibility_checked_at: Optional[datetime] = SQLField(
        default=None,
        sa_column=Column(
            "feasibility_checked_at", DateTime(timezone=True), nullable=True
        ),
        description="When feasibility was last evaluated.",
    )
    feasibility_input_fingerprint: Optional[str] = SQLField(
        default=None,
        max_length=128,
        description=(
            "Deterministic hash of every feasibility input (plan §20.23); a "
            "mismatch invalidates a stored FEASIBLE result."
        ),
    )
    feasibility_solver_version: Optional[str] = SQLField(
        default=None,
        max_length=64,
        description=(
            "Solver/algorithm version the result was produced by (plan §20.23); "
            "a mismatch is treated as NOT_EVALUATED."
        ),
    )
    feasibility_diagnostics_ref: Optional[str] = SQLField(
        default=None,
        max_length=256,
        description=(
            "Reference to department-head-only feasibility diagnostics "
            "(plan §20.1, §20.24); never exposed to teachers or the shared screen."
        ),
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class TeachingPlanPublic(SQLModel):
    """Public representation of a teaching plan.

    The feasibility *witness* is deliberately absent (plan §20.24); only the
    ``feasibility_status`` and its provenance metadata are exposed. Role-based
    redaction of the diagnostics reference is applied by the feasibility routes
    added in a later task (plan §20.20).
    """

    id: uuid.UUID = Field(description="Teaching plan ID.")
    assignment_process_id: uuid.UUID = Field(description="Owning process ID.")
    allocation_revision_id: Optional[uuid.UUID] = Field(
        default=None, description="Allocation revision the plan is balanced against."
    )
    status: TeachingPlanStatus = Field(description="Operational lifecycle stage.")
    current_generation_number: int = Field(
        description="Plan-wide processing-revision counter (plan §20.8)."
    )
    locked_at: Optional[datetime] = Field(
        default=None, description="Lock timestamp; NULL while unlocked."
    )
    locked_by_user_id: Optional[uuid.UUID] = Field(
        default=None, description="User who locked the plan."
    )
    requirements_generated_at: Optional[datetime] = Field(
        default=None, description="When requirement slots were last generated."
    )
    stale_reason: Optional[str] = Field(
        default=None, description="Why the plan was marked stale."
    )
    feasibility_status: FeasibilityStatus = Field(
        description="Assignment-feasibility invariant (plan §20.1)."
    )
    feasibility_generation: Optional[int] = Field(
        default=None,
        description="Generation the feasibility result was computed against.",
    )
    feasibility_checked_at: Optional[datetime] = Field(
        default=None, description="When feasibility was last evaluated."
    )
    feasibility_input_fingerprint: Optional[str] = Field(
        default=None, description="Deterministic feasibility-input hash (plan §20.23)."
    )
    feasibility_solver_version: Optional[str] = Field(
        default=None, description="Solver version the result was produced by."
    )
    feasibility_diagnostics_ref: Optional[str] = Field(
        default=None, description="Department-head-only diagnostics reference."
    )
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class TeachingPlansPublic(SQLModel):
    """List wrapper for teaching plans (there is at most one per process)."""

    data: list[TeachingPlanPublic] = Field(description="Teaching plans.")
    count: int = Field(description="Total teaching-plan count.")
