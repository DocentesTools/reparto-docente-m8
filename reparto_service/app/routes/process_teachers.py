"""ProcessTeacher routes (nested under an assignment process).

The reads here are department-head reads (remediation `W5.3`).
``ProcessTeacherPublic`` carries every participant's base, extra and target
weekly hours together with ``extra_hours_reason`` — the head's written
justification, which :mod:`reparto_service.services.sse` redacts from the
teacher tier even on an event about the viewer themselves. A payload the
stream refuses to a teacher cannot be one a plain ``GET`` hands them, so the
list and the detail sit at the administrator floor and the teacher tier reads
its own row from ``GET /assignment-processes/{process_id}/lan/me``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from reparto_service.app.deps import CurrentAdmin, SessionDep, require_visible_process
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
    # Read scope (plan §21.4): every route under this process — read and
    # mutation alike — is refused with 404 when the process lies outside the
    # caller's departments.
    dependencies=[Depends(require_visible_process)],
)


@router.get("/", response_model=ProcessTeachersPublic)
def list_process_teachers(
    session: SessionDep, current_user: CurrentAdmin, process_id: uuid.UUID
) -> ProcessTeachersPublic:
    """Expose the roster's hours and extra-hours reasons only to an administrator."""
    return ProcessTeacherController.list_process_teachers(session, process_id)


@router.post("/", response_model=ProcessTeacherPublic, status_code=201)
def create_process_teacher(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    process_teacher_in: ProcessTeacherCreate,
) -> ProcessTeacherPublic:
    return ProcessTeacherController.create_process_teacher(
        session, process_id, current_user, process_teacher_in
    )


@router.get("/{process_teacher_id}", response_model=ProcessTeacherPublic)
def get_process_teacher(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    process_teacher_id: uuid.UUID,
) -> ProcessTeacherPublic:
    """Same payload as the list, one row at a time — same floor."""
    return ProcessTeacherController.get_process_teacher(
        session, process_id, process_teacher_id
    )


@router.patch("/{process_teacher_id}", response_model=ProcessTeacherPublic)
def update_process_teacher(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    process_teacher_id: uuid.UUID,
    process_teacher_in: ProcessTeacherUpdate,
) -> ProcessTeacherPublic:
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
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    process_teacher_id: uuid.UUID,
    payload: ProcessTeacherExtraHoursUpdate,
) -> ProcessTeacherPublic:
    """Dedicated audited extra-hours action (plan §7.6).

    Keeps authorized-overload changes off the generic PATCH so they always
    carry a reason and an audit event.
    """
    return ProcessTeacherController.update_extra_hours(
        session, process_id, process_teacher_id, payload, current_user
    )


@router.delete("/{process_teacher_id}", response_model=ProcessTeacherPublic)
def delete_process_teacher(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    process_teacher_id: uuid.UUID,
) -> ProcessTeacherPublic:
    return ProcessTeacherController.delete_process_teacher(
        session, process_id, process_teacher_id, current_user
    )
