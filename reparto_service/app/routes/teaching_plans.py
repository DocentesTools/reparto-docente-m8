"""Teaching-plan routes (nested under an assignment process, plan §7.3).

This slice exposes the plan's ownership surface plus the read-only planning
``validations`` endpoint (plan §7.3). The ``materialize-main``, ``summary``,
``lock``/``unlock`` and ``feasibility`` endpoints (plan §7.3) depend on the
activity, generation and feasibility services introduced by their dedicated
later tasks and are added there.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
from reparto_service.controllers.teaching_plans import TeachingPlanController
from reparto_service.db_models.teaching_plans import TeachingPlanPublic
from reparto_service.schemas.planning import PlanValidationReport

router = APIRouter(
    prefix="/assignment-processes/{process_id}/teaching-plan",
    tags=["teaching-plan"],
)


@router.get("", response_model=TeachingPlanPublic)
def get_teaching_plan(session: SessionDep, process_id: uuid.UUID) -> TeachingPlanPublic:
    return TeachingPlanController.get_plan(session, process_id)


@router.get("/validations", response_model=PlanValidationReport)
def get_teaching_plan_validations(
    session: SessionDep, process_id: uuid.UUID
) -> PlanValidationReport:
    return TeachingPlanController.get_validations(session, process_id)


@router.post("", response_model=TeachingPlanPublic, status_code=201)
def create_teaching_plan(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
) -> TeachingPlanPublic:
    TeachingPlanController.require_process_writer(session, current_user, process_id)
    return TeachingPlanController.create_plan(session, process_id, current_user)
