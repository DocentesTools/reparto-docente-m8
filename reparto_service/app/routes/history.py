"""History and export routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
from reparto_service.controllers.history import HistoryController
from reparto_service.db_models.assignment_processes import AssignmentProcessPublic
from reparto_service.db_models.export_artifacts import (
    ExportBackupRestoreRequest,
    ExportArtifactCreate,
    ExportArtifactPublic,
    ExportArtifactsPublic,
)
from reparto_service.db_models.process_versions import (
    ProcessVersionCreate,
    ProcessVersionPublic,
    ProcessVersionsPublic,
    VersionComparison,
)

router = APIRouter(
    prefix="/assignment-processes/{process_id}",
    tags=["history"],
)


@router.get("/versions", response_model=ProcessVersionsPublic)
def list_versions(session: SessionDep, process_id: uuid.UUID) -> ProcessVersionsPublic:
    return HistoryController.list_versions(session, process_id)


@router.post("/versions", response_model=ProcessVersionPublic, status_code=201)
def create_version(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    payload: ProcessVersionCreate,
) -> ProcessVersionPublic:
    HistoryController.require_process_writer(session, current_user, process_id)
    return HistoryController.create_version(session, process_id, current_user, payload)


@router.get(
    "/versions/{left_version_id}/compare/{right_version_id}",
    response_model=VersionComparison,
)
def compare_versions(
    session: SessionDep,
    process_id: uuid.UUID,
    left_version_id: uuid.UUID,
    right_version_id: uuid.UUID,
) -> VersionComparison:
    return HistoryController.compare_versions(
        session, process_id, left_version_id, right_version_id
    )


@router.get("/compare-previous-year", response_model=VersionComparison)
def compare_previous_year(
    session: SessionDep, process_id: uuid.UUID
) -> VersionComparison:
    return HistoryController.compare_previous_year(session, process_id)


@router.get("/exports", response_model=ExportArtifactsPublic)
def list_artifacts(session: SessionDep, process_id: uuid.UUID) -> ExportArtifactsPublic:
    return HistoryController.list_artifacts(session, process_id)


@router.post("/exports", response_model=ExportArtifactPublic, status_code=201)
def create_artifact(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    payload: ExportArtifactCreate,
) -> ExportArtifactPublic:
    HistoryController.require_process_writer(session, current_user, process_id)
    return HistoryController.create_artifact(session, process_id, current_user, payload)


@router.post("/restore-draft", response_model=AssignmentProcessPublic, status_code=201)
def restore_backup_to_draft(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    payload: ExportBackupRestoreRequest,
) -> AssignmentProcessPublic:
    HistoryController.require_process_writer(session, current_user, process_id)
    return HistoryController.restore_backup_to_draft(
        session, process_id, current_user, payload
    )
