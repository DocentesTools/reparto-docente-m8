"""Teaching-plan routes (nested under an assignment process, plan §7.3).

This slice exposes the plan's ownership surface, the read-only planning
``summary`` and ``validations`` endpoints, the ``materialize-main`` action, and
the administrator-only feasibility evaluation/witness/diagnostics operations
(plan §7.3, §20.20), plus feasibility-gated lock/unlock lifecycle actions.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from reparto_service.app.deps import CurrentAdmin, SessionDep, require_visible_process
from reparto_service.controllers.teaching_activities import TeachingActivityController
from reparto_service.controllers.teaching_plans import TeachingPlanController
from reparto_service.db_models.teaching_activities import MainMaterializationResult
from reparto_service.db_models.feasibility_witnesses import (
    FeasibilityDiagnosticsPublic,
    FeasibilityEvaluationPublic,
    FeasibilityWitnessPublic,
)
from reparto_service.db_models.teaching_plans import TeachingPlanPublic
from reparto_service.schemas.planning import PlanBalance, PlanValidationReport

router = APIRouter(
    prefix="/assignment-processes/{process_id}/teaching-plan",
    tags=["teaching-plan"],
    # Read scope (plan §21.4): every route under this process — read and
    # mutation alike — is refused with 404 when the process lies outside the
    # caller's departments.
    dependencies=[Depends(require_visible_process)],
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
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
) -> FeasibilityEvaluationPublic:
    """Run the full bounded solver only for an administrator."""

    return TeachingPlanController.evaluate_feasibility(session, process_id)


@router.get(
    "/feasibility/witness",
    response_model=FeasibilityWitnessPublic,
)
def get_teaching_plan_feasibility_witness(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
) -> FeasibilityWitnessPublic:
    """Expose the provisional mapping only to an administrator."""

    return TeachingPlanController.get_feasibility_witness(session, process_id)


@router.get(
    "/feasibility/diagnostics",
    response_model=FeasibilityDiagnosticsPublic,
)
def get_teaching_plan_feasibility_diagnostics(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
) -> FeasibilityDiagnosticsPublic:
    """Expose the latest evaluation's findings only to an administrator."""

    return TeachingPlanController.get_feasibility_diagnostics(session, process_id)


@router.post("", response_model=TeachingPlanPublic, status_code=201)
def create_teaching_plan(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
) -> TeachingPlanPublic:
    return TeachingPlanController.create_plan(session, process_id, current_user)


@router.post("/lock", response_model=TeachingPlanPublic)
def lock_teaching_plan(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
) -> TeachingPlanPublic:
    return TeachingPlanController.lock_plan(session, process_id, current_user)


@router.post("/unlock", response_model=TeachingPlanPublic)
def unlock_teaching_plan(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
) -> TeachingPlanPublic:
    return TeachingPlanController.unlock_plan(session, process_id, current_user)


@router.post("/materialize-main", response_model=MainMaterializationResult)
def materialize_main_activities(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
) -> MainMaterializationResult:
    return TeachingActivityController.materialize_main(
        session, process_id, current_user
    )
