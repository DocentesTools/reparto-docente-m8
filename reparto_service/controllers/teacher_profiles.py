"""TeacherProfile controller."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, func, select

from auth_sdk_m8.authorization import has_minimum_role
from auth_sdk_m8.schemas.base import RoleType
from auth_sdk_m8.schemas.user import UserModel

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

    #: Fields a teacher may change on their own profile (plan §21.3). The
    #: linkage (``user_id``) and the operational ``active`` flag are absent on
    #: purpose: both decide *whose* participation a profile carries, which is a
    #: department-head decision, not a self-service one.
    SELF_EDITABLE_FIELDS: frozenset[str] = frozenset({"display_name", "notes"})

    @staticmethod
    def update_profile(
        session: Session,
        profile_id: uuid.UUID,
        profile_in: TeacherProfileUpdate,
        current_user: UserModel,
    ) -> TeacherProfilePublic:
        profile = DomainController.get_or_404(session, TeacherProfile, profile_id)
        changes = profile_in.model_dump(exclude_unset=True)
        if not has_minimum_role(current_user.role, RoleType.ADMIN):
            forbidden = sorted(
                set(changes) - TeacherProfileController.SELF_EDITABLE_FIELDS
            )
            if forbidden:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Only a department head may change "
                        f"{', '.join(forbidden)} on a teacher profile."
                    ),
                )
        profile.sqlmodel_update(changes)
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
