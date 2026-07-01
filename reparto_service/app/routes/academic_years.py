"""AcademicYear routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
from reparto_service.controllers.academic_years import AcademicYearController
from reparto_service.db_models.academic_years import (
    AcademicYearCreate,
    AcademicYearPublic,
    AcademicYearsPublic,
    AcademicYearUpdate,
)

router = APIRouter(prefix="/academic-years", tags=["academic-years"])


@router.get("/", response_model=AcademicYearsPublic)
def list_years(
    session: SessionDep,
    skip: int = 0,
    limit: int = 100,
) -> AcademicYearsPublic:
    return AcademicYearController.list_years(session, skip=skip, limit=limit)


@router.post(
    "/",
    response_model=AcademicYearPublic,
    status_code=201,
    responses={403: {"description": "Writer role required"}},
)
def create_year(
    session: SessionDep,
    current_user: CurrentUser,
    year_in: AcademicYearCreate,
) -> AcademicYearPublic:
    AcademicYearController.require_writer(current_user)
    return AcademicYearController.create_year(session, current_user, year_in)


@router.get("/{year_id}", response_model=AcademicYearPublic)
def get_year(session: SessionDep, year_id: uuid.UUID) -> AcademicYearPublic:
    return AcademicYearController.get_year(session, year_id)


@router.patch("/{year_id}", response_model=AcademicYearPublic)
def update_year(
    session: SessionDep,
    current_user: CurrentUser,
    year_id: uuid.UUID,
    year_in: AcademicYearUpdate,
) -> AcademicYearPublic:
    AcademicYearController.require_writer(current_user)
    return AcademicYearController.update_year(session, year_id, year_in)


@router.post("/{year_id}/archive", response_model=AcademicYearPublic)
def archive_year(
    session: SessionDep,
    current_user: CurrentUser,
    year_id: uuid.UUID,
) -> AcademicYearPublic:
    AcademicYearController.require_writer(current_user)
    return AcademicYearController.archive_year(session, year_id)
