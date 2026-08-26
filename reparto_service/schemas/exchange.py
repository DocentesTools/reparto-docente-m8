"""Planning import/export exchange schemas (plan §3.10, §7.8).

The three-stage adaptation preserves the existing provisional/final export story
for the new intermediate planning stage. A *planning artifact* is a
self-describing snapshot of one teaching plan carrying:

* both independent balance states (plan §3.1) via
  :class:`~reparto_service.schemas.planning.PlanBalance`;
* the full blocking/warning validation report (plan §6.3/§6.4) via
  :class:`~reparto_service.schemas.planning.PlanValidationReport`;
* the live teaching activities with their per-activity group/teacher loads.

Draft and provisional artifacts are **never blocked** by an inexact or stale
plan (plan §3.10 "Draft and provisional exports must never be blocked only
because the plan is inexact"); a final artifact **retains blocking validation**
(plan §7.8). The import side accepts a set of activities to ingest as
``IMPORTED`` teaching activities; it validates every reference and every decimal
string and **never activates assignments** (plan §7.8). Hour values are exchanged
as canonical two-place decimal strings (plan §3.9): the output loads reuse the
computed :data:`~reparto_service.schemas.planning.HoursField`, while the import
input reuses the strict :data:`~reparto_service.core.decimals.HoursDecimal`
validator so a binary float or a >2-place value is rejected at the boundary.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from reparto_service.core.decimals import HoursDecimal
from reparto_service.enums import (
    ActivityType,
    PlanningExportMode,
    SubjectAllocationCategory,
    TeachingActivitySource,
    TeachingPlanStatus,
)
from reparto_service.schemas.planning import (
    HoursField,
    PlanBalance,
    PlanValidationReport,
)


# ── Export artifact ───────────────────────────────────────────────────────────


class PlanningExportActivity(BaseModel):
    """One live teaching activity as it appears in a planning artifact (plan §7.8)."""

    id: uuid.UUID = Field(description="Teaching activity ID.")
    subject_id: uuid.UUID = Field(description="Subject the activity teaches.")
    source: TeachingActivitySource = Field(description="Origin of the activity.")
    allocation_category: SubjectAllocationCategory = Field(
        description="MAIN or SECONDARY planning item (plan §5.6)."
    )
    activity_type: ActivityType = Field(
        description="Descriptive category (plan §20.17)."
    )
    group_weekly_hours_per_group: HoursField = Field(
        description="Actual weekly group hours delivered to each linked group."
    )
    teacher_weekly_hours_per_position: HoursField = Field(
        description="Actual weekly teacher-load hours per position."
    )
    required_teacher_count: int = Field(
        ge=1, description="Number of teacher positions the activity generates."
    )
    linked_group_count: int = Field(
        ge=0, description="Number of group-subject cells the activity links."
    )
    group_subject_ids: list[uuid.UUID] = Field(
        description="Group-subject cells this activity links (plan §5.7)."
    )
    group_load: HoursField = Field(
        description="group_weekly_hours_per_group × linked_group_count (plan §3.4)."
    )
    teacher_load: HoursField = Field(
        description="teacher_weekly_hours_per_position × required_teacher_count."
    )


class PlanningExportArtifact(BaseModel):
    """A draft/provisional/final planning artifact for one teaching plan (plan §7.8).

    ``balance`` and ``validations`` are always present so the artifact "clearly
    reports both balance states" (plan §7.8) and carries the findings. A draft or
    provisional artifact is produced regardless of ``is_final_exportable``; a
    final artifact is only produced when there is no blocking finding.
    """

    mode: PlanningExportMode = Field(description="Export strictness mode (plan §3.10).")
    generated_at: datetime = Field(description="Artifact generation timestamp (UTC).")
    assignment_process_id: uuid.UUID = Field(description="Owning process ID.")
    teaching_plan_id: uuid.UUID = Field(description="Teaching plan ID.")
    plan_status: TeachingPlanStatus = Field(description="Current plan status.")
    is_exact: bool = Field(
        description="True when both balances equal their targets (plan §3.10)."
    )
    is_final_exportable: bool = Field(
        description="True when no blocking validation is present (plan §3.10, §7.8)."
    )
    balance: PlanBalance = Field(description="Both independent balances (plan §3.1).")
    validations: PlanValidationReport = Field(
        description="Blocking/warning findings for the plan (plan §6.3/§6.4)."
    )
    activities: list[PlanningExportActivity] = Field(
        description="Live teaching activities, ordered by ID."
    )


# ── Import request/result ─────────────────────────────────────────────────────


class PlanningImportActivity(BaseModel):
    """One activity to ingest during a planning import (plan §7.8).

    Hour fields are typed :data:`HoursDecimal`, so a binary float or a value
    carrying more than two decimal places is rejected at the request boundary
    (plan §3.9); the referenced ``subject_id`` and ``group_subject_ids`` are
    validated against the target process by the controller before anything is
    written.
    """

    subject_id: uuid.UUID = Field(description="Subject this activity teaches.")
    allocation_category: SubjectAllocationCategory = Field(
        default=SubjectAllocationCategory.SECONDARY,
        description="MAIN or SECONDARY planning item (plan §5.6).",
    )
    activity_type: ActivityType = Field(
        default=ActivityType.ORDINARY,
        description="Descriptive category only (plan §20.17).",
    )
    group_weekly_hours_per_group: HoursDecimal = Field(
        description="Weekly group hours per linked group as a decimal string."
    )
    teacher_weekly_hours_per_position: HoursDecimal = Field(
        description="Weekly teacher-load hours per position as a decimal string."
    )
    required_teacher_count: int = Field(
        default=1, ge=1, description="Teacher positions the activity generates."
    )
    group_subject_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Group-subject cells this activity links (plan §5.7).",
    )
    notes: Optional[str] = Field(default=None, description="Free-form notes.")


class PlanningImportRequest(BaseModel):
    """Payload of a planning import (plan §7.8).

    Every referenced subject/cell and every decimal string is validated; no
    assignment is created or activated by an import (plan §7.8).
    """

    activities: list[PlanningImportActivity] = Field(
        default_factory=list,
        description="Activities to ingest as IMPORTED teaching activities.",
    )


class PlanningImportResult(BaseModel):
    """Result of a planning import (plan §7.8).

    Reports what was ingested plus the resulting balance and validation report,
    so the caller sees the (possibly still-unbalanced) plan state without the
    import ever being blocked.
    """

    imported_count: int = Field(ge=0, description="Activities ingested.")
    imported_activity_ids: list[uuid.UUID] = Field(
        description="IDs of the created IMPORTED activities, creation-ordered."
    )
    balance: PlanBalance = Field(description="Post-import balances (plan §3.1).")
    validations: PlanValidationReport = Field(
        description="Post-import blocking/warning findings (plan §6.3/§6.4)."
    )


__all__ = [
    "PlanningExportActivity",
    "PlanningExportArtifact",
    "PlanningImportActivity",
    "PlanningImportRequest",
    "PlanningImportResult",
]
