"""History and export routes.

Every read here is a department-head read (remediation `W7.1`). A version
snapshot is a whole-process dump — :mod:`reparto_service.controllers.history`
restores ``extra_hours_reason`` out of one, so the head's written justification
is inside the row a version list describes and inside both sides of a
comparison. The export list names the artefacts built from that same data and
who built them. None of it is a weaker tier than the dashboard `W5.3` narrowed;
it is the same tier, read after the fact.

The mutations were already administrator-only, so this file's floor is now
uniform: creating a version, creating an artefact and restoring a backup sit
beside listing and comparing them.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from reparto_service.app.deps import CurrentAdmin, SessionDep, require_visible_process
from reparto_service.controllers.history import HistoryController
from reparto_service.controllers.process_versions import ProcessVersionController
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
    # Read scope (plan §21.4): every route under this process — read and
    # mutation alike — is refused with 404 when the process lies outside the
    # caller's departments.
    dependencies=[Depends(require_visible_process)],
)


@router.get("/versions", response_model=ProcessVersionsPublic)
def list_versions(
    session: SessionDep, current_user: CurrentAdmin, process_id: uuid.UUID
) -> ProcessVersionsPublic:
    """List the snapshots only for an administrator (`W7.1`)."""
    return ProcessVersionController.list_versions(session, process_id)


@router.post("/versions", response_model=ProcessVersionPublic, status_code=201)
def create_version(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    payload: ProcessVersionCreate,
) -> ProcessVersionPublic:
    return ProcessVersionController.create_version(
        session, process_id, current_user, payload
    )


@router.get(
    "/versions/{left_version_id}/compare/{right_version_id}",
    response_model=VersionComparison,
)
def compare_versions(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    left_version_id: uuid.UUID,
    right_version_id: uuid.UUID,
) -> VersionComparison:
    """Compare two named snapshots, for an administrator only (`W7.1`).

    The same ``VersionComparison`` payload as ``compare-previous-year`` below,
    reached by naming both sides instead of one. Leaving this route at the
    reader floor while that one moved would have been a hole rather than a
    decision, so `W7.1` covers both.
    """
    return ProcessVersionController.compare_versions(
        session, process_id, left_version_id, right_version_id
    )


@router.get("/compare-previous-year", response_model=VersionComparison)
def compare_previous_year(
    session: SessionDep, current_user: CurrentAdmin, process_id: uuid.UUID
) -> VersionComparison:
    """Compare against last year's snapshot, for an administrator only (`W7.1`)."""
    return ProcessVersionController.compare_previous_year(session, process_id)


@router.get("/exports", response_model=ExportArtifactsPublic)
def list_artifacts(
    session: SessionDep, current_user: CurrentAdmin, process_id: uuid.UUID
) -> ExportArtifactsPublic:
    """List the artefacts only for an administrator (`W7.1`).

    Creating one was already administrator-only; the inventory of what was
    built, when and by whom is the same tier as the artefacts themselves.
    """
    return HistoryController.list_artifacts(session, process_id)


@router.post("/exports", response_model=ExportArtifactPublic, status_code=201)
def create_artifact(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    payload: ExportArtifactCreate,
) -> ExportArtifactPublic:
    return HistoryController.create_artifact(session, process_id, current_user, payload)


@router.post("/restore-draft", response_model=AssignmentProcessPublic, status_code=201)
def restore_backup_to_draft(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    payload: ExportBackupRestoreRequest,
) -> AssignmentProcessPublic:
    return HistoryController.restore_backup_to_draft(
        session, process_id, current_user, payload
    )
