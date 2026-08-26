"""Allocation-revision routes (nested under an assignment process, plan §7.1).

Revisions are immutable: only list, current and create are exposed — there is
deliberately no update or delete endpoint.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from reparto_service.app.deps import CurrentAdmin, SessionDep, require_visible_process
from reparto_service.controllers.department_hour_allocation_revisions import (
    DepartmentHourAllocationRevisionController,
)
from reparto_service.db_models.department_hour_allocation_revisions import (
    DepartmentHourAllocationRevisionCreate,
    DepartmentHourAllocationRevisionPublic,
    DepartmentHourAllocationRevisionsPublic,
)

router = APIRouter(
    prefix="/assignment-processes/{process_id}/allocation-revisions",
    tags=["allocation-revisions"],
    # Read scope (plan §21.4): every route under this process — read and
    # mutation alike — is refused with 404 when the process lies outside the
    # caller's departments.
    dependencies=[Depends(require_visible_process)],
)


@router.get("/", response_model=DepartmentHourAllocationRevisionsPublic)
def list_allocation_revisions(
    session: SessionDep, process_id: uuid.UUID
) -> DepartmentHourAllocationRevisionsPublic:
    return DepartmentHourAllocationRevisionController.list_revisions(
        session, process_id
    )


@router.get("/current", response_model=DepartmentHourAllocationRevisionPublic)
def get_current_allocation_revision(
    session: SessionDep, process_id: uuid.UUID
) -> DepartmentHourAllocationRevisionPublic:
    return DepartmentHourAllocationRevisionController.get_current_revision(
        session, process_id
    )


@router.post(
    "/", response_model=DepartmentHourAllocationRevisionPublic, status_code=201
)
def create_allocation_revision(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    revision_in: DepartmentHourAllocationRevisionCreate,
) -> DepartmentHourAllocationRevisionPublic:
    return DepartmentHourAllocationRevisionController.create_revision(
        session, process_id, current_user, revision_in
    )
