"""TeacherProfile routes."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter

from reparto_service.app.deps import (
    CurrentAdmin,
    CurrentReader,
    CurrentWriter,
    SessionDep,
)
from reparto_service.controllers.teacher_profiles import TeacherProfileController
from reparto_service.db_models.teacher_profiles import (
    TeacherProfileClaim,
    TeacherProfileClaimCode,
    TeacherProfileCreate,
    TeacherProfileLinkUser,
    TeacherProfilePublic,
    TeacherProfileUpdate,
    TeacherProfilesPublic,
)

router = APIRouter(prefix="/teacher-profiles", tags=["teacher-profiles"])


@router.get("/", response_model=TeacherProfilesPublic)
def list_profiles(
    session: SessionDep,
    current_user: CurrentReader,
    active: Optional[bool] = None,
    skip: int = 0,
    limit: int = 100,
) -> TeacherProfilesPublic:
    return TeacherProfileController.list_profiles(
        session, current_user, active=active, skip=skip, limit=limit
    )


@router.post("/", response_model=TeacherProfilePublic, status_code=201)
def create_profile(
    session: SessionDep,
    current_user: CurrentAdmin,
    profile_in: TeacherProfileCreate,
) -> TeacherProfilePublic:
    return TeacherProfileController.create_profile(session, profile_in)


@router.post("/claim", response_model=TeacherProfilePublic)
def claim_profile(
    session: SessionDep,
    current_user: CurrentReader,
    claim_in: TeacherProfileClaim,
) -> TeacherProfilePublic:
    """Redeem a claim code against the caller's **own** account (`W1.4`).

    The one mutation in this service whose authorization is a credential rather
    than a role, and deliberately so. The department head cannot look a
    colleague's user id up — ``fa-auth-m8`` restricts its directory to
    superusers by its own design — so the head issues a code instead and the
    teacher presents it with their own token. The floor is therefore the
    service's reader floor and no higher: requiring ``WRITER`` would leave a
    read-only participant permanently unable to reach their own view, and
    requiring more would put a superuser back on the path this whole flow
    exists to remove. It stays safe because the caller's id comes from the
    token (the schema has no ``user_id`` at all, so no payload can name another
    account) and because the code is single-use, expiring and hashed at rest.

    Declared above ``/{profile_id}`` so the literal path is never read as an id.
    """
    return TeacherProfileController.claim_profile(session, current_user, claim_in)


@router.get("/{profile_id}", response_model=TeacherProfilePublic)
def get_profile(
    session: SessionDep, current_user: CurrentReader, profile_id: uuid.UUID
) -> TeacherProfilePublic:
    return TeacherProfileController.get_profile(session, current_user, profile_id)


@router.patch("/{profile_id}", response_model=TeacherProfilePublic)
def update_profile(
    session: SessionDep,
    current_user: CurrentWriter,
    profile_id: uuid.UUID,
    profile_in: TeacherProfileUpdate,
) -> TeacherProfilePublic:
    # Own-data mutation (plan §21.3): a department head may edit any profile;
    # anyone else may edit only the profile linked to their own account, and
    # only its descriptive fields — the linkage and the active flag stay
    # department-head territory, so nobody can re-point a profile at another
    # account and inherit that account's participation.
    TeacherProfileController.require_own_teacher_profile(
        session, current_user, profile_id
    )
    return TeacherProfileController.update_profile(
        session, profile_id, profile_in, current_user
    )


@router.post("/{profile_id}/link-user", response_model=TeacherProfilePublic)
def link_profile_user(
    session: SessionDep,
    current_user: CurrentAdmin,
    profile_id: uuid.UUID,
    link_in: TeacherProfileLinkUser,
) -> TeacherProfilePublic:
    return TeacherProfileController.link_user(session, profile_id, link_in)


@router.post(
    "/{profile_id}/claim-code",
    response_model=TeacherProfileClaimCode,
    status_code=201,
)
def issue_profile_claim_code(
    session: SessionDep,
    current_user: CurrentAdmin,
    profile_id: uuid.UUID,
) -> TeacherProfileClaimCode:
    """Mint the code a teacher redeems to claim *profile_id* (`W1.4`).

    The response is the only time the code exists in readable form — it is
    stored hashed — so a head who loses it issues another rather than reading
    it back.
    """
    return TeacherProfileController.issue_claim_code(session, current_user, profile_id)


@router.delete("/{profile_id}", response_model=TeacherProfilePublic)
def delete_profile(
    session: SessionDep,
    current_user: CurrentAdmin,
    profile_id: uuid.UUID,
) -> TeacherProfilePublic:
    return TeacherProfileController.delete_profile(session, profile_id)
