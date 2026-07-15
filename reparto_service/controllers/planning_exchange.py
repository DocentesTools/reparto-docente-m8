"""Planning import/export exchange controller (plan §3.10, §7.8).

The three-stage adaptation keeps the existing provisional/final export story
alive for the new intermediate planning stage, built entirely on the new
dual-balance calculation and validation services (never the retired
``SummaryService``):

* :meth:`~PlanningExchangeController.export_planning` renders a
  :class:`~reparto_service.schemas.exchange.PlanningExportArtifact` for a plan.
  In ``DRAFT``/``PROVISIONAL`` mode it is **never blocked** by an inexact,
  unbalanced or stale plan (plan §3.10) — the artifact simply carries the
  validation findings and both balance states. In ``FINAL`` mode it **retains
  blocking validation** (plan §7.8): a plan with any blocking finding is refused.
* :meth:`~PlanningExchangeController.import_planning` ingests a set of activities
  as ``IMPORTED`` teaching activities (plan §5.6). Every referenced subject/cell
  is validated against the target process and every hour is a validated decimal
  string (the schema rejects binary floats / >2-place values at the boundary,
  plan §3.9); the import **never creates or activates an assignment** (plan §7.8)
  and is not blocked by the plan being unbalanced.

Reference/link validation and the §5.6/§5.7 link-count policy are reused from
:class:`~reparto_service.controllers.teaching_activities.TeachingActivityController`
so an imported activity obeys exactly the same structural rules as a manually
created one. Balance recomputation / the balanced→unbalanced status transition an
import triggers (plan §20.14) stays with the dual-balance status-wiring task,
matching every prior activity-mutation task.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.controllers.teaching_activities import TeachingActivityController
from reparto_service.db_models.teaching_activities import (
    TeachingActivity,
    TeachingActivityGroup,
)
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import PlanningExportMode, TeachingActivitySource
from reparto_service.schemas.exchange import (
    PlanningExportActivity,
    PlanningExportArtifact,
    PlanningImportRequest,
    PlanningImportResult,
)
from reparto_service.services.calculations import PlanningCalculationService
from reparto_service.services.validations import PlanValidationService


class PlanningExchangeController(DomainController):
    """Draft/provisional/final planning export and validated planning import."""

    @staticmethod
    def export_planning(
        session: Session,
        process_id: uuid.UUID,
        mode: PlanningExportMode,
    ) -> PlanningExportArtifact:
        """Render a planning artifact for the process's plan (plan §3.10, §7.8).

        Draft and provisional exports are produced regardless of the plan's
        balance/validation state; a final export is refused (400) when the plan
        has any blocking finding.
        """
        DomainController.get_process_or_404(session, process_id)
        plan = PlanningExchangeController._plan_or_404(session, process_id)

        balance = PlanningCalculationService.compute_plan_balance(session, plan)
        validations = PlanValidationService.compute_plan_validations(session, plan)
        is_final_exportable = validations.blocking_count == 0

        if mode == PlanningExportMode.FINAL and not is_final_exportable:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Final planning export is blocked by "
                    f"{validations.blocking_count} blocking validation(s); "
                    "resolve them or export as draft/provisional (plan §7.8)."
                ),
            )

        return PlanningExportArtifact(
            mode=mode,
            generated_at=datetime.now(timezone.utc),
            assignment_process_id=process_id,
            teaching_plan_id=plan.id,
            plan_status=plan.status,
            is_exact=balance.is_exact,
            is_final_exportable=is_final_exportable,
            balance=balance,
            validations=validations,
            activities=PlanningExchangeController._export_activities(session, plan),
        )

    @staticmethod
    def import_planning(
        session: Session,
        process_id: uuid.UUID,
        payload: PlanningImportRequest,
        current_user: UserModel,
    ) -> PlanningImportResult:
        """Ingest activities as IMPORTED, validating references (plan §7.8).

        Works while the plan is unbalanced; validates every subject/cell against
        the target process and every hour string (the schema already rejected
        floats / >2-place values). No assignment is ever created or activated.
        """
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        plan = TeachingActivityController._require_mutable_plan(session, process_id)

        created: list[uuid.UUID] = []
        for item in payload.activities:
            subject = TeachingActivityController._get_subject_or_404(
                session, process_id, item.subject_id
            )
            link_ids = TeachingActivityController._validate_links(
                session, process_id, subject, item.group_subject_ids
            )
            activity = TeachingActivity(
                teaching_plan_id=plan.id,
                subject_id=subject.id,
                allocation_category=item.allocation_category,
                activity_type=item.activity_type,
                group_weekly_hours_per_group=float(item.group_weekly_hours_per_group),
                teacher_weekly_hours_per_position=float(
                    item.teacher_weekly_hours_per_position
                ),
                required_teacher_count=item.required_teacher_count,
                source=TeachingActivitySource.IMPORTED,
                notes=item.notes,
            )
            session.add(activity)
            for cell_id in link_ids:
                session.add(
                    TeachingActivityGroup(
                        teaching_activity_id=activity.id, group_subject_id=cell_id
                    )
                )
            PlanningExchangeController.record_audit_event(
                session,
                process_id=process_id,
                current_user=current_user,
                event_type="teaching_activity.imported",
                entity_type="teaching_activity",
                entity_id=activity.id,
                before=None,
                after=activity,
            )
            created.append(activity.id)

        session.commit()
        session.refresh(plan)
        return PlanningImportResult(
            imported_count=len(created),
            imported_activity_ids=created,
            balance=PlanningCalculationService.compute_plan_balance(session, plan),
            validations=PlanValidationService.compute_plan_validations(session, plan),
        )

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _plan_or_404(session: Session, process_id: uuid.UUID) -> TeachingPlan:
        """Return the process's single plan, or 404 when it has none."""
        plan = session.exec(
            select(TeachingPlan).where(TeachingPlan.assignment_process_id == process_id)
        ).first()
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No teaching plan for process {process_id}.",
            )
        return plan

    @staticmethod
    def _export_activities(
        session: Session, plan: TeachingPlan
    ) -> list[PlanningExportActivity]:
        """List the plan's live activities with per-activity loads, ID-ordered."""
        activities = session.exec(
            select(TeachingActivity)
            .where(TeachingActivity.teaching_plan_id == plan.id)
            .where(col(TeachingActivity.retired_at).is_(None))
            .order_by(col(TeachingActivity.id))
        ).all()
        exported: list[PlanningExportActivity] = []
        for activity in activities:
            link_ids = TeachingActivityController._link_ids(session, activity.id)
            exported.append(
                PlanningExportActivity(
                    id=activity.id,
                    subject_id=activity.subject_id,
                    source=activity.source,
                    allocation_category=activity.allocation_category,
                    activity_type=activity.activity_type,
                    group_weekly_hours_per_group=Decimal(
                        str(activity.group_weekly_hours_per_group)
                    ),
                    teacher_weekly_hours_per_position=Decimal(
                        str(activity.teacher_weekly_hours_per_position)
                    ),
                    required_teacher_count=activity.required_teacher_count,
                    linked_group_count=len(link_ids),
                    group_subject_ids=link_ids,
                    group_load=PlanningCalculationService.compute_activity_group_load(
                        activity, len(link_ids)
                    ),
                    teacher_load=(
                        PlanningCalculationService.compute_activity_teacher_load(
                            activity
                        )
                    ),
                )
            )
        return exported


__all__ = ["PlanningExchangeController"]
