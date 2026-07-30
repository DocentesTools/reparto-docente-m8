"""Teaching-plan routes (nested under an assignment process, plan §7.3).

This slice exposes the plan's ownership surface, the read-only planning
``summary`` and ``validations`` endpoints, the ``materialize-main`` action, and
the administrator-only feasibility evaluation/witness operations (plan §7.3,
§20.20). The ``lock``/``unlock`` endpoints remain in their later lifecycle task.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
from reparto_service.controllers.teaching_activities import TeachingActivityController
from reparto_service.controllers.teaching_plans import TeachingPlanController
from reparto_service.db_models.teaching_activities import MainMaterializationResult
from reparto_service.db_models.feasibility_witnesses import (
    FeasibilityEvaluationPublic,
    FeasibilityWitnessPublic,
)
from reparto_service.db_models.teaching_plans import TeachingPlanPublic
from reparto_service.schemas.planning import PlanBalance, PlanValidationReport

router = APIRouter(
    prefix="/assignment-processes/{process_id}/teaching-plan",
    tags=["teaching-plan"],
)


@router.get("", response_model=TeachingPlanPublic)
def get_teaching_plan(session: SessionDep, process_id: uuid.UUID) -> TeachingPlanPublic:
    return TeachingPlanController.get_plan(session, process_id)


@router.get("/summary", response_model=PlanBalance)
def get_teaching_plan_summary(
    session: SessionDep, process_id: uuid.UUID
) -> PlanBalance:
    return TeachingPlanController.get_summary(session, process_id)


@router.get("/validations", response_model=PlanValidationReport)
def get_teaching_plan_validations(
    session: SessionDep, process_id: uuid.UUID
) -> PlanValidationReport:
    return TeachingPlanController.get_validations(session, process_id)


@router.post(
    "/feasibility/evaluate",
    response_model=FeasibilityEvaluationPublic,
)
def evaluate_teaching_plan_feasibility(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
) -> FeasibilityEvaluationPublic:
    """Run the full bounded solver only for an administrator."""

    TeachingPlanController.require_admin(current_user)
    return TeachingPlanController.evaluate_feasibility(session, process_id)


@router.get(
    "/feasibility/witness",
    response_model=FeasibilityWitnessPublic,
)
def get_teaching_plan_feasibility_witness(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
) -> FeasibilityWitnessPublic:
    """Expose the provisional mapping only to an administrator."""

    TeachingPlanController.require_admin(current_user)
    return TeachingPlanController.get_feasibility_witness(session, process_id)


@router.post("", response_model=TeachingPlanPublic, status_code=201)
def create_teaching_plan(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
) -> TeachingPlanPublic:
    TeachingPlanController.require_process_writer(session, current_user, process_id)
    return TeachingPlanController.create_plan(session, process_id, current_user)


@router.post("/materialize-main", response_model=MainMaterializationResult)
def materialize_main_activities(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
) -> MainMaterializationResult:
    TeachingActivityController.require_process_writer(session, current_user, process_id)
    return TeachingActivityController.materialize_main(
        session, process_id, current_user
    )
