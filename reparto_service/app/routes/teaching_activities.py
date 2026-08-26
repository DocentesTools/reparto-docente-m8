"""Teaching-activity routes (nested under an assignment process).

Exposes the plan §7.4 create/read/update plus guarded-retirement surface.
Every mutation is writer-gated; the owning teaching plan is resolved from the
process by the controller.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from reparto_service.app.deps import CurrentAdmin, SessionDep, require_visible_process
from reparto_service.controllers.teaching_activities import TeachingActivityController
from reparto_service.db_models.teaching_activities import (
    TeachingActivitiesPublic,
    TeachingActivityCreate,
    TeachingActivityPublic,
    TeachingActivityUpdate,
)

router = APIRouter(
    prefix="/assignment-processes/{process_id}/teaching-activities",
    tags=["teaching-activities"],
    # Read scope (plan §21.4): every route under this process — read and
    # mutation alike — is refused with 404 when the process lies outside the
    # caller's departments.
    dependencies=[Depends(require_visible_process)],
)


@router.get("/", response_model=TeachingActivitiesPublic)
def list_teaching_activities(
    session: SessionDep, process_id: uuid.UUID
) -> TeachingActivitiesPublic:
    return TeachingActivityController.list_teaching_activities(session, process_id)


@router.post("/", response_model=TeachingActivityPublic, status_code=201)
def create_teaching_activity(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    activity_in: TeachingActivityCreate,
) -> TeachingActivityPublic:
    return TeachingActivityController.create_teaching_activity(
        session, process_id, activity_in, current_user
    )


@router.get("/{activity_id}", response_model=TeachingActivityPublic)
def get_teaching_activity(
    session: SessionDep, process_id: uuid.UUID, activity_id: uuid.UUID
) -> TeachingActivityPublic:
    return TeachingActivityController.get_teaching_activity(
        session, process_id, activity_id
    )


@router.patch("/{activity_id}", response_model=TeachingActivityPublic)
def update_teaching_activity(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    activity_id: uuid.UUID,
    activity_in: TeachingActivityUpdate,
) -> TeachingActivityPublic:
    return TeachingActivityController.update_teaching_activity(
        session, process_id, activity_id, activity_in, current_user
    )


@router.post("/{activity_id}/retire", response_model=TeachingActivityPublic)
def retire_teaching_activity(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    activity_id: uuid.UUID,
) -> TeachingActivityPublic:
    return TeachingActivityController.retire_teaching_activity(
        session, process_id, activity_id, current_user
    )
