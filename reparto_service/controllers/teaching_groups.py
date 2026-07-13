"""Teaching-group controller with stage validation and atomic bulk creation."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.classroom_stages import (
    ClassroomStage,
    ClassroomStageSummary,
)
from reparto_service.db_models.teaching_groups import (
    TeachingGroup,
    TeachingGroupBulkCreate,
    TeachingGroupCreate,
    TeachingGroupPublic,
    TeachingGroupsPublic,
    TeachingGroupUpdate,
)


def generate_classroom_label(*, grade: int, stage_label: str, group_code: str) -> str:
    """Generate the canonical classroom label."""
    return f"{grade}° {' '.join(stage_label.split())} {group_code.strip().upper()}"


def generate_group_code_range(start: str, end: str) -> list[str]:
    """Return an inclusive A-Z range or raise a 422 error."""
    first, last = start.strip().upper(), end.strip().upper()
    valid = len(first) == len(last) == 1 and first.isascii() and last.isascii()
    if not valid or not first.isalpha() or not last.isalpha() or first > last:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="group_start and group_end must define an ascending A-Z range",
        )
    return [chr(code) for code in range(ord(first), ord(last) + 1)]


class TeachingGroupController(DomainController):
    """CRUD logic for teaching groups inside one assignment process."""

    @staticmethod
    def list_groups(session: Session, process_id: uuid.UUID) -> TeachingGroupsPublic:
        DomainController.get_process_or_404(session, process_id)
        items = list(
            session.exec(
                select(TeachingGroup).where(
                    TeachingGroup.assignment_process_id == process_id
                )
            ).all()
        )
        return TeachingGroupsPublic(
            data=[TeachingGroupController._public(session, item) for item in items],
            count=len(items),
        )

    @staticmethod
    def get_group(
        session: Session, process_id: uuid.UUID, group_id: uuid.UUID
    ) -> TeachingGroupPublic:
        return TeachingGroupController._public(
            session,
            TeachingGroupController._get_or_404(session, process_id, group_id),
        )

    @staticmethod
    def create_group(
        session: Session,
        process_id: uuid.UUID,
        group_in: TeachingGroupCreate,
        current_user: UserModel,
    ) -> TeachingGroupPublic:
        TeachingGroupController._prepare_process(session, process_id)
        if group_in.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payload assignment_process_id does not match the URL.",
            )
        stage = TeachingGroupController._stage_for_grade(
            session, group_in.classroom_stage_id, group_in.grade
        )
        values = group_in.model_dump(exclude={"label"})
        values["label"] = TeachingGroupController._resolved_label(
            group_in.label, group_in.grade, stage.label, group_in.group_code
        )
        group = TeachingGroup.model_validate(values)
        session.add(group)
        TeachingGroupController._audit(session, process_id, current_user, group)
        TeachingGroupController._commit(session)
        session.refresh(group)
        return TeachingGroupController._public(session, group)

    @staticmethod
    def update_group(
        session: Session,
        process_id: uuid.UUID,
        group_id: uuid.UUID,
        group_in: TeachingGroupUpdate,
        current_user: UserModel,
    ) -> TeachingGroupPublic:
        group = TeachingGroupController._get_or_404(session, process_id, group_id)
        TeachingGroupController._prepare_process(session, process_id)
        values = group_in.model_dump(exclude_unset=True)
        stage_id = values.get("classroom_stage_id", group.classroom_stage_id)
        grade = values.get("grade", group.grade)
        stage = TeachingGroupController._stage_for_grade(session, stage_id, grade)
        if "label" in values:
            values["label"] = TeachingGroupController._resolved_label(
                values["label"],
                grade,
                stage.label,
                values.get("group_code", group.group_code),
            )
        before = TeachingGroup.model_validate(group.model_dump())
        group.sqlmodel_update(values)
        session.add(group)
        TeachingGroupController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="teaching_group.updated",
            entity_type="teaching_group",
            entity_id=group.id,
            before=before,
            after=group,
        )
        TeachingGroupController._commit(session)
        session.refresh(group)
        return TeachingGroupController._public(session, group)

    @staticmethod
    def bulk_create(
        session: Session,
        process_id: uuid.UUID,
        bulk_in: TeachingGroupBulkCreate,
        current_user: UserModel,
    ) -> TeachingGroupsPublic:
        """Create a complete group range in one transaction."""
        TeachingGroupController._prepare_process(session, process_id)
        stage = TeachingGroupController._stage_for_grade(
            session, bulk_in.classroom_stage_id, bulk_in.grade
        )
        codes = generate_group_code_range(bulk_in.group_start, bulk_in.group_end)
        groups = [
            TeachingGroup(
                assignment_process_id=process_id,
                classroom_stage_id=stage.id,
                grade=bulk_in.grade,
                group_code=code,
                label=generate_classroom_label(
                    grade=bulk_in.grade,
                    stage_label=stage.label,
                    group_code=code,
                ),
            )
            for code in codes
        ]
        for group in groups:
            session.add(group)
            TeachingGroupController._audit(session, process_id, current_user, group)
        TeachingGroupController._commit(session)
        for group in groups:
            session.refresh(group)
        return TeachingGroupsPublic(
            data=[TeachingGroupController._public(session, item) for item in groups],
            count=len(groups),
        )

    @staticmethod
    def delete_group(
        session: Session,
        process_id: uuid.UUID,
        group_id: uuid.UUID,
        current_user: UserModel,
    ) -> TeachingGroupPublic:
        group = TeachingGroupController._get_or_404(session, process_id, group_id)
        TeachingGroupController._prepare_process(session, process_id)
        public = TeachingGroupController._public(session, group)
        before = TeachingGroup.model_validate(group.model_dump())
        session.delete(group)
        TeachingGroupController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="teaching_group.deleted",
            entity_type="teaching_group",
            entity_id=group.id,
            before=before,
            after=None,
        )
        session.commit()
        return public

    @staticmethod
    def _prepare_process(session: Session, process_id: uuid.UUID) -> None:
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )

    @staticmethod
    def _stage_for_grade(
        session: Session, stage_id: uuid.UUID, grade: int
    ) -> ClassroomStage:
        stage = session.get(ClassroomStage, stage_id)
        if stage is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"ClassroomStage {stage_id} not found.",
            )
        if not stage.min_grade <= grade <= stage.max_grade:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"grade must be between {stage.min_grade} and "
                    f"{stage.max_grade} for this classroom stage"
                ),
            )
        return stage

    @staticmethod
    def _resolved_label(
        value: object, grade: int, stage_label: str, group_code: str
    ) -> str:
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
        return generate_classroom_label(
            grade=grade, stage_label=stage_label, group_code=group_code
        )

    @staticmethod
    def _public(session: Session, group: TeachingGroup) -> TeachingGroupPublic:
        stage = session.get(ClassroomStage, group.classroom_stage_id)
        if stage is None:  # pragma: no cover - protected by the database FK
            raise RuntimeError("Teaching group references a missing classroom stage")
        return TeachingGroupPublic.model_validate(
            {
                **group.model_dump(),
                "classroom_stage": ClassroomStageSummary.model_validate(stage),
            }
        )

    @staticmethod
    def _audit(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
        group: TeachingGroup,
    ) -> None:
        TeachingGroupController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="teaching_group.created",
            entity_type="teaching_group",
            entity_id=group.id,
            before=None,
            after=group,
        )

    @staticmethod
    def _commit(session: Session) -> None:
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "classroom_conflict",
                    "message": "A classroom with this label already exists.",
                },
            ) from exc

    @staticmethod
    def _get_or_404(
        session: Session, process_id: uuid.UUID, group_id: uuid.UUID
    ) -> TeachingGroup:
        group = session.exec(
            select(TeachingGroup).where(TeachingGroup.id == group_id)
        ).first()
        if group is None or group.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"TeachingGroup {group_id} not found in process {process_id}.",
            )
        return group


__all__ = [
    "TeachingGroupController",
    "generate_classroom_label",
    "generate_group_code_range",
]
