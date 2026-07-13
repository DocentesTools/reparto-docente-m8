"""School routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
from reparto_service.controllers.schools import SchoolController
from reparto_service.db_models.schools import (
    SchoolCreate,
    SchoolPublic,
    SchoolsPublic,
    SchoolUpdate,
)

router = APIRouter(prefix="/schools", tags=["schools"])


@router.get("/", response_model=SchoolsPublic)
def list_schools(
    session: SessionDep,
    skip: int = 0,
    limit: int = 100,
) -> SchoolsPublic:
    return SchoolController.list_schools(session, skip=skip, limit=limit)


@router.post("/", response_model=SchoolPublic, status_code=201)
def create_school(
    session: SessionDep,
    current_user: CurrentUser,
    school_in: SchoolCreate,
) -> SchoolPublic:
    SchoolController.require_writer(current_user)
    return SchoolController.create_school(session, school_in)


@router.get("/{school_id}", response_model=SchoolPublic)
def get_school(session: SessionDep, school_id: uuid.UUID) -> SchoolPublic:
    return SchoolController.get_school(session, school_id)


@router.patch("/{school_id}", response_model=SchoolPublic)
def update_school(
    session: SessionDep,
    current_user: CurrentUser,
    school_id: uuid.UUID,
    school_in: SchoolUpdate,
) -> SchoolPublic:
    SchoolController.require_writer(current_user)
    return SchoolController.update_school(session, school_id, school_in)
