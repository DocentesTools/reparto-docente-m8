"""HourRequirement routes (nested under an assignment process).

Read-only: requirement slots are generated, never manually mutated (plan §5.9,
§20.12). The generation / reconciliation endpoints (plan §7.5) are a later task.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import SessionDep
from reparto_service.controllers.hour_requirements import HourRequirementController
from reparto_service.db_models.hour_requirements import (
    HourRequirementPublic,
    HourRequirementsPublic,
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


@router.get("/{requirement_id}", response_model=HourRequirementPublic)
def get_requirement(
    session: SessionDep, process_id: uuid.UUID, requirement_id: uuid.UUID
) -> HourRequirementPublic:
    return HourRequirementController.get_requirement(
        session, process_id, requirement_id
    )
