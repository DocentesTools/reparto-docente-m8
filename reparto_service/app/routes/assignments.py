"""Assignment routes (nested under an assignment process)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from reparto_service.app.deps import (
    CurrentAdmin,
    CurrentWriter,
    SessionDep,
    require_visible_process,
)
from reparto_service.controllers.assignments import AssignmentController
from reparto_service.db_models.assignments import (
    AssignmentCreate,
    AssignmentDirectChoice,
    AssignmentPublic,
    AssignmentReassign,
    AssignmentUndo,
    AssignmentsPublic,
    AssignmentUpdate,
)
from reparto_service.schemas.planning import AssignmentValidationReport

router = APIRouter(
    prefix="/assignment-processes/{process_id}/assignments",
    tags=["assignments"],
    # Read scope (plan §21.4): every route under this process — read and
    # mutation alike — is refused with 404 when the process lies outside the
    # caller's departments.
    dependencies=[Depends(require_visible_process)],
)


@router.get("/", response_model=AssignmentsPublic)
def list_assignments(session: SessionDep, process_id: uuid.UUID) -> AssignmentsPublic:
    return AssignmentController.list_assignments(session, process_id)


@router.post("/", response_model=AssignmentPublic, status_code=201)
def create_assignment(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    assignment_in: AssignmentCreate,
) -> AssignmentPublic:
    return AssignmentController.create_assignment(
        session, process_id, current_user, assignment_in
    )


@router.post("/direct-choice", response_model=AssignmentPublic, status_code=201)
def create_direct_choice(
    session: SessionDep,
    current_user: CurrentWriter,
    process_id: uuid.UUID,
    choice: AssignmentDirectChoice,
) -> AssignmentPublic:
    # Own-data mutation (plan §21.3): ``WRITER`` is the floor, and the
    # controller binds the assignment to the caller's *own* participation row —
    # there is no participant id in the payload to point somewhere else.
    return AssignmentController.create_direct_choice(
        session, process_id, current_user, choice
    )


@router.get("/validations", response_model=AssignmentValidationReport)
def get_assignment_validations(
    session: SessionDep, current_user: CurrentAdmin, process_id: uuid.UUID
) -> AssignmentValidationReport:
    """Expose the assignment findings only to an administrator (`W7.1`).

    Since `W5.1` every §6.3/§6.4 finding names the participant it is about by
    display name and carries their hour figures, so the report is the
    department-head tier in list form — the same tier
    :mod:`reparto_service.services.sse` withholds from a teacher even on an
    event about themselves. A participant who needs to know whether the stage
    is ready asks ``GET .../teaching-plan/summary``, which is nameless and
    stays at the reader floor.
    """
    return AssignmentController.get_validations(session, process_id)


@router.get("/{assignment_id}", response_model=AssignmentPublic)
def get_assignment(
    session: SessionDep, process_id: uuid.UUID, assignment_id: uuid.UUID
) -> AssignmentPublic:
    return AssignmentController.get_assignment(session, process_id, assignment_id)


@router.patch("/{assignment_id}", response_model=AssignmentPublic)
def update_assignment(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    assignment_id: uuid.UUID,
    assignment_in: AssignmentUpdate,
) -> AssignmentPublic:
    return AssignmentController.update_assignment(
        session, process_id, assignment_id, assignment_in, current_user
    )


@router.post("/{assignment_id}/undo", response_model=AssignmentPublic)
def undo_assignment(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    assignment_id: uuid.UUID,
    action: AssignmentUndo,
) -> AssignmentPublic:
    return AssignmentController.undo_assignment(
        session, process_id, assignment_id, current_user, action
    )


@router.post(
    "/{assignment_id}/reassign", response_model=AssignmentPublic, status_code=201
)
def reassign_assignment(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    assignment_id: uuid.UUID,
    action: AssignmentReassign,
) -> AssignmentPublic:
    return AssignmentController.reassign_assignment(
        session, process_id, assignment_id, current_user, action
    )


@router.delete(
    "/{assignment_id}",
    response_model=AssignmentPublic,
    deprecated=True,
    include_in_schema=False,
)
def delete_assignment(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    assignment_id: uuid.UUID,
    action: AssignmentUndo,
) -> AssignmentPublic:
    """Compatibility alias for the explicit, reason-required undo action."""
    return AssignmentController.undo_assignment(
        session, process_id, assignment_id, current_user, action
    )
