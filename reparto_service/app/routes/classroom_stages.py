"""Authenticated global classroom-stage routes."""

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
from reparto_service.controllers.base import DomainController
from reparto_service.controllers.classroom_stages import ClassroomStageController
from reparto_service.db_models.classroom_stages import (
    ClassroomStageCreate,
    ClassroomStagePublic,
    ClassroomStagesPublic,
    ClassroomStageUpdate,
)

router = APIRouter(prefix="/classroom-stages", tags=["classroom-stages"])


@router.get("/", response_model=ClassroomStagesPublic)
def list_stages(
    session: SessionDep, current_user: CurrentUser
) -> ClassroomStagesPublic:
    """List global stages for authenticated users."""
    return ClassroomStageController.list_stages(session)


@router.get("/{stage_id}", response_model=ClassroomStagePublic)
def get_stage(
    session: SessionDep, current_user: CurrentUser, stage_id: uuid.UUID
) -> ClassroomStagePublic:
    """Read a global stage."""
    return ClassroomStageController.get_stage(session, stage_id)


@router.post("/", response_model=ClassroomStagePublic, status_code=201)
def create_stage(
    session: SessionDep,
    current_user: CurrentUser,
    stage_in: ClassroomStageCreate,
) -> ClassroomStagePublic:
    """Create a stage as an administrator."""
    DomainController.require_admin(current_user)
    return ClassroomStageController.create_stage(session, stage_in)


@router.patch("/{stage_id}", response_model=ClassroomStagePublic)
def update_stage(
    session: SessionDep,
    current_user: CurrentUser,
    stage_id: uuid.UUID,
    stage_in: ClassroomStageUpdate,
) -> ClassroomStagePublic:
    """Update a stage as an administrator."""
    DomainController.require_admin(current_user)
    return ClassroomStageController.update_stage(session, stage_id, stage_in)


@router.delete("/{stage_id}", response_model=ClassroomStagePublic)
def delete_stage(
    session: SessionDep, current_user: CurrentUser, stage_id: uuid.UUID
) -> ClassroomStagePublic:
    """Delete an unused stage as an administrator."""
    DomainController.require_admin(current_user)
    return ClassroomStageController.delete_stage(session, stage_id)
