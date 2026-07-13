"""AcademicYear controller."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, func, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.academic_years import (
    AcademicYear,
    AcademicYearCreate,
    AcademicYearPublic,
    AcademicYearsPublic,
    AcademicYearUpdate,
)
from reparto_service.enums import AcademicYearStatus


class AcademicYearController(DomainController):
    """CRUD logic for academic years."""

    @staticmethod
    def list_years(
        session: Session,
        skip: int = 0,
        limit: int = 100,
    ) -> AcademicYearsPublic:
        count = session.exec(select(func.count()).select_from(AcademicYear)).one()
        statement = select(AcademicYear).offset(skip).limit(limit)
        items = list(session.exec(statement).all())
        return AcademicYearsPublic(
            data=[AcademicYearPublic.model_validate(item) for item in items],
            count=count,
        )

    @staticmethod
    def get_year(session: Session, year_id: uuid.UUID) -> AcademicYearPublic:
        year = DomainController.get_or_404(session, AcademicYear, year_id)
        return AcademicYearPublic.model_validate(year)

    @staticmethod
    def create_year(
        session: Session,
        current_user: UserModel,
        year_in: AcademicYearCreate,
    ) -> AcademicYearPublic:
        if year_in.end_date <= year_in.start_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="end_date must be strictly after start_date.",
            )
        year = AcademicYear.model_validate(
            year_in.model_dump(),
            update={"created_by_user_id": current_user.id},
        )
        session.add(year)
        session.commit()
        session.refresh(year)
        return AcademicYearPublic.model_validate(year)

    @staticmethod
    def update_year(
        session: Session,
        year_id: uuid.UUID,
        year_in: AcademicYearUpdate,
    ) -> AcademicYearPublic:
        year = DomainController.get_or_404(session, AcademicYear, year_id)
        update_dict = year_in.model_dump(exclude_unset=True)
        if "start_date" in update_dict or "end_date" in update_dict:
            new_start = update_dict.get("start_date", year.start_date)
            new_end = update_dict.get("end_date", year.end_date)
            if new_end <= new_start:  # pragma: no branch
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="end_date must be strictly after start_date.",
                )
        year.sqlmodel_update(update_dict)
        session.add(year)
        session.commit()
        session.refresh(year)
        return AcademicYearPublic.model_validate(year)

    @staticmethod
    def archive_year(session: Session, year_id: uuid.UUID) -> AcademicYearPublic:
        year = DomainController.get_or_404(session, AcademicYear, year_id)
        year.status = AcademicYearStatus.ARCHIVED
        session.add(year)
        session.commit()
        session.refresh(year)
        return AcademicYearPublic.model_validate(year)


__all__ = ["AcademicYearController"]
