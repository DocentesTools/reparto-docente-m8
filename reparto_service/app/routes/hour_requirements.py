"""HourRequirement routes (nested under an assignment process).

Requirement slots are generated, never manually mutated (plan §5.9, §20.12): the
``GET`` endpoints are read-only, and the plan §7.5 ``generation-preview`` /
``generate`` actions produce and retire slots through the generation flow. The
reconciliation-preview / reconcile endpoints (plan §7.5) are a later task.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
from reparto_service.controllers.hour_requirements import HourRequirementController
from reparto_service.db_models.hour_requirements import (
    HourRequirementPublic,
    HourRequirementsPublic,
    RequirementGenerationPreview,
    RequirementGenerationResult,
)

router = APIRouter(
    prefix="/assignment-processes/{process_id}/requirements",
    tags=["hour-requirements"],
)


@router.get("/", response_model=HourRequirementsPublic)
def list_requirements(
    session: SessionDep, process_id: uuid.UUID
) -> HourRequirementsPublic:
    return HourRequirementController.list_requirements(session, process_id)


@router.post("/generation-preview", response_model=RequirementGenerationPreview)
def preview_requirement_generation(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
) -> RequirementGenerationPreview:
    HourRequirementController.require_process_writer(session, current_user, process_id)
    return HourRequirementController.generation_preview(session, process_id)


@router.post("/generate", response_model=RequirementGenerationResult)
def generate_requirements(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
) -> RequirementGenerationResult:
    HourRequirementController.require_process_writer(session, current_user, process_id)
    return HourRequirementController.generate(session, process_id, current_user)


@router.get("/{requirement_id}", response_model=HourRequirementPublic)
def get_requirement(
    session: SessionDep, process_id: uuid.UUID, requirement_id: uuid.UUID
) -> HourRequirementPublic:
    return HourRequirementController.get_requirement(
        session, process_id, requirement_id
    )
