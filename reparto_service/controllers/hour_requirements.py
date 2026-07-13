"""HourRequirement controller (per process)."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.hour_requirements import (
    HourRequirement,
    HourRequirementCreate,
    HourRequirementPublic,
    HourRequirementsPublic,
    HourRequirementUpdate,
)
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teaching_groups import TeachingGroup


class HourRequirementController(DomainController):
    """CRUD logic for hour requirements inside one assignment process."""

    @staticmethod
    def list_requirements(
        session: Session, process_id: uuid.UUID
    ) -> HourRequirementsPublic:
        DomainController.get_process_or_404(session, process_id)
        statement = select(HourRequirement).where(
            HourRequirement.assignment_process_id == process_id
        )
        items = list(session.exec(statement).all())
        return HourRequirementsPublic(
            data=[HourRequirementPublic.model_validate(item) for item in items],
            count=len(items),
        )

    @staticmethod
    def get_requirement(
        session: Session, process_id: uuid.UUID, requirement_id: uuid.UUID
    ) -> HourRequirementPublic:
        requirement = HourRequirementController._get_or_404(
            session, process_id, requirement_id
        )
        return HourRequirementPublic.model_validate(requirement)

    @staticmethod
    def create_requirement(
        session: Session,
        process_id: uuid.UUID,
        requirement_in: HourRequirementCreate,
        current_user: UserModel,
    ) -> HourRequirementPublic:
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        if requirement_in.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "assignment_process_id in the payload does not match the "
                    "URL process_id."
                ),
            )
        # Ensure the referenced subject and teaching group exist in the
        # same process.
        HourRequirementController._get_subject_or_404(
            session, process_id, requirement_in.subject_id
        )
        HourRequirementController._get_group_or_404(
            session, process_id, requirement_in.teaching_group_id
        )
        requirement = HourRequirement.model_validate(requirement_in.model_dump())
        session.add(requirement)
        HourRequirementController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="hour_requirement.created",
            entity_type="hour_requirement",
            entity_id=requirement.id,
            before=None,
            after=requirement,
        )
        session.commit()
        session.refresh(requirement)
        return HourRequirementPublic.model_validate(requirement)

    @staticmethod
    def update_requirement(
        session: Session,
        process_id: uuid.UUID,
        requirement_id: uuid.UUID,
        requirement_in: HourRequirementUpdate,
        current_user: UserModel,
    ) -> HourRequirementPublic:
        requirement = HourRequirementController._get_or_404(
            session, process_id, requirement_id
        )
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        before = HourRequirement.model_validate(requirement.model_dump())
        requirement.sqlmodel_update(requirement_in.model_dump(exclude_unset=True))
        session.add(requirement)
        HourRequirementController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="hour_requirement.updated",
            entity_type="hour_requirement",
            entity_id=requirement.id,
            before=before,
            after=requirement,
        )
        session.commit()
        session.refresh(requirement)
        return HourRequirementPublic.model_validate(requirement)

    @staticmethod
    def delete_requirement(
        session: Session,
        process_id: uuid.UUID,
        requirement_id: uuid.UUID,
        current_user: UserModel,
    ) -> HourRequirementPublic:
        requirement = HourRequirementController._get_or_404(
            session, process_id, requirement_id
        )
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        # Block delete if any assignment exists for the requirement.
        from reparto_service.db_models.assignments import Assignment

        statement = select(Assignment).where(
            Assignment.hour_requirement_id == requirement_id
        )
        if session.exec(statement).first() is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Cannot delete an hour requirement that already has assignments."
                ),
            )
        before = HourRequirement.model_validate(requirement.model_dump())
        session.delete(requirement)
        HourRequirementController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="hour_requirement.deleted",
            entity_type="hour_requirement",
            entity_id=requirement.id,
            before=before,
            after=None,
        )
        session.commit()
        return HourRequirementPublic.model_validate(requirement)

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _get_or_404(
        session: Session, process_id: uuid.UUID, requirement_id: uuid.UUID
    ) -> HourRequirement:
        DomainController.get_process_or_404(session, process_id)
        statement = select(HourRequirement).where(HourRequirement.id == requirement_id)
        requirement = session.exec(statement).first()
        if requirement is None or requirement.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"HourRequirement {requirement_id} not found in process "
                    f"{process_id}."
                ),
            )
        return requirement

    @staticmethod
    def _get_subject_or_404(
        session: Session, process_id: uuid.UUID, subject_id: uuid.UUID
    ) -> Subject:
        statement = select(Subject).where(Subject.id == subject_id)
        subject = session.exec(statement).first()
        if subject is None or subject.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(f"Subject {subject_id} not found in process {process_id}."),
            )
        return subject

    @staticmethod
    def _get_group_or_404(
        session: Session, process_id: uuid.UUID, group_id: uuid.UUID
    ) -> TeachingGroup:
        statement = select(TeachingGroup).where(TeachingGroup.id == group_id)
        group = session.exec(statement).first()
        if group is None or group.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(f"TeachingGroup {group_id} not found in process {process_id}."),
            )
        return group


__all__ = ["HourRequirementController"]
