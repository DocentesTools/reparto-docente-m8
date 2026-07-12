"""CRUD controller for global classroom stages."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.classroom_stages import (
    ClassroomStage,
    ClassroomStageCreate,
    ClassroomStagePublic,
    ClassroomStagesPublic,
    ClassroomStageUpdate,
)
from reparto_service.db_models.teaching_groups import TeachingGroup


class ClassroomStageController(DomainController):
    """Global classroom-stage operations."""

    @staticmethod
    def list_stages(session: Session) -> ClassroomStagesPublic:
        """Return every global classroom stage."""
        items = list(session.exec(select(ClassroomStage)).all())
        return ClassroomStagesPublic(
            data=[ClassroomStagePublic.model_validate(item) for item in items],
            count=len(items),
        )

    @staticmethod
    def get_stage(session: Session, stage_id: uuid.UUID) -> ClassroomStagePublic:
        """Return one classroom stage or 404."""
        stage = DomainController.get_or_404(session, ClassroomStage, stage_id)
        return ClassroomStagePublic.model_validate(stage)

    @staticmethod
    def create_stage(
        session: Session, stage_in: ClassroomStageCreate
    ) -> ClassroomStagePublic:
        """Create global stage data."""
        stage = ClassroomStage.model_validate(stage_in.model_dump())
        session.add(stage)
        ClassroomStageController._commit_unique(session)
        session.refresh(stage)
        return ClassroomStagePublic.model_validate(stage)

    @staticmethod
    def update_stage(
        session: Session,
        stage_id: uuid.UUID,
        stage_in: ClassroomStageUpdate,
    ) -> ClassroomStagePublic:
        """Update a stage after validating its final grade range."""
        stage = DomainController.get_or_404(session, ClassroomStage, stage_id)
        values = stage_in.model_dump(exclude_unset=True)
        final_min = values.get("min_grade", stage.min_grade)
        final_max = values.get("max_grade", stage.max_grade)
        if final_min > final_max:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="min_grade must be less than or equal to max_grade",
            )
        stage.sqlmodel_update(values)
        session.add(stage)
        ClassroomStageController._commit_unique(session)
        session.refresh(stage)
        return ClassroomStagePublic.model_validate(stage)

    @staticmethod
    def delete_stage(session: Session, stage_id: uuid.UUID) -> ClassroomStagePublic:
        """Delete an unused stage and reject referenced stages."""
        stage = DomainController.get_or_404(session, ClassroomStage, stage_id)
        referenced = session.exec(
            select(TeachingGroup).where(TeachingGroup.classroom_stage_id == stage_id)
        ).first()
        if referenced is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "classroom_stage_in_use",
                    "message": "The classroom stage is referenced by classrooms.",
                },
            )
        public = ClassroomStagePublic.model_validate(stage)
        session.delete(stage)
        session.commit()
        return public

    @staticmethod
    def _commit_unique(session: Session) -> None:
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "classroom_stage_exists",
                    "message": "A classroom stage with this name already exists.",
                },
            ) from exc


__all__ = ["ClassroomStageController"]
