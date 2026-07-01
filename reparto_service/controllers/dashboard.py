"""Dashboard controller.

The dashboard is a read-only composition of the per-process balances and
validation messages. It does not mutate any state.
"""

from __future__ import annotations

import uuid

from sqlmodel import Session

from reparto_service.controllers.base import DomainController
from reparto_service.schemas.summary import ProcessDashboard, ProcessSummary
from reparto_service.services.summary import SummaryService


class DashboardController(DomainController):
    """Read-only controller for the department-head dashboard."""

    @staticmethod
    def get_dashboard(session: Session, process_id: uuid.UUID) -> ProcessDashboard:
        DomainController.get_process_or_404(session, process_id)
        return SummaryService.compute_dashboard(session, process_id)

    @staticmethod
    def get_summary(session: Session, process_id: uuid.UUID) -> ProcessSummary:
        DomainController.get_process_or_404(session, process_id)
        return SummaryService.compute_summary(session, process_id)


__all__ = ["DashboardController"]
