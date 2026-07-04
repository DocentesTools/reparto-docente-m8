"""API tests for Phase 5 versions and export artifacts."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.enums import AssignmentProcessStatus
from tests import factories


def test_create_and_compare_versions(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    first = client.post(
        f"/reparto/assignment-processes/{process.id}/versions",
        json={"reason": "baseline"},
    )
    assert first.status_code == 201
    profile = factories.make_teacher_profile(session)
    factories.make_process_teacher(session, process, profile, available_hours=18)
    second = client.post(
        f"/reparto/assignment-processes/{process.id}/versions",
        json={"reason": "teacher added"},
    )

    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/versions/"
        f"{first.json()['id']}/compare/{second.json()['id']}"
    )

    assert second.status_code == 201
    assert resp.status_code == 200
    assert resp.json()["teacher_count_delta"] == 1
    assert "teachers" in resp.json()["changed_sections"]


def test_list_versions_endpoint(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    created = client.post(
        f"/reparto/assignment-processes/{process.id}/versions",
        json={"reason": "baseline"},
    )

    resp = client.get(f"/reparto/assignment-processes/{process.id}/versions")

    assert created.status_code == 201
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_compare_versions_returns_404_for_missing_version(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)

    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/versions/"
        f"{uuid.uuid4()}/compare/{uuid.uuid4()}"
    )

    assert resp.status_code == 404


def test_create_json_backup_artifact(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/exports",
        json={"export_type": "backup", "format": "json"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["checksum"]
    assert body["file_path"].endswith(".json")
    assert '"process"' in body["content"]


def test_list_artifacts_endpoint(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    created = client.post(
        f"/reparto/assignment-processes/{process.id}/exports",
        json={"export_type": "backup", "format": "json"},
    )

    resp = client.get(f"/reparto/assignment-processes/{process.id}/exports")

    assert created.status_code == 201
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_create_csv_export_with_version(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    teacher = factories.make_process_teacher(session, process, profile)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(session, process, group, subject)
    factories.make_assignment(session, process, requirement, teacher)
    version = client.post(
        f"/reparto/assignment-processes/{process.id}/versions",
        json={"reason": "csv"},
    ).json()

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/exports",
        json={
            "export_type": "internal_draft",
            "format": "csv",
            "process_version_id": version["id"],
        },
    )

    assert resp.status_code == 201
    assert resp.json()["content"].startswith("section,id,hours,status")


def test_create_export_returns_404_for_wrong_version(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/exports",
        json={
            "export_type": "backup",
            "format": "json",
            "process_version_id": str(uuid.uuid4()),
        },
    )

    assert resp.status_code == 404


def test_final_export_blocked_by_validations(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    factories.make_hour_requirement(session, process, group, subject)

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/exports",
        json={"export_type": "final", "format": "pdf"},
    )

    assert resp.status_code == 400
    assert "blocking validations" in resp.json()["detail"]


def test_final_export_archives_balanced_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    teacher = factories.make_process_teacher(session, process, profile)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(session, process, group, subject)
    factories.make_assignment(session, process, requirement, teacher)

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/exports",
        json={"export_type": "final", "format": "pdf"},
    )

    assert resp.status_code == 201
    session.refresh(process)
    assert process.status == AssignmentProcessStatus.ARCHIVED


def test_compare_previous_year_requires_source(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)

    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/compare-previous-year"
    )

    assert resp.status_code == 400


def test_compare_previous_year_success(client: TestClient, session: Session) -> None:
    source = factories.make_assignment_process(session)
    target = factories.make_assignment_process(session)
    target.created_from_process_id = source.id
    session.add(target)
    session.commit()

    resp = client.get(
        f"/reparto/assignment-processes/{target.id}/compare-previous-year"
    )

    assert resp.status_code == 200
    assert resp.json()["teacher_count_delta"] == 0


def test_reader_cannot_create_version(
    reader_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)

    resp = reader_client.post(
        f"/reparto/assignment-processes/{process.id}/versions",
        json={"reason": "reader"},
    )

    assert resp.status_code == 403
