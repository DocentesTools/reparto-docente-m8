"""TeacherProfile routes."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
from reparto_service.controllers.teacher_profiles import TeacherProfileController
from reparto_service.db_models.teacher_profiles import (
    TeacherProfileCreate,
    TeacherProfilePublic,
    TeacherProfileUpdate,
    TeacherProfilesPublic,
)

router = APIRouter(prefix="/teacher-profiles", tags=["teacher-profiles"])


@router.get("/", response_model=TeacherProfilesPublic)
def list_profiles(
    session: SessionDep,
    active: Optional[bool] = None,
    skip: int = 0,
    limit: int = 100,
) -> TeacherProfilesPublic:
    return TeacherProfileController.list_profiles(
        session, active=active, skip=skip, limit=limit
    )


@router.post("/", response_model=TeacherProfilePublic, status_code=201)
def create_profile(
    session: SessionDep,
    current_user: CurrentUser,
    profile_in: TeacherProfileCreate,
) -> TeacherProfilePublic:
    TeacherProfileController.require_writer(current_user)
    return TeacherProfileController.create_profile(session, profile_in)


@router.get("/{profile_id}", response_model=TeacherProfilePublic)
def get_profile(session: SessionDep, profile_id: uuid.UUID) -> TeacherProfilePublic:
    return TeacherProfileController.get_profile(session, profile_id)


@router.patch("/{profile_id}", response_model=TeacherProfilePublic)
def update_profile(
    session: SessionDep,
    current_user: CurrentUser,
    profile_id: uuid.UUID,
    profile_in: TeacherProfileUpdate,
) -> TeacherProfilePublic:
    TeacherProfileController.require_writer(current_user)
    return TeacherProfileController.update_profile(session, profile_id, profile_in)


@router.delete("/{profile_id}", response_model=TeacherProfilePublic)
def delete_profile(
    session: SessionDep,
    current_user: CurrentUser,
    profile_id: uuid.UUID,
) -> TeacherProfilePublic:
    TeacherProfileController.require_writer(current_user)
    return TeacherProfileController.delete_profile(session, profile_id)
