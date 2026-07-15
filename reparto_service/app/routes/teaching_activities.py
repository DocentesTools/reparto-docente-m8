"""Teaching-activity routes (nested under an assignment process).

Exposes the plan §7.4 CRUD surface for department teaching-plan activities.
Every mutation is writer-gated; the owning teaching plan is resolved from the
process by the controller.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
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
)


@router.get("/", response_model=TeachingActivitiesPublic)
def list_teaching_activities(
    session: SessionDep, process_id: uuid.UUID
) -> TeachingActivitiesPublic:
    return TeachingActivityController.list_teaching_activities(session, process_id)


@router.post("/", response_model=TeachingActivityPublic, status_code=201)
def create_teaching_activity(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    activity_in: TeachingActivityCreate,
) -> TeachingActivityPublic:
    TeachingActivityController.require_process_writer(session, current_user, process_id)
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
    current_user: CurrentUser,
    process_id: uuid.UUID,
    activity_id: uuid.UUID,
    activity_in: TeachingActivityUpdate,
) -> TeachingActivityPublic:
    TeachingActivityController.require_process_writer(session, current_user, process_id)
    return TeachingActivityController.update_teaching_activity(
        session, process_id, activity_id, activity_in, current_user
    )


@router.delete("/{activity_id}", response_model=TeachingActivityPublic)
def delete_teaching_activity(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    activity_id: uuid.UUID,
) -> TeachingActivityPublic:
    TeachingActivityController.require_process_writer(session, current_user, process_id)
    return TeachingActivityController.delete_teaching_activity(
        session, process_id, activity_id, current_user
    )
