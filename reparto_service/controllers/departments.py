"""Department controller."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, func, select

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.departments import (
    Department,
    DepartmentCreate,
    DepartmentPublic,
    DepartmentsPublic,
    DepartmentUpdate,
)
from reparto_service.db_models.schools import School


class DepartmentController(DomainController):
    """CRUD logic for departments."""

    @staticmethod
    def list_departments(
        session: Session,
        school_id: uuid.UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> DepartmentsPublic:
        count_stmt = select(func.count()).select_from(Department)
        list_stmt = select(Department)
        if school_id is not None:
            count_stmt = count_stmt.where(Department.school_id == school_id)
            list_stmt = list_stmt.where(Department.school_id == school_id)
        count = session.exec(count_stmt).one()
        items = list(session.exec(list_stmt.offset(skip).limit(limit)).all())
        return DepartmentsPublic(
            data=[DepartmentPublic.model_validate(item) for item in items],
            count=count,
        )

    @staticmethod
    def get_department(session: Session, department_id: uuid.UUID) -> DepartmentPublic:
        department = DomainController.get_or_404(session, Department, department_id)
        return DepartmentPublic.model_validate(department)

    @staticmethod
    def create_department(
        session: Session, department_in: DepartmentCreate
    ) -> DepartmentPublic:
        # Validate the school exists.
        DomainController.get_or_404(session, School, department_in.school_id)
        department = Department.model_validate(department_in.model_dump())
        session.add(department)
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Could not create department: "
                "check that the slug is unique within the school.",
            ) from exc
        session.refresh(department)
        return DepartmentPublic.model_validate(department)

    @staticmethod
    def update_department(
        session: Session,
        department_id: uuid.UUID,
        department_in: DepartmentUpdate,
    ) -> DepartmentPublic:
        department = DomainController.get_or_404(session, Department, department_id)
        department.sqlmodel_update(department_in.model_dump(exclude_unset=True))
        session.add(department)
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Could not update department: "
                "check that the slug is unique within the school.",
            ) from exc
        session.refresh(department)
        return DepartmentPublic.model_validate(department)


__all__ = ["DepartmentController"]
