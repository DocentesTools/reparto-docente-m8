"""ProcessTeacher routes (nested under an assignment process)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
from reparto_service.controllers.process_teachers import ProcessTeacherController
from reparto_service.db_models.process_teachers import (
    ProcessTeacherCreate,
    ProcessTeacherExtraHoursUpdate,
    ProcessTeacherPublic,
    ProcessTeachersPublic,
    ProcessTeacherUpdate,
)

router = APIRouter(
    prefix="/assignment-processes/{process_id}/teachers",
    tags=["process-teachers"],
)


@router.get("/", response_model=ProcessTeachersPublic)
def list_process_teachers(
    session: SessionDep, process_id: uuid.UUID
) -> ProcessTeachersPublic:
    return ProcessTeacherController.list_process_teachers(session, process_id)


@router.post("/", response_model=ProcessTeacherPublic, status_code=201)
def create_process_teacher(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    process_teacher_in: ProcessTeacherCreate,
) -> ProcessTeacherPublic:
    ProcessTeacherController.require_process_writer(session, current_user, process_id)
    return ProcessTeacherController.create_process_teacher(
        session, process_id, current_user, process_teacher_in
    )


@router.get("/{process_teacher_id}", response_model=ProcessTeacherPublic)
def get_process_teacher(
    session: SessionDep, process_id: uuid.UUID, process_teacher_id: uuid.UUID
) -> ProcessTeacherPublic:
    return ProcessTeacherController.get_process_teacher(
        session, process_id, process_teacher_id
    )


@router.patch("/{process_teacher_id}", response_model=ProcessTeacherPublic)
def update_process_teacher(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    process_teacher_id: uuid.UUID,
    process_teacher_in: ProcessTeacherUpdate,
) -> ProcessTeacherPublic:
    ProcessTeacherController.require_process_writer(session, current_user, process_id)
    return ProcessTeacherController.update_process_teacher(
        session,
        process_id,
        process_teacher_id,
        process_teacher_in,
        current_user,
    )


@router.post("/{process_teacher_id}/extra-hours", response_model=ProcessTeacherPublic)
def update_process_teacher_extra_hours(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    process_teacher_id: uuid.UUID,
    payload: ProcessTeacherExtraHoursUpdate,
) -> ProcessTeacherPublic:
    """Dedicated audited extra-hours action (plan §7.6).

    Keeps authorized-overload changes off the generic PATCH so they always
    carry a reason and an audit event.
    """
    ProcessTeacherController.require_process_writer(session, current_user, process_id)
    return ProcessTeacherController.update_extra_hours(
        session, process_id, process_teacher_id, payload, current_user
    )


@router.delete("/{process_teacher_id}", response_model=ProcessTeacherPublic)
def delete_process_teacher(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    process_teacher_id: uuid.UUID,
) -> ProcessTeacherPublic:
    ProcessTeacherController.require_process_writer(session, current_user, process_id)
    return ProcessTeacherController.delete_process_teacher(
        session, process_id, process_teacher_id, current_user
    )
