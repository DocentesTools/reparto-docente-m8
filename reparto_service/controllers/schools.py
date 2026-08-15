"""School controller."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from fastapi_m8 import UserModel
from sqlmodel import Session, col, func, select

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.schools import (
    School,
    SchoolCreate,
    SchoolPublic,
    SchoolsPublic,
    SchoolUpdate,
)
from reparto_service.services.read_scope import UNRESTRICTED, visible_school_ids


class SchoolController(DomainController):
    """CRUD logic for schools."""

    @staticmethod
    def list_schools(
        session: Session,
        current_user: UserModel,
        skip: int = 0,
        limit: int = 100,
    ) -> SchoolsPublic:
        count_stmt = select(func.count()).select_from(School)
        list_stmt = select(School)
        schools = visible_school_ids(session, current_user)
        if schools is not UNRESTRICTED:
            count_stmt = count_stmt.where(col(School.id).in_(schools))
            list_stmt = list_stmt.where(col(School.id).in_(schools))
        count = session.exec(count_stmt).one()
        items = list(session.exec(list_stmt.offset(skip).limit(limit)).all())
        return SchoolsPublic(
            data=[SchoolPublic.model_validate(item) for item in items],
            count=count,
        )

    @staticmethod
    def get_school(
        session: Session, current_user: UserModel, school_id: uuid.UUID
    ) -> SchoolPublic:
        school = DomainController.get_or_404(session, School, school_id)
        schools = visible_school_ids(session, current_user)
        if schools is not UNRESTRICTED and school.id not in schools:
            # 404, not 403: confirming the row exists is itself out of scope.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"School {school_id} not found.",
            )
        return SchoolPublic.model_validate(school)

    @staticmethod
    def create_school(session: Session, school_in: SchoolCreate) -> SchoolPublic:
        school = School.model_validate(school_in.model_dump())
        session.add(school)
        session.commit()
        session.refresh(school)
        return SchoolPublic.model_validate(school)

    @staticmethod
    def update_school(
        session: Session,
        school_id: uuid.UUID,
        school_in: SchoolUpdate,
    ) -> SchoolPublic:
        school = DomainController.get_or_404(session, School, school_id)
        school.sqlmodel_update(school_in.model_dump(exclude_unset=True))
        session.add(school)
        session.commit()
        session.refresh(school)
        return SchoolPublic.model_validate(school)


__all__ = ["SchoolController"]
