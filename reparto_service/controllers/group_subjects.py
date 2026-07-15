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
from sqlmodel import Session, SQLModel, col, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.classroom_stages import ClassroomStage
from reparto_service.db_models.group_subjects import (
    GroupSubject,
    GroupSubjectBulkApplyRequest,
    GroupSubjectBulkChange,
    GroupSubjectBulkConflict,
    GroupSubjectBulkPreview,
    GroupSubjectBulkRequest,
    GroupSubjectBulkResult,
    GroupSubjectCreate,
    GroupSubjectPublic,
    GroupSubjectsPublic,
    GroupSubjectUpdate,
)
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teaching_groups import TeachingGroup
from reparto_service.enums import GroupSubjectBulkMode

# Planning-value fields a bulk operation may set on a cell.
_BULK_VALUE_FIELDS = (
    "group_weekly_hours",
    "teacher_weekly_hours_per_position",
    "required_teacher_count",
)


class _BulkAuditPayload(SQLModel):
    """Row-level detail recorded in the single ``bulk_applied`` audit event."""

    mode: str
    subject_id: str
    created: int
    updated: int
    rows: list[dict[str, object]]


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

    # ── Bulk preview / apply (plan §7.2, §8.4) ───────────────────────────────

    @staticmethod
    def bulk_preview(
        session: Session,
        process_id: uuid.UUID,
        request: GroupSubjectBulkRequest,
    ) -> GroupSubjectBulkPreview:
        """Dry-run a bulk operation without writing anything (plan §7.2)."""
        DomainController.get_process_or_404(session, process_id)
        GroupSubjectController._get_subject_or_404(
            session, process_id, request.subject_id
        )
        preview, _create_specs, _update_specs = GroupSubjectController._plan_bulk(
            session, process_id, request
        )
        return preview

    @staticmethod
    def bulk_apply(
        session: Session,
        process_id: uuid.UUID,
        request: GroupSubjectBulkApplyRequest,
        current_user: UserModel,
    ) -> GroupSubjectBulkResult:
        """Transactionally apply a previewed bulk operation (plan §7.2)."""
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        subject = GroupSubjectController._get_subject_or_404(
            session, process_id, request.subject_id
        )
        preview, create_specs, update_specs = GroupSubjectController._plan_bulk(
            session, process_id, request
        )
        if preview.validation_errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="; ".join(preview.validation_errors),
            )
        # Staleness guard: the confirmed count must still match the recomputed
        # plan, otherwise the underlying selection changed since preview.
        if preview.expected_affected_count != request.expected_affected_count:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Bulk selection changed since preview "
                    f"(now {preview.expected_affected_count} affected, "
                    f"confirmed {request.expected_affected_count}); re-preview."
                ),
            )
        affected: list[GroupSubject] = []
        rows_detail: list[dict[str, object]] = []
        for group_id, values in create_specs:
            row = GroupSubject(
                assignment_process_id=process_id,
                teaching_group_id=group_id,
                subject_id=subject.id,
                **values,
            )
            session.add(row)
            affected.append(row)
            rows_detail.append(
                {
                    "action": "create",
                    "teaching_group_id": str(group_id),
                    "after": values,
                }
            )
        for row, patch in update_specs:
            before = {field: getattr(row, field) for field in _BULK_VALUE_FIELDS}
            row.sqlmodel_update(patch)
            session.add(row)
            affected.append(row)
            rows_detail.append(
                {
                    "action": "update",
                    "group_subject_id": str(row.id),
                    "teaching_group_id": str(row.teaching_group_id),
                    "before": before,
                    "after": {**before, **patch},
                }
            )
        GroupSubjectController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="group_subject.bulk_applied",
            entity_type="group_subject",
            entity_id=subject.id,
            before=None,
            after=_BulkAuditPayload(
                mode=request.mode.value,
                subject_id=str(subject.id),
                created=len(create_specs),
                updated=len(update_specs),
                rows=rows_detail,
            ),
        )
        session.commit()
        for row in affected:
            session.refresh(row)
        return GroupSubjectBulkResult(
            created_count=len(create_specs),
            updated_count=len(update_specs),
            data=[GroupSubjectPublic.model_validate(row) for row in affected],
            count=len(affected),
        )

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

    @staticmethod
    def _plan_bulk(
        session: Session,
        process_id: uuid.UUID,
        request: GroupSubjectBulkRequest,
    ) -> tuple[
        GroupSubjectBulkPreview,
        list[tuple[uuid.UUID, dict[str, object]]],
        list[tuple[GroupSubject, dict[str, object]]],
    ]:
        """Compute the create/update/unchanged/conflict split for a bulk request.

        Returns the preview alongside the concrete work lists apply consumes:
        ``create_specs`` as ``(teaching_group_id, create_values)`` and
        ``update_specs`` as ``(existing_row, patch)``.
        """
        groups, errors = GroupSubjectController._matched_groups(
            session, process_id, request
        )
        existing = {
            row.teaching_group_id: row
            for row in session.exec(
                select(GroupSubject).where(
                    GroupSubject.assignment_process_id == process_id,
                    GroupSubject.subject_id == request.subject_id,
                )
            ).all()
        }
        provided = {
            field: getattr(request, field)
            for field in _BULK_VALUE_FIELDS
            if field in request.model_fields_set
        }
        create_values = GroupSubjectController._create_values(provided)

        to_create: list[GroupSubjectBulkChange] = []
        to_update: list[GroupSubjectBulkChange] = []
        unchanged: list[GroupSubjectBulkChange] = []
        conflicts: list[GroupSubjectBulkConflict] = []
        create_specs: list[tuple[uuid.UUID, dict[str, object]]] = []
        update_specs: list[tuple[GroupSubject, dict[str, object]]] = []

        for group in groups:
            row = existing.get(group.id)
            if row is None:
                if request.mode == GroupSubjectBulkMode.UPDATE_EXISTING:
                    conflicts.append(
                        GroupSubjectBulkConflict(
                            teaching_group_id=group.id,
                            reason="No existing group-subject row to update.",
                        )
                    )
                    continue
                create_specs.append((group.id, create_values))
                to_create.append(
                    GroupSubjectBulkChange(teaching_group_id=group.id, **create_values)
                )
                continue
            if request.mode == GroupSubjectBulkMode.CREATE_MISSING:
                unchanged.append(GroupSubjectController._row_change(row))
                continue
            patch = {
                field: value
                for field, value in provided.items()
                if getattr(row, field) != value
            }
            if patch:
                update_specs.append((row, patch))
                to_update.append(GroupSubjectController._row_change(row, patch))
            else:
                unchanged.append(GroupSubjectController._row_change(row))

        preview = GroupSubjectBulkPreview(
            mode=request.mode,
            subject_id=request.subject_id,
            matched_group_ids=[group.id for group in groups],
            to_create=to_create,
            to_update=to_update,
            unchanged=unchanged,
            conflicts=conflicts,
            validation_errors=errors,
            expected_affected_count=len(to_create) + len(to_update),
        )
        return preview, create_specs, update_specs

    @staticmethod
    def _matched_groups(
        session: Session,
        process_id: uuid.UUID,
        request: GroupSubjectBulkRequest,
    ) -> tuple[list[TeachingGroup], list[str]]:
        """Resolve the groups a bulk request targets and any selection errors."""
        errors: list[str] = []
        if (
            request.minimum_grade is not None
            and request.maximum_grade is not None
            and request.minimum_grade > request.maximum_grade
        ):
            errors.append("minimum_grade must be less than or equal to maximum_grade.")
            return [], errors
        statement = select(TeachingGroup).where(
            TeachingGroup.assignment_process_id == process_id
        )
        if request.minimum_grade is not None:
            statement = statement.where(
                col(TeachingGroup.grade) >= request.minimum_grade
            )
        if request.maximum_grade is not None:
            statement = statement.where(
                col(TeachingGroup.grade) <= request.maximum_grade
            )
        if request.stage is not None:
            stage = " ".join(request.stage.split())
            statement = statement.join(
                ClassroomStage,
                col(TeachingGroup.classroom_stage_id) == col(ClassroomStage.id),
            ).where(ClassroomStage.stage == stage)
        statement = statement.order_by(
            col(TeachingGroup.grade), col(TeachingGroup.group_code)
        )
        return list(session.exec(statement).all()), errors

    @staticmethod
    def _create_values(provided: dict[str, object]) -> dict[str, object]:
        """Resolve the concrete field values for a newly created cell.

        Unset hour fields inherit (NULL); an unset count falls back to 1.
        """
        return {
            "group_weekly_hours": provided.get("group_weekly_hours"),
            "teacher_weekly_hours_per_position": provided.get(
                "teacher_weekly_hours_per_position"
            ),
            "required_teacher_count": provided.get("required_teacher_count", 1),
        }

    @staticmethod
    def _row_change(
        row: GroupSubject, patch: dict[str, object] | None = None
    ) -> GroupSubjectBulkChange:
        """Build a preview change carrying a row's resulting field values."""
        values = {field: getattr(row, field) for field in _BULK_VALUE_FIELDS}
        if patch:
            values.update(patch)
        return GroupSubjectBulkChange(
            teaching_group_id=row.teaching_group_id,
            group_subject_id=row.id,
            **values,
        )


__all__ = ["GroupSubjectController"]
