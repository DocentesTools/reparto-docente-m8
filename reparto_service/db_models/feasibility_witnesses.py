"""Restricted persistence for deterministic feasibility witnesses.

The witness is a complete provisional slot-to-participant reparto and is
therefore never embedded in a common teaching-plan response, audit event,
snapshot or export (plan sections 20.6 and 20.24).  This internal one-to-one
row is exposed only by the administrator-gated feasibility endpoint.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field
from sqlalchemy import JSON, UniqueConstraint
from sqlmodel import Column, Field as SQLField, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables
from reparto_service.enums import FeasibilityStatus


class FeasibilityWitness(TimestampMixin, SQLModel, table=True):
    """Backend-only cached witness for one teaching plan."""

    __tablename__ = prefixed_tables("feasibility_witness")
    __table_args__ = (
        UniqueConstraint(
            "teaching_plan_id",
            name="uq_reparto_feasibility_witness_plan",
        ),
        UniqueConstraint(
            "assignment_process_id",
            name="uq_reparto_feasibility_witness_process",
        ),
    )

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
    )
    teaching_plan_id: uuid.UUID = SQLField(
        sa_column=Column("teaching_plan_id", UUIDString(), nullable=False, index=True)
    )
    assignment_process_id: uuid.UUID = SQLField(
        sa_column=Column(
            "assignment_process_id", UUIDString(), nullable=False, index=True
        )
    )
    input_fingerprint: str = SQLField(max_length=128, index=True)
    solver_version: str = SQLField(max_length=64)
    witness_json: list[dict[str, str]] = SQLField(
        sa_column=Column("witness_json", JSON, nullable=False)
    )
    diagnostics_json: list[dict[str, Any]] = SQLField(
        default_factory=list,
        sa_column=Column("diagnostics_json", JSON, nullable=False),
    )


class FeasibilityWitnessEntryPublic(SQLModel):
    """One administration-only persisted witness mapping."""

    slot_id: uuid.UUID = Field(description="Requirement slot ID.")
    process_teacher_id: uuid.UUID = Field(description="Provisional participant ID.")


class FeasibilityWitnessPublic(SQLModel):
    """Administration-only witness response."""

    teaching_plan_id: uuid.UUID
    assignment_process_id: uuid.UUID
    input_fingerprint: str
    solver_version: str
    checked_at: datetime
    witness: list[FeasibilityWitnessEntryPublic]


class FeasibilityEvaluationPublic(SQLModel):
    """Result of an administration-only bounded feasibility evaluation."""

    teaching_plan_id: uuid.UUID
    assignment_process_id: uuid.UUID
    status: FeasibilityStatus
    input_fingerprint: str
    solver_version: str
    checked_at: datetime
    cache_reused: bool
    witness_available: bool
    states_explored: int
    memoization_hits: int


__all__ = [
    "FeasibilityEvaluationPublic",
    "FeasibilityWitness",
    "FeasibilityWitnessEntryPublic",
    "FeasibilityWitnessPublic",
]
