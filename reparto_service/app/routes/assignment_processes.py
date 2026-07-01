"""AssignmentProcess routes.

Hosts the parent CRUD for an annual assignment process and the
read-only summary/dashboard endpoints used by the department-head view.
Per-resource child endpoints (teachers, subjects, groups, requirements,
assignments) live in their own route files but are mounted under the
``/assignment-processes/{process_id}/...`` namespace.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
from reparto_service.controllers.assignment_processes import (
    AssignmentProcessController,
)
from reparto_service.controllers.dashboard import DashboardController
from reparto_service.db_models.assignment_processes import (
    AssignmentProcessCreate,
    AssignmentProcessPublic,
    AssignmentProcessesPublic,
    AssignmentProcessUpdate,
)
from reparto_service.schemas.summary import ProcessDashboard, ProcessSummary

router = APIRouter(prefix="/assignment-processes", tags=["assignment-processes"])


@router.get("/", response_model=AssignmentProcessesPublic)
def list_processes(
    session: SessionDep,
    academic_year_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = 100,
) -> AssignmentProcessesPublic:
    return AssignmentProcessController.list_processes(
        session, academic_year_id=academic_year_id, skip=skip, limit=limit
    )


@router.post("/", response_model=AssignmentProcessPublic, status_code=201)
def create_process(
    session: SessionDep,
    current_user: CurrentUser,
    process_in: AssignmentProcessCreate,
) -> AssignmentProcessPublic:
    AssignmentProcessController.require_writer(current_user)
    return AssignmentProcessController.create_process(session, current_user, process_in)


@router.get("/{process_id}", response_model=AssignmentProcessPublic)
def get_process(session: SessionDep, process_id: uuid.UUID) -> AssignmentProcessPublic:
    return AssignmentProcessController.get_process(session, process_id)


@router.patch("/{process_id}", response_model=AssignmentProcessPublic)
def update_process(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    process_in: AssignmentProcessUpdate,
) -> AssignmentProcessPublic:
    AssignmentProcessController.require_writer(current_user)
    return AssignmentProcessController.update_process(session, process_id, process_in)


# ── Summary / dashboard read endpoints ────────────────────────────────────────


@router.get("/{process_id}/summary", response_model=ProcessSummary)
def get_process_summary(session: SessionDep, process_id: uuid.UUID) -> ProcessSummary:
    return DashboardController.get_summary(session, process_id)


@router.get("/{process_id}/dashboard", response_model=ProcessDashboard)
def get_process_dashboard(
    session: SessionDep, process_id: uuid.UUID
) -> ProcessDashboard:
    return DashboardController.get_dashboard(session, process_id)
