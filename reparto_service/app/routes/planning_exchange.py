"""Planning import/export routes (nested under an assignment process, plan §7.8).

Exposes the three §7.8 planning-exchange operations:

* ``POST .../exports/planning-draft`` and ``.../exports/planning-provisional`` —
  never blocked by an inexact/stale plan (plan §3.10);
* ``POST .../exports/planning-final`` — retains blocking validation (plan §7.8);
* ``POST .../imports/planning`` — validated, assignment-free ingestion (plan §7.8).

Reads (the export artifact) are available to any authenticated caller; the import
mutates the plan and is writer-gated.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
from reparto_service.controllers.planning_exchange import PlanningExchangeController
from reparto_service.enums import PlanningExportMode
from reparto_service.schemas.exchange import (
    PlanningExportArtifact,
    PlanningImportRequest,
    PlanningImportResult,
)

router = APIRouter(
    prefix="/assignment-processes/{process_id}",
    tags=["planning-exchange"],
)


@router.post("/exports/planning-draft", response_model=PlanningExportArtifact)
def export_planning_draft(
    session: SessionDep, process_id: uuid.UUID
) -> PlanningExportArtifact:
    return PlanningExchangeController.export_planning(
        session, process_id, PlanningExportMode.DRAFT
    )


@router.post("/exports/planning-provisional", response_model=PlanningExportArtifact)
def export_planning_provisional(
    session: SessionDep, process_id: uuid.UUID
) -> PlanningExportArtifact:
    return PlanningExchangeController.export_planning(
        session, process_id, PlanningExportMode.PROVISIONAL
    )


@router.post("/exports/planning-final", response_model=PlanningExportArtifact)
def export_planning_final(
    session: SessionDep, process_id: uuid.UUID
) -> PlanningExportArtifact:
    return PlanningExchangeController.export_planning(
        session, process_id, PlanningExportMode.FINAL
    )


@router.post("/imports/planning", response_model=PlanningImportResult)
def import_planning(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    payload: PlanningImportRequest,
) -> PlanningImportResult:
    PlanningExchangeController.require_process_writer(session, current_user, process_id)
    return PlanningExchangeController.import_planning(
        session, process_id, payload, current_user
    )
