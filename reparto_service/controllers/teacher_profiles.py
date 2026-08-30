"""TeacherProfile controller."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from fastapi_m8 import RoleType, UserModel, has_minimum_role
from sqlmodel import Session, col, func, select

from reparto_service.controllers.base import DomainController
from reparto_service.core.config import settings
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.teacher_profiles import (
    TeacherProfile,
    TeacherProfileClaim,
    TeacherProfileClaimCode,
    TeacherProfileCreate,
    TeacherProfileLinkUser,
    TeacherProfilePublic,
    TeacherProfilesPublic,
    TeacherProfileUpdate,
)
from reparto_service.enums import AuditEventType
from reparto_service.services.claim_codes import hash_claim_code, mint_claim_code
from reparto_service.services.read_scope import UNRESTRICTED, visible_department_ids


class TeacherProfileController(DomainController):
    """CRUD logic for teacher profiles (cross-process)."""

    @staticmethod
    def list_profiles(
        session: Session,
        current_user: UserModel,
        active: bool | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> TeacherProfilesPublic:
        count_stmt = select(func.count()).select_from(TeacherProfile)
        list_stmt = select(TeacherProfile)
        visible = TeacherProfileController._visible_profile_ids(session, current_user)
        if visible is not UNRESTRICTED:
            count_stmt = count_stmt.where(col(TeacherProfile.id).in_(visible))
            list_stmt = list_stmt.where(col(TeacherProfile.id).in_(visible))
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
    def _visible_profile_ids(
        session: Session, current_user: UserModel
    ) -> set[uuid.UUID] | None:
        """Return the profiles *current_user* may read, or ``UNRESTRICTED``.

        A teacher sees the colleagues they share a department with, plus their
        own profile — which matters for the caller whose profile exists but who
        has not been added to any process yet, and who would otherwise be
        unable to read the very record they are allowed to edit.
        """
        departments = visible_department_ids(session, current_user)
        if departments is UNRESTRICTED:
            return UNRESTRICTED
        visible: set[uuid.UUID] = set()
        if departments:
            statement = (
                select(ProcessTeacher.teacher_profile_id)
                .join(
                    AssignmentProcess,
                    col(AssignmentProcess.id)
                    == col(ProcessTeacher.assignment_process_id),
                )
                .where(col(AssignmentProcess.department_id).in_(departments))
            )
            visible = set(session.exec(statement).all())
        own = session.exec(
            select(TeacherProfile.id).where(
                TeacherProfile.user_id == uuid.UUID(str(current_user.id))
            )
        ).all()
        visible.update(own)
        return visible

    @staticmethod
    def get_profile(
        session: Session, current_user: UserModel, profile_id: uuid.UUID
    ) -> TeacherProfilePublic:
        profile = DomainController.get_or_404(session, TeacherProfile, profile_id)
        visible = TeacherProfileController._visible_profile_ids(session, current_user)
        if visible is not UNRESTRICTED and profile.id not in visible:
            # 404, not 403: confirming the row exists is itself out of scope.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"TeacherProfile {profile_id} not found.",
            )
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
    def issue_claim_code(
        session: Session,
        current_user: UserModel,
        profile_id: uuid.UUID,
    ) -> TeacherProfileClaimCode:
        """Mint the single-use code that lets *this* profile be claimed (`W1.4`).

        Refused for a profile that is already linked. That is not tidiness: a
        code minted over a live linkage would let whoever redeems it take over
        the participation of the account currently holding it. Unlinking first
        is an explicit department-head act with its own row on the roster, so
        the takeover cannot happen by pressing one button.

        Minting again replaces any outstanding code — there is at most one live
        code per profile, so a code read out in the wrong room is revoked by
        issuing the next one rather than by remembering to expire it.
        """
        profile = DomainController.get_or_404(session, TeacherProfile, profile_id)
        if profile.user_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Teacher profile is already linked to an auth user; "
                    "unlink it before issuing a claim code."
                ),
            )
        code = mint_claim_code()
        expires_at = datetime.now(tz=timezone.utc) + timedelta(
            hours=settings.CLAIM_CODE_TTL_HOURS
        )
        profile.claim_code_hash = hash_claim_code(code)
        profile.claim_code_expires_at = expires_at
        session.add(profile)
        TeacherProfileController._audit_profile_linkage(
            session,
            current_user=current_user,
            profile=profile,
            event_type=AuditEventType.TEACHER_PROFILE_CLAIM_CODE_ISSUED,
            before=None,
        )
        session.commit()
        session.refresh(profile)
        return TeacherProfileClaimCode(
            teacher_profile_id=profile.id,
            display_name=profile.display_name,
            claim_code=code,
            expires_at=expires_at,
        )

    @staticmethod
    def claim_profile(
        session: Session,
        current_user: UserModel,
        claim_in: TeacherProfileClaim,
    ) -> TeacherProfilePublic:
        """Bind the profile *claim_in*'s code names to the caller's own account.

        The caller's id comes from the token and from nowhere else, so this
        endpoint can only ever link the person presenting the code. The 409
        "already linked to another profile" rule is not restated here: the
        linkage goes through :meth:`link_user`, the same path the department
        head's own action uses, so there is one place that decides an account
        holds at most one profile.

        Every refusal answers the same 400 with the same wording — unknown,
        expired, or minted for a profile that has since been linked. Telling
        the difference would tell a caller holding a wrong code which half of
        it to vary.
        """
        profile = session.exec(
            select(TeacherProfile).where(
                TeacherProfile.claim_code_hash == hash_claim_code(claim_in.claim_code)
            )
        ).first()
        expires_at = profile.claim_code_expires_at if profile else None
        if (
            profile is None
            or profile.user_id is not None
            or expires_at is None
            or _as_utc(expires_at) <= datetime.now(tz=timezone.utc)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Claim code is not valid, or has expired or been used.",
            )
        before = TeacherProfilePublic.model_validate(profile)
        linked = TeacherProfileController.link_user(
            session,
            profile.id,
            TeacherProfileLinkUser(user_id=uuid.UUID(str(current_user.id))),
        )
        # Consumed only once the linkage committed: a 409 from ``link_user``
        # leaves the code redeemable, because the caller who was refused is not
        # the teacher the head issued it to.
        profile.claim_code_hash = None
        profile.claim_code_expires_at = None
        session.add(profile)
        TeacherProfileController._audit_profile_linkage(
            session,
            current_user=current_user,
            profile=profile,
            event_type=AuditEventType.TEACHER_PROFILE_CLAIMED,
            before=before,
        )
        session.commit()
        return linked

    @staticmethod
    def _audit_profile_linkage(
        session: Session,
        *,
        current_user: UserModel,
        profile: TeacherProfile,
        event_type: AuditEventType,
        before: TeacherProfilePublic | None,
    ) -> None:
        """Record a linkage event on every process the profile takes part in.

        A teacher profile is cross-process but ``AuditEvent`` is not: the trail
        is read per process and ``assignment_process_id`` is not nullable. So
        one event is written per participating process, which is also where the
        record is useful — the head running that reparto is the person who needs
        to see who claimed a participant. A profile in no process yet writes no
        row; there is no reparto for it to belong to.

        Neither the code nor its hash appears in the payload: both sides are
        recorded from :class:`TeacherProfilePublic`, which has no claim-code
        field at all, so the trail cannot leak a live credential.
        """
        process_ids = session.exec(
            select(ProcessTeacher.assignment_process_id)
            .where(ProcessTeacher.teacher_profile_id == profile.id)
            .distinct()
        ).all()
        after = TeacherProfilePublic.model_validate(profile)
        for process_id in process_ids:
            TeacherProfileController.record_audit_event(
                session,
                process_id=process_id,
                current_user=current_user,
                event_type=event_type,
                entity_type="teacher_profile",
                entity_id=profile.id,
                before=before,
                after=after,
            )

    @staticmethod
    def delete_profile(session: Session, profile_id: uuid.UUID) -> TeacherProfilePublic:
        profile = DomainController.get_or_404(session, TeacherProfile, profile_id)
        session.delete(profile)
        session.commit()
        return TeacherProfilePublic.model_validate(profile)


def _as_utc(moment: datetime) -> datetime:
    """Read a stored timestamp as UTC-aware.

    SQLite hands back a naive value from a ``DateTime(timezone=True)`` column,
    so an expiry compared as-is would raise rather than expire. The stored
    instant is always UTC — it is written from ``datetime.now(tz=utc)``.
    """
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


__all__ = ["TeacherProfileController"]
