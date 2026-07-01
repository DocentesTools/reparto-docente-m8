"""TeachingGroup controller (per process)."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, select

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.teaching_groups import (
    TeachingGroup,
    TeachingGroupCreate,
    TeachingGroupPublic,
    TeachingGroupsPublic,
    TeachingGroupUpdate,
)


class TeachingGroupController(DomainController):
    """CRUD logic for teaching groups inside one assignment process."""

    @staticmethod
    def list_groups(session: Session, process_id: uuid.UUID) -> TeachingGroupsPublic:
        DomainController.get_process_or_404(session, process_id)
        statement = select(TeachingGroup).where(
            TeachingGroup.assignment_process_id == process_id
        )
        items = list(session.exec(statement).all())
        return TeachingGroupsPublic(
            data=[TeachingGroupPublic.model_validate(item) for item in items],
            count=len(items),
        )

    @staticmethod
    def get_group(
        session: Session, process_id: uuid.UUID, group_id: uuid.UUID
    ) -> TeachingGroupPublic:
        group = TeachingGroupController._get_or_404(session, process_id, group_id)
        return TeachingGroupPublic.model_validate(group)

    @staticmethod
    def create_group(
        session: Session,
        process_id: uuid.UUID,
        group_in: TeachingGroupCreate,
    ) -> TeachingGroupPublic:
        DomainController.get_process_or_404(session, process_id)
        if group_in.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "assignment_process_id in the payload does not match the "
                    "URL process_id."
                ),
            )
        group = TeachingGroup.model_validate(group_in.model_dump())
        session.add(group)
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Could not create teaching group: a group with this label "
                    "already exists in the process."
                ),
            ) from exc
        session.refresh(group)
        return TeachingGroupPublic.model_validate(group)

    @staticmethod
    def update_group(
        session: Session,
        process_id: uuid.UUID,
        group_id: uuid.UUID,
        group_in: TeachingGroupUpdate,
    ) -> TeachingGroupPublic:
        group = TeachingGroupController._get_or_404(session, process_id, group_id)
        group.sqlmodel_update(group_in.model_dump(exclude_unset=True))
        session.add(group)
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Could not update teaching group: a group with this label "
                    "already exists in the process."
                ),
            ) from exc
        session.refresh(group)
        return TeachingGroupPublic.model_validate(group)

    @staticmethod
    def delete_group(
        session: Session, process_id: uuid.UUID, group_id: uuid.UUID
    ) -> TeachingGroupPublic:
        group = TeachingGroupController._get_or_404(session, process_id, group_id)
        session.delete(group)
        session.commit()
        return TeachingGroupPublic.model_validate(group)

    @staticmethod
    def _get_or_404(
        session: Session, process_id: uuid.UUID, group_id: uuid.UUID
    ) -> TeachingGroup:
        DomainController.get_process_or_404(session, process_id)
        statement = select(TeachingGroup).where(TeachingGroup.id == group_id)
        group = session.exec(statement).first()
        if group is None or group.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(f"TeachingGroup {group_id} not found in process {process_id}."),
            )
        return group


__all__ = ["TeachingGroupController"]
