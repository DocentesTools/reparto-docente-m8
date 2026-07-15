"""Group-subject routes (nested under an assignment process).

Exposes the plan §7.2 CRUD surface plus the ``bulk-preview``/``bulk-apply``
operations for the intermediate group-subject matrix.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
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

router = APIRouter(
    prefix="/assignment-processes/{process_id}/group-subjects",
    tags=["group-subjects"],
)


@router.get("/", response_model=GroupSubjectsPublic)
def list_group_subjects(
    session: SessionDep, process_id: uuid.UUID
) -> GroupSubjectsPublic:
    return GroupSubjectController.list_group_subjects(session, process_id)


@router.post("/", response_model=GroupSubjectPublic, status_code=201)
def create_group_subject(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    group_subject_in: GroupSubjectCreate,
) -> GroupSubjectPublic:
    GroupSubjectController.require_process_writer(session, current_user, process_id)
    return GroupSubjectController.create_group_subject(
        session, process_id, group_subject_in, current_user
    )


@router.post("/bulk-preview", response_model=GroupSubjectBulkPreview)
def bulk_preview_group_subjects(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    request: GroupSubjectBulkRequest,
) -> GroupSubjectBulkPreview:
    GroupSubjectController.require_process_writer(session, current_user, process_id)
    return GroupSubjectController.bulk_preview(session, process_id, request)


@router.post("/bulk-apply", response_model=GroupSubjectBulkResult)
def bulk_apply_group_subjects(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    request: GroupSubjectBulkApplyRequest,
) -> GroupSubjectBulkResult:
    GroupSubjectController.require_process_writer(session, current_user, process_id)
    return GroupSubjectController.bulk_apply(session, process_id, request, current_user)


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
    current_user: CurrentUser,
    process_id: uuid.UUID,
    group_subject_id: uuid.UUID,
    group_subject_in: GroupSubjectUpdate,
) -> GroupSubjectPublic:
    GroupSubjectController.require_process_writer(session, current_user, process_id)
    return GroupSubjectController.update_group_subject(
        session, process_id, group_subject_id, group_subject_in, current_user
    )


@router.delete("/{group_subject_id}", response_model=GroupSubjectPublic)
def delete_group_subject(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    group_subject_id: uuid.UUID,
) -> GroupSubjectPublic:
    GroupSubjectController.require_process_writer(session, current_user, process_id)
    return GroupSubjectController.delete_group_subject(
        session, process_id, group_subject_id, current_user
    )
