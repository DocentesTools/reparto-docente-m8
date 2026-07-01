"""Assignment controller.

The assignment is the central mutation: it links a process teacher to an
hour requirement. Controllers enforce the documented rules from the
first-slice plan:

* ``assigned_hours`` must be > 0 (schema-enforced).
* Sum of ``assigned_hours`` for a requirement must not exceed
  ``required_hours`` unless at least one assignment on the requirement
  carries a department head override.
* Mutations are blocked when the parent process is in a final state.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.assignments import (
    Assignment,
    AssignmentCreate,
    AssignmentPublic,
    AssignmentsPublic,
    AssignmentUpdate,
)
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.enums import (
    AssignmentProcessStatus,
    AssignmentStatus,
)


class AssignmentController(DomainController):
    """CRUD logic for assignments inside one assignment process."""

    @staticmethod
    def list_assignments(session: Session, process_id: uuid.UUID) -> AssignmentsPublic:
        DomainController.get_process_or_404(session, process_id)
        statement = select(Assignment).where(
            Assignment.assignment_process_id == process_id
        )
        items = list(session.exec(statement).all())
        return AssignmentsPublic(
            data=[AssignmentPublic.model_validate(item) for item in items],
            count=len(items),
        )

    @staticmethod
    def get_assignment(
        session: Session, process_id: uuid.UUID, assignment_id: uuid.UUID
    ) -> AssignmentPublic:
        assignment = AssignmentController._get_or_404(
            session, process_id, assignment_id
        )
        return AssignmentPublic.model_validate(assignment)

    @staticmethod
    def create_assignment(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
        assignment_in: AssignmentCreate,
    ) -> AssignmentPublic:
        process = AssignmentController._ensure_open(
            session, process_id, assignment_in.assignment_process_id
        )
        AssignmentController._get_requirement_or_404(
            session, process_id, assignment_in.hour_requirement_id
        )
        AssignmentController._get_process_teacher_or_404(
            session, process_id, assignment_in.process_teacher_id
        )
        # Enforce over-assignment cap unless this row itself records an override.
        AssignmentController._enforce_requirement_cap(
            session=session,
            process=process,
            requirement_id=assignment_in.hour_requirement_id,
            incoming_hours=assignment_in.assigned_hours,
            incoming_has_override=assignment_in.override_reason is not None,
        )
        assignment = Assignment.model_validate(
            assignment_in.model_dump(),
            update={"chosen_by_user_id": current_user.id},
        )
        session.add(assignment)
        session.commit()
        session.refresh(assignment)
        return AssignmentPublic.model_validate(assignment)

    @staticmethod
    def update_assignment(
        session: Session,
        process_id: uuid.UUID,
        assignment_id: uuid.UUID,
        assignment_in: AssignmentUpdate,
        current_user: UserModel,
    ) -> AssignmentPublic:
        del current_user
        process = AssignmentController._ensure_open(session, process_id)
        assignment = AssignmentController._get_or_404(
            session, process_id, assignment_id
        )
        update_dict = assignment_in.model_dump(exclude_unset=True)
        new_hours = update_dict.get("assigned_hours", assignment.assigned_hours)
        new_override = update_dict.get("override_reason", assignment.override_reason)
        # Re-check the cap with the post-update hours + override.
        AssignmentController._enforce_requirement_cap(
            session=session,
            process=process,
            requirement_id=assignment.hour_requirement_id,
            incoming_hours=new_hours,
            incoming_has_override=new_override is not None,
            exclude_assignment_id=assignment.id,
        )
        assignment.sqlmodel_update(update_dict)
        session.add(assignment)
        session.commit()
        session.refresh(assignment)
        return AssignmentPublic.model_validate(assignment)

    @staticmethod
    def delete_assignment(
        session: Session,
        process_id: uuid.UUID,
        assignment_id: uuid.UUID,
    ) -> AssignmentPublic:
        AssignmentController._ensure_open(session, process_id)
        assignment = AssignmentController._get_or_404(
            session, process_id, assignment_id
        )
        session.delete(assignment)
        session.commit()
        return AssignmentPublic.model_validate(assignment)

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _get_or_404(
        session: Session, process_id: uuid.UUID, assignment_id: uuid.UUID
    ) -> Assignment:
        DomainController.get_process_or_404(session, process_id)
        statement = select(Assignment).where(Assignment.id == assignment_id)
        assignment = session.exec(statement).first()
        if assignment is None or assignment.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Assignment {assignment_id} not found in process {process_id}."
                ),
            )
        return assignment

    @staticmethod
    def _get_requirement_or_404(
        session: Session, process_id: uuid.UUID, requirement_id: uuid.UUID
    ) -> HourRequirement:
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
    def _get_process_teacher_or_404(
        session: Session, process_id: uuid.UUID, process_teacher_id: uuid.UUID
    ) -> ProcessTeacher:
        statement = select(ProcessTeacher).where(
            ProcessTeacher.id == process_teacher_id
        )
        process_teacher = session.exec(statement).first()
        if (
            process_teacher is None
            or process_teacher.assignment_process_id != process_id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"ProcessTeacher {process_teacher_id} not found in "
                    f"process {process_id}."
                ),
            )
        return process_teacher

    @staticmethod
    def _ensure_open(
        session: Session,
        process_id: uuid.UUID,
        payload_process_id: uuid.UUID | None = None,
    ) -> AssignmentProcess:
        if payload_process_id is not None and payload_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "assignment_process_id in the payload does not match the "
                    "URL process_id."
                ),
            )
        process = DomainController.get_process_or_404(session, process_id)
        if process.status in {
            AssignmentProcessStatus.FINAL,
            AssignmentProcessStatus.ARCHIVED,
        }:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot mutate assignments on a process in status "
                    f"{process.status.value}."
                ),
            )
        return process

    @staticmethod
    def _enforce_requirement_cap(
        *,
        session: Session,
        process: AssignmentProcess,
        requirement_id: uuid.UUID,
        incoming_hours: float,
        incoming_has_override: bool,
        exclude_assignment_id: uuid.UUID | None = None,
    ) -> None:
        """Block the mutation when it would push the requirement past the cap.

        Cap rule (plan 9.3, 8.10): the sum of ``assigned_hours`` for a
        requirement must not exceed ``required_hours`` unless at least
        one assignment on the requirement (after the mutation) carries
        a department head override. Cancellations are excluded from
        the sum.
        """
        statement = select(Assignment).where(
            Assignment.hour_requirement_id == requirement_id
        )
        rows = list(session.exec(statement).all())
        totals_by_requirement: dict[uuid.UUID, tuple[float, bool]] = {}
        for row in rows:
            if row.status == AssignmentStatus.CANCELLED:
                continue
            if exclude_assignment_id is not None and row.id == exclude_assignment_id:
                continue
            current, has_override = totals_by_requirement.get(
                row.hour_requirement_id, (0.0, False)
            )
            totals_by_requirement[row.hour_requirement_id] = (
                current + row.assigned_hours,
                has_override or row.override_reason is not None,
            )
        current_hours, has_override = totals_by_requirement.get(
            requirement_id, (0.0, False)
        )
        projected = current_hours + incoming_hours
        # Fetch the requirement to know its cap.
        requirement = session.get(HourRequirement, requirement_id)
        if requirement is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"HourRequirement {requirement_id} not found.",
            )
        cap = requirement.required_hours
        if projected <= cap:
            return
        if incoming_has_override or has_override:
            return
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Assignment would push requirement {requirement_id} above its "
                f"required hours ({projected:.2f} > {cap:.2f}). "
                "Provide an override_reason to exceed the cap."
            ),
        )


__all__ = ["AssignmentController"]
