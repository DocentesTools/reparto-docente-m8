"""Subject routes (nested under an assignment process)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
from reparto_service.controllers.subjects import SubjectController
from reparto_service.db_models.subjects import (
    SubjectCreate,
    SubjectPublic,
    SubjectsPublic,
    SubjectUpdate,
)

router = APIRouter(
    prefix="/assignment-processes/{process_id}/subjects",
    tags=["subjects"],
)


@router.get("/", response_model=SubjectsPublic)
def list_subjects(session: SessionDep, process_id: uuid.UUID) -> SubjectsPublic:
    return SubjectController.list_subjects(session, process_id)


@router.post("/", response_model=SubjectPublic, status_code=201)
def create_subject(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    subject_in: SubjectCreate,
) -> SubjectPublic:
    SubjectController.require_process_writer(session, current_user, process_id)
    return SubjectController.create_subject(
        session, process_id, subject_in, current_user
    )


@router.get("/{subject_id}", response_model=SubjectPublic)
def get_subject(
    session: SessionDep, process_id: uuid.UUID, subject_id: uuid.UUID
) -> SubjectPublic:
    return SubjectController.get_subject(session, process_id, subject_id)


@router.patch("/{subject_id}", response_model=SubjectPublic)
def update_subject(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    subject_id: uuid.UUID,
    subject_in: SubjectUpdate,
) -> SubjectPublic:
    SubjectController.require_process_writer(session, current_user, process_id)
    return SubjectController.update_subject(
        session, process_id, subject_id, subject_in, current_user
    )


@router.delete("/{subject_id}", response_model=SubjectPublic)
def delete_subject(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    subject_id: uuid.UUID,
) -> SubjectPublic:
    SubjectController.require_process_writer(session, current_user, process_id)
    return SubjectController.delete_subject(
        session, process_id, subject_id, current_user
    )
