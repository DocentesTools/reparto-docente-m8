"""HourRequirement controller (per process) — read-only.

Redesigned for the three-stage adaptation (plan §5.9, §20.8, §20.12):
``HourRequirement`` rows are **generated** teacher-position slots, never manually
created, updated or deleted. This controller therefore exposes read access only.

The requirement generation / reconciliation flows that produce and retire these
rows (``generation-preview`` / ``generate`` / ``reconciliation-preview`` /
``reconcile``, plan §7.5) are their own later tasks; until then rows are created
directly by the generation service (and by tests via the factory).
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.hour_requirements import (
    HourRequirement,
    HourRequirementPublic,
    HourRequirementsPublic,
)


class HourRequirementController(DomainController):
    """Read-only access to the generated requirement slots of one process."""

    @staticmethod
    def list_requirements(
        session: Session, process_id: uuid.UUID
    ) -> HourRequirementsPublic:
        DomainController.get_process_or_404(session, process_id)
        statement = (
            select(HourRequirement)
            .where(HourRequirement.assignment_process_id == process_id)
            .order_by(
                col(HourRequirement.teaching_activity_id),
                col(HourRequirement.position_index),
            )
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


__all__ = ["HourRequirementController"]
