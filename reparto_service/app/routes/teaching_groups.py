"""TeachingGroup routes (nested under an assignment process)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from reparto_service.app.deps import CurrentUser, SessionDep, require_visible_process
from reparto_service.controllers.teaching_groups import TeachingGroupController
from reparto_service.db_models.teaching_groups import (
    TeachingGroupCreate,
    TeachingGroupBulkCreate,
    TeachingGroupPublic,
    TeachingGroupsPublic,
    TeachingGroupUpdate,
)

router = APIRouter(
    prefix="/assignment-processes/{process_id}/groups",
    tags=["teaching-groups"],
    # Read scope (plan §21.4): every route under this process — read and
    # mutation alike — is refused with 404 when the process lies outside the
    # caller's departments.
    dependencies=[Depends(require_visible_process)],
)


@router.post("/bulk", response_model=TeachingGroupsPublic, status_code=201)
def bulk_create_groups(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    bulk_in: TeachingGroupBulkCreate,
) -> TeachingGroupsPublic:
    """Atomically create an inclusive classroom group range."""
    TeachingGroupController.require_department_head(current_user)
    return TeachingGroupController.bulk_create(
        session, process_id, bulk_in, current_user
    )


@router.get("/", response_model=TeachingGroupsPublic)
def list_groups(session: SessionDep, process_id: uuid.UUID) -> TeachingGroupsPublic:
    return TeachingGroupController.list_groups(session, process_id)


@router.post("/", response_model=TeachingGroupPublic, status_code=201)
def create_group(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    group_in: TeachingGroupCreate,
) -> TeachingGroupPublic:
    TeachingGroupController.require_department_head(current_user)
    return TeachingGroupController.create_group(
        session, process_id, group_in, current_user
    )


@router.get("/{group_id}", response_model=TeachingGroupPublic)
def get_group(
    session: SessionDep, process_id: uuid.UUID, group_id: uuid.UUID
) -> TeachingGroupPublic:
    return TeachingGroupController.get_group(session, process_id, group_id)


@router.patch("/{group_id}", response_model=TeachingGroupPublic)
def update_group(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    group_id: uuid.UUID,
    group_in: TeachingGroupUpdate,
) -> TeachingGroupPublic:
    TeachingGroupController.require_department_head(current_user)
    return TeachingGroupController.update_group(
        session, process_id, group_id, group_in, current_user
    )


@router.delete("/{group_id}", response_model=TeachingGroupPublic)
def delete_group(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    group_id: uuid.UUID,
) -> TeachingGroupPublic:
    TeachingGroupController.require_department_head(current_user)
    return TeachingGroupController.delete_group(
        session, process_id, group_id, current_user
    )
