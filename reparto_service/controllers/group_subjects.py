"""GroupSubject controller (per process).

CRUD logic for the intermediate group-subject matrix (plan §5.5, §7.2). Every
mutation validates that the referenced teaching group and subject both belong to
the URL process, enforces the per-process
``(assignment_process_id, teaching_group_id, subject_id)`` uniqueness and honours
the final/archived-process immutability guard (plan §8.4).

Guarded retirement against downstream materialised activities (plan §20.12) is a
no-op today because ``TeachingActivity`` does not exist yet; it is wired in when
that model lands. Bulk preview/apply (plan §7.2) is its own dedicated later task.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.group_subjects import (
    GroupSubject,
    GroupSubjectCreate,
    GroupSubjectPublic,
    GroupSubjectsPublic,
    GroupSubjectUpdate,
)
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teaching_groups import TeachingGroup


class GroupSubjectController(DomainController):
    """CRUD logic for group-subject cells inside one assignment process."""

    @staticmethod
    def list_group_subjects(
        session: Session, process_id: uuid.UUID
    ) -> GroupSubjectsPublic:
        DomainController.get_process_or_404(session, process_id)
        statement = select(GroupSubject).where(
            GroupSubject.assignment_process_id == process_id
        )
        items = list(session.exec(statement).all())
        return GroupSubjectsPublic(
            data=[GroupSubjectPublic.model_validate(item) for item in items],
            count=len(items),
        )

    @staticmethod
    def get_group_subject(
        session: Session, process_id: uuid.UUID, group_subject_id: uuid.UUID
    ) -> GroupSubjectPublic:
        group_subject = GroupSubjectController._get_or_404(
            session, process_id, group_subject_id
        )
        return GroupSubjectPublic.model_validate(group_subject)

    @staticmethod
    def create_group_subject(
        session: Session,
        process_id: uuid.UUID,
        group_subject_in: GroupSubjectCreate,
        current_user: UserModel,
    ) -> GroupSubjectPublic:
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        if group_subject_in.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "assignment_process_id in the payload does not match the "
                    "URL process_id."
                ),
            )
        # Both references must live in the same process.
        GroupSubjectController._get_group_or_404(
            session, process_id, group_subject_in.teaching_group_id
        )
        GroupSubjectController._get_subject_or_404(
            session, process_id, group_subject_in.subject_id
        )
        group_subject = GroupSubject.model_validate(group_subject_in.model_dump())
        session.add(group_subject)
        GroupSubjectController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="group_subject.created",
            entity_type="group_subject",
            entity_id=group_subject.id,
            before=None,
            after=group_subject,
        )
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Could not create group-subject: this group/subject pair "
                    "is already configured in the process."
                ),
            ) from exc
        session.refresh(group_subject)
        return GroupSubjectPublic.model_validate(group_subject)

    @staticmethod
    def update_group_subject(
        session: Session,
        process_id: uuid.UUID,
        group_subject_id: uuid.UUID,
        group_subject_in: GroupSubjectUpdate,
        current_user: UserModel,
    ) -> GroupSubjectPublic:
        group_subject = GroupSubjectController._get_or_404(
            session, process_id, group_subject_id
        )
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        before = GroupSubject.model_validate(group_subject.model_dump())
        group_subject.sqlmodel_update(group_subject_in.model_dump(exclude_unset=True))
        session.add(group_subject)
        GroupSubjectController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="group_subject.updated",
            entity_type="group_subject",
            entity_id=group_subject.id,
            before=before,
            after=group_subject,
        )
        session.commit()
        session.refresh(group_subject)
        return GroupSubjectPublic.model_validate(group_subject)

    @staticmethod
    def delete_group_subject(
        session: Session,
        process_id: uuid.UUID,
        group_subject_id: uuid.UUID,
        current_user: UserModel,
    ) -> GroupSubjectPublic:
        group_subject = GroupSubjectController._get_or_404(
            session, process_id, group_subject_id
        )
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        before = GroupSubject.model_validate(group_subject.model_dump())
        session.delete(group_subject)
        GroupSubjectController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="group_subject.deleted",
            entity_type="group_subject",
            entity_id=group_subject.id,
            before=before,
            after=None,
        )
        session.commit()
        return GroupSubjectPublic.model_validate(group_subject)

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _get_or_404(
        session: Session, process_id: uuid.UUID, group_subject_id: uuid.UUID
    ) -> GroupSubject:
        DomainController.get_process_or_404(session, process_id)
        statement = select(GroupSubject).where(GroupSubject.id == group_subject_id)
        group_subject = session.exec(statement).first()
        if group_subject is None or group_subject.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"GroupSubject {group_subject_id} not found in process "
                    f"{process_id}."
                ),
            )
        return group_subject

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


__all__ = ["GroupSubjectController"]
