"""API tests for the adapted version snapshot / comparison routes (plan §10.2/§10.3).

Cover :class:`~reparto_service.controllers.process_versions.ProcessVersionController`
through its routes: creating and listing versions, comparing two stored versions,
previous-year comparison and the writer gate. The comparison assertions key off
the three-stage §10.3 dimensions produced by the new snapshot service.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.enums import SubjectAllocationCategory, TeachingPlanStatus
from tests import factories


def test_create_version_stores_three_stage_snapshot(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_allocation_revision(
        session, process, allocated_group_weekly_hours=120.0
    )
    factories.make_teaching_plan(session, process, status=TeachingPlanStatus.LOCKED)

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/versions",
        json={"reason": "baseline"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["version_number"] == 1
    snapshot = body["snapshot_json"]
    assert snapshot["current_allocation"] == "120.00"
    assert snapshot["teaching_plan"]["status"] == TeachingPlanStatus.LOCKED
    assert snapshot["plan_balance"] is not None


def test_list_versions_endpoint(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    client.post(
        f"/reparto/assignment-processes/{process.id}/versions",
        json={"reason": "one"},
    )
    client.post(
        f"/reparto/assignment-processes/{process.id}/versions",
        json={"reason": "two"},
    )

    resp = client.get(f"/reparto/assignment-processes/{process.id}/versions")

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert [row["version_number"] for row in body["data"]] == [1, 2]


def test_compare_versions_reports_dimensions(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    first = client.post(
        f"/reparto/assignment-processes/{process.id}/versions",
        json={"reason": "baseline"},
    ).json()
    profile = factories.make_teacher_profile(session)
    factories.make_process_teacher(session, process, profile, base_weekly_hours=18.0)
    second = client.post(
        f"/reparto/assignment-processes/{process.id}/versions",
        json={"reason": "teacher added"},
    ).json()

    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/versions/"
        f"{first['id']}/compare/{second['id']}"
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["teacher_count_delta"] == 1
    assert body["participant_target_changed"] is True
    assert body["participant_target_total_delta"] == "18.00"
    assert "teachers" in body["changed_sections"]


def test_compare_versions_404_for_missing_version(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    version = client.post(
        f"/reparto/assignment-processes/{process.id}/versions",
        json={"reason": "baseline"},
    ).json()

    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/versions/"
        f"{version['id']}/compare/{uuid.uuid4()}"
    )

    assert resp.status_code == 404


def test_compare_versions_404_for_wrong_process_version(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    other = factories.make_assignment_process(session)
    mine = client.post(
        f"/reparto/assignment-processes/{process.id}/versions",
        json={"reason": "mine"},
    ).json()
    theirs = client.post(
        f"/reparto/assignment-processes/{other.id}/versions",
        json={"reason": "theirs"},
    ).json()

    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/versions/"
        f"{mine['id']}/compare/{theirs['id']}"
    )

    assert resp.status_code == 404


def test_compare_previous_year_requires_source(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)

    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/compare-previous-year"
    )

    assert resp.status_code == 400
    assert "previous-year" in resp.json()["detail"]


def test_compare_previous_year_success(client: TestClient, session: Session) -> None:
    source = factories.make_assignment_process(session)
    factories.make_allocation_revision(
        session, source, allocated_group_weekly_hours=100.0
    )
    factories.make_subject(
        session, source, allocation_category=SubjectAllocationCategory.MAIN
    )
    target = factories.make_assignment_process(session)
    target.created_from_process_id = source.id
    session.add(target)
    session.commit()

    resp = client.get(
        f"/reparto/assignment-processes/{target.id}/compare-previous-year"
    )

    assert resp.status_code == 200
    body = resp.json()
    # The source carries an allocation and a subject the empty target does not.
    assert body["allocation_changed"] is True
    assert body["allocation_delta"] is None
    assert "subjects" in body["changed_sections"]


def test_compare_previous_year_missing_process_404(
    client: TestClient, session: Session
) -> None:
    resp = client.get(
        f"/reparto/assignment-processes/{uuid.uuid4()}/compare-previous-year"
    )

    assert resp.status_code == 404


def test_list_versions_missing_process_404(
    client: TestClient, session: Session
) -> None:
    resp = client.get(f"/reparto/assignment-processes/{uuid.uuid4()}/versions")

    assert resp.status_code == 404


def test_reader_cannot_create_version(
    reader_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)

    resp = reader_client.post(
        f"/reparto/assignment-processes/{process.id}/versions",
        json={"reason": "reader"},
    )

    assert resp.status_code == 403
