"""School controller."""

from __future__ import annotations

import uuid

from sqlmodel import Session, func, select

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.schools import (
    School,
    SchoolCreate,
    SchoolPublic,
    SchoolsPublic,
    SchoolUpdate,
)


class SchoolController(DomainController):
    """CRUD logic for schools."""

    @staticmethod
    def list_schools(
        session: Session,
        skip: int = 0,
        limit: int = 100,
    ) -> SchoolsPublic:
        count = session.exec(select(func.count()).select_from(School)).one()
        statement = select(School).offset(skip).limit(limit)
        items = list(session.exec(statement).all())
        return SchoolsPublic(
            data=[SchoolPublic.model_validate(item) for item in items],
            count=count,
        )

    @staticmethod
    def get_school(session: Session, school_id: uuid.UUID) -> SchoolPublic:
        school = DomainController.get_or_404(session, School, school_id)
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
