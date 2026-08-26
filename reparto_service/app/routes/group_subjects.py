"""Group-subject routes (nested under an assignment process).

Exposes the plan §7.2 create/read/update/retire surface plus ``bulk-preview``/``bulk-apply``
operations for the intermediate group-subject matrix.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from reparto_service.app.deps import CurrentAdmin, SessionDep, require_visible_process
from reparto_service.controllers.group_subjects import GroupSubjectController
from reparto_service.db_models.group_subjects import (
    GroupSubjectBulkApplyRequest,
    GroupSubjectBulkPreview,
    GroupSubjectBulkRequest,
    GroupSubjectBulkResult,
    GroupSubjectCreate,
    GroupSubjectPublic,
    GroupSubjectsPublic,
    GroupSubjectUpdate,
)
from reparto_service.db_models.teaching_activities import (
    MainActivitySyncApplyRequest,
    MainActivitySyncPreview,
    MainActivitySyncResult,
)

router = APIRouter(
    prefix="/assignment-processes/{process_id}/group-subjects",
    tags=["group-subjects"],
    # Read scope (plan §21.4): every route under this process — read and
    # mutation alike — is refused with 404 when the process lies outside the
    # caller's departments.
    dependencies=[Depends(require_visible_process)],
)


@router.get("/", response_model=GroupSubjectsPublic)
def list_group_subjects(
    session: SessionDep, process_id: uuid.UUID
) -> GroupSubjectsPublic:
    return GroupSubjectController.list_group_subjects(session, process_id)


@router.post("/", response_model=GroupSubjectPublic, status_code=201)
def create_group_subject(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    group_subject_in: GroupSubjectCreate,
) -> GroupSubjectPublic:
    return GroupSubjectController.create_group_subject(
        session, process_id, group_subject_in, current_user
    )


@router.post("/bulk-preview", response_model=GroupSubjectBulkPreview)
def bulk_preview_group_subjects(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    request: GroupSubjectBulkRequest,
) -> GroupSubjectBulkPreview:
    return GroupSubjectController.bulk_preview(session, process_id, request)


@router.post("/bulk-apply", response_model=GroupSubjectBulkResult)
def bulk_apply_group_subjects(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    request: GroupSubjectBulkApplyRequest,
) -> GroupSubjectBulkResult:
    return GroupSubjectController.bulk_apply(session, process_id, request, current_user)


@router.post("/{group_subject_id}/sync-preview", response_model=MainActivitySyncPreview)
def preview_group_subject_sync(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    group_subject_id: uuid.UUID,
) -> MainActivitySyncPreview:
    return GroupSubjectController.sync_preview(session, process_id, group_subject_id)


@router.post("/{group_subject_id}/sync-apply", response_model=MainActivitySyncResult)
def apply_group_subject_sync(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    group_subject_id: uuid.UUID,
    request: MainActivitySyncApplyRequest,
) -> MainActivitySyncResult:
    return GroupSubjectController.sync_apply(
        session, process_id, group_subject_id, request, current_user
    )


@router.get("/{group_subject_id}", response_model=GroupSubjectPublic)
def get_group_subject(
    session: SessionDep, process_id: uuid.UUID, group_subject_id: uuid.UUID
) -> GroupSubjectPublic:
    return GroupSubjectController.get_group_subject(
        session, process_id, group_subject_id
    )


@router.patch("/{group_subject_id}", response_model=GroupSubjectPublic)
def update_group_subject(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    group_subject_id: uuid.UUID,
    group_subject_in: GroupSubjectUpdate,
) -> GroupSubjectPublic:
    return GroupSubjectController.update_group_subject(
        session, process_id, group_subject_id, group_subject_in, current_user
    )


@router.post("/{group_subject_id}/retire", response_model=GroupSubjectPublic)
def retire_group_subject(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    group_subject_id: uuid.UUID,
) -> GroupSubjectPublic:
    return GroupSubjectController.retire_group_subject(
        session, process_id, group_subject_id, current_user
    )
