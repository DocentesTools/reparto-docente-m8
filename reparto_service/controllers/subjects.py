"""Subject controller (per process)."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, select

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.subjects import (
    Subject,
    SubjectCreate,
    SubjectPublic,
    SubjectsPublic,
    SubjectUpdate,
)


class SubjectController(DomainController):
    """CRUD logic for subjects inside one assignment process."""

    @staticmethod
    def list_subjects(session: Session, process_id: uuid.UUID) -> SubjectsPublic:
        DomainController.get_process_or_404(session, process_id)
        statement = select(Subject).where(Subject.assignment_process_id == process_id)
        items = list(session.exec(statement).all())
        return SubjectsPublic(
            data=[SubjectPublic.model_validate(item) for item in items],
            count=len(items),
        )

    @staticmethod
    def get_subject(
        session: Session, process_id: uuid.UUID, subject_id: uuid.UUID
    ) -> SubjectPublic:
        subject = SubjectController._get_or_404(session, process_id, subject_id)
        return SubjectPublic.model_validate(subject)

    @staticmethod
    def create_subject(
        session: Session, process_id: uuid.UUID, subject_in: SubjectCreate
    ) -> SubjectPublic:
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        if subject_in.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "assignment_process_id in the payload does not match the "
                    "URL process_id."
                ),
            )
        subject = Subject.model_validate(subject_in.model_dump())
        session.add(subject)
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Could not create subject: a subject with this name "
                    "already exists in the process."
                ),
            ) from exc
        session.refresh(subject)
        return SubjectPublic.model_validate(subject)

    @staticmethod
    def update_subject(
        session: Session,
        process_id: uuid.UUID,
        subject_id: uuid.UUID,
        subject_in: SubjectUpdate,
    ) -> SubjectPublic:
        subject = SubjectController._get_or_404(session, process_id, subject_id)
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        subject.sqlmodel_update(subject_in.model_dump(exclude_unset=True))
        session.add(subject)
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Could not update subject: a subject with this name "
                    "already exists in the process."
                ),
            ) from exc
        session.refresh(subject)
        return SubjectPublic.model_validate(subject)

    @staticmethod
    def delete_subject(
        session: Session, process_id: uuid.UUID, subject_id: uuid.UUID
    ) -> SubjectPublic:
        subject = SubjectController._get_or_404(session, process_id, subject_id)
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        session.delete(subject)
        session.commit()
        return SubjectPublic.model_validate(subject)

    @staticmethod
    def _get_or_404(
        session: Session, process_id: uuid.UUID, subject_id: uuid.UUID
    ) -> Subject:
        DomainController.get_process_or_404(session, process_id)
        statement = select(Subject).where(Subject.id == subject_id)
        subject = session.exec(statement).first()
        if subject is None or subject.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Subject {subject_id} not found in process {process_id}.",
            )
        return subject


__all__ = ["SubjectController"]
