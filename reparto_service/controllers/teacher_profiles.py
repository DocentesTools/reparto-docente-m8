"""TeacherProfile controller."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, func, select

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.teacher_profiles import (
    TeacherProfile,
    TeacherProfileCreate,
    TeacherProfileLinkUser,
    TeacherProfilePublic,
    TeacherProfileUpdate,
    TeacherProfilesPublic,
)


class TeacherProfileController(DomainController):
    """CRUD logic for teacher profiles (cross-process)."""

    @staticmethod
    def list_profiles(
        session: Session,
        active: bool | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> TeacherProfilesPublic:
        count_stmt = select(func.count()).select_from(TeacherProfile)
        list_stmt = select(TeacherProfile)
        if active is not None:
            count_stmt = count_stmt.where(TeacherProfile.active == active)
            list_stmt = list_stmt.where(TeacherProfile.active == active)
        count = session.exec(count_stmt).one()
        items = list(session.exec(list_stmt.offset(skip).limit(limit)).all())
        return TeacherProfilesPublic(
            data=[TeacherProfilePublic.model_validate(item) for item in items],
            count=count,
        )

    @staticmethod
    def get_profile(session: Session, profile_id: uuid.UUID) -> TeacherProfilePublic:
        profile = DomainController.get_or_404(session, TeacherProfile, profile_id)
        return TeacherProfilePublic.model_validate(profile)

    @staticmethod
    def create_profile(
        session: Session, profile_in: TeacherProfileCreate
    ) -> TeacherProfilePublic:
        profile = TeacherProfile.model_validate(profile_in.model_dump())
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return TeacherProfilePublic.model_validate(profile)

    @staticmethod
    def update_profile(
        session: Session,
        profile_id: uuid.UUID,
        profile_in: TeacherProfileUpdate,
    ) -> TeacherProfilePublic:
        profile = DomainController.get_or_404(session, TeacherProfile, profile_id)
        profile.sqlmodel_update(profile_in.model_dump(exclude_unset=True))
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return TeacherProfilePublic.model_validate(profile)

    @staticmethod
    def link_user(
        session: Session,
        profile_id: uuid.UUID,
        link_in: TeacherProfileLinkUser,
    ) -> TeacherProfilePublic:
        profile = DomainController.get_or_404(session, TeacherProfile, profile_id)
        existing = session.exec(
            select(TeacherProfile)
            .where(TeacherProfile.user_id == link_in.user_id)
            .where(TeacherProfile.id != profile_id)
        ).first()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Auth user is already linked to another teacher profile.",
            )
        profile.user_id = link_in.user_id
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return TeacherProfilePublic.model_validate(profile)

    @staticmethod
    def delete_profile(session: Session, profile_id: uuid.UUID) -> TeacherProfilePublic:
        profile = DomainController.get_or_404(session, TeacherProfile, profile_id)
        session.delete(profile)
        session.commit()
        return TeacherProfilePublic.model_validate(profile)


__all__ = ["TeacherProfileController"]
