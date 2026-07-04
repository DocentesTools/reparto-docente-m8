"""HourRequirement routes (nested under an assignment process)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
from reparto_service.controllers.hour_requirements import HourRequirementController
from reparto_service.db_models.hour_requirements import (
    HourRequirementCreate,
    HourRequirementPublic,
    HourRequirementsPublic,
    HourRequirementUpdate,
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


@router.post("/", response_model=HourRequirementPublic, status_code=201)
def create_requirement(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    requirement_in: HourRequirementCreate,
) -> HourRequirementPublic:
    HourRequirementController.require_process_writer(session, current_user, process_id)
    return HourRequirementController.create_requirement(
        session, process_id, requirement_in
    )


@router.get("/{requirement_id}", response_model=HourRequirementPublic)
def get_requirement(
    session: SessionDep, process_id: uuid.UUID, requirement_id: uuid.UUID
) -> HourRequirementPublic:
    return HourRequirementController.get_requirement(
        session, process_id, requirement_id
    )


@router.patch("/{requirement_id}", response_model=HourRequirementPublic)
def update_requirement(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    requirement_id: uuid.UUID,
    requirement_in: HourRequirementUpdate,
) -> HourRequirementPublic:
    HourRequirementController.require_process_writer(session, current_user, process_id)
    return HourRequirementController.update_requirement(
        session, process_id, requirement_id, requirement_in
    )


@router.delete("/{requirement_id}", response_model=HourRequirementPublic)
def delete_requirement(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    requirement_id: uuid.UUID,
) -> HourRequirementPublic:
    HourRequirementController.require_process_writer(session, current_user, process_id)
    return HourRequirementController.delete_requirement(
        session, process_id, requirement_id
    )
