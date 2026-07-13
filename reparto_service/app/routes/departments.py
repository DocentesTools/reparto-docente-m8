"""Department routes."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
from reparto_service.controllers.departments import DepartmentController
from reparto_service.db_models.departments import (
    DepartmentCreate,
    DepartmentPublic,
    DepartmentsPublic,
    DepartmentUpdate,
)

router = APIRouter(prefix="/departments", tags=["departments"])


@router.get("/", response_model=DepartmentsPublic)
def list_departments(
    session: SessionDep,
    school_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = 100,
) -> DepartmentsPublic:
    return DepartmentController.list_departments(
        session, school_id=school_id, skip=skip, limit=limit
    )


@router.post("/", response_model=DepartmentPublic, status_code=201)
def create_department(
    session: SessionDep,
    current_user: CurrentUser,
    department_in: DepartmentCreate,
) -> DepartmentPublic:
    DepartmentController.require_writer(current_user)
    return DepartmentController.create_department(session, department_in)


@router.get("/{department_id}", response_model=DepartmentPublic)
def get_department(session: SessionDep, department_id: uuid.UUID) -> DepartmentPublic:
    return DepartmentController.get_department(session, department_id)


@router.patch("/{department_id}", response_model=DepartmentPublic)
def update_department(
    session: SessionDep,
    current_user: CurrentUser,
    department_id: uuid.UUID,
    department_in: DepartmentUpdate,
) -> DepartmentPublic:
    DepartmentController.require_writer(current_user)
    return DepartmentController.update_department(session, department_id, department_in)
