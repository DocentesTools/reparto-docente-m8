"""Tests for the redesigned HourRequirement slot model and read routes.

Plan §5.9 / §20.8: an ``HourRequirement`` is a generated, indivisible
teacher-position slot with stable-id + generation-lineage identity. Rows are
generated (here, inserted via the factory), never manually created or deleted
through the API — only the read endpoints exist (plan §5.9, §20.12, §7.5).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from reparto_service.enums import HourRequirementStatus
from tests import factories


def _setup(session: Session):
    """Create a process + plan + subject + one activity to hang slots on."""
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    activity = factories.make_teaching_activity(
        session, plan, subject, required_teacher_count=2
    )
    return process, plan, subject, activity


# ── Model / factory ───────────────────────────────────────────────────────────


def test_factory_defaults(session: Session) -> None:
    process, _plan, _subject, activity = _setup(session)
    slot = factories.make_hour_requirement(session, process, activity)
    assert slot.position_index == 0
    assert slot.required_teacher_hours == 4.0
    assert slot.status == HourRequirementStatus.AVAILABLE
    assert slot.created_generation == 1
    assert slot.last_validated_generation == 1
    assert slot.retired_generation is None
    assert slot.superseded_by_requirement_id is None


def test_active_slot_uniqueness_blocks_duplicate_position(session: Session) -> None:
    """Two live rows for the same (activity, position_index) are rejected."""
    process, _plan, _subject, activity = _setup(session)
    factories.make_hour_requirement(session, process, activity, position_index=0)
    with pytest.raises(IntegrityError):
        factories.make_hour_requirement(session, process, activity, position_index=0)
    session.rollback()


def test_retired_slot_frees_its_position(session: Session) -> None:
    """A retired row does not block a live successor on the same slot."""
    process, _plan, _subject, activity = _setup(session)
    retired = factories.make_hour_requirement(
        session,
        process,
        activity,
        position_index=0,
        retired_generation=1,
        status=HourRequirementStatus.STALE,
    )
    live = factories.make_hour_requirement(
        session,
        process,
        activity,
        position_index=0,
        created_generation=2,
        last_validated_generation=2,
    )
    assert retired.retired_generation == 1
    assert live.retired_generation is None
    assert live.id != retired.id


def test_supersession_links_new_row(session: Session) -> None:
    process, _plan, _subject, activity = _setup(session)
    successor = factories.make_hour_requirement(
        session, process, activity, position_index=1
    )
    superseded = factories.make_hour_requirement(
        session,
        process,
        activity,
        position_index=0,
        retired_generation=2,
        status=HourRequirementStatus.RECONCILIATION_REQUIRED,
        superseded_by_requirement_id=successor.id,
    )
    assert superseded.superseded_by_requirement_id == successor.id


# ── Read routes ───────────────────────────────────────────────────────────────


def test_list_requirements_ordered(client: TestClient, session: Session) -> None:
    process, _plan, _subject, activity = _setup(session)
    factories.make_hour_requirement(session, process, activity, position_index=1)
    factories.make_hour_requirement(session, process, activity, position_index=0)
    resp = client.get(f"/reparto/assignment-processes/{process.id}/requirements/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    positions = [row["position_index"] for row in body["data"]]
    assert positions == [0, 1]
    assert body["data"][0]["status"] == HourRequirementStatus.AVAILABLE.value
    assert body["data"][0]["teaching_activity_id"] == str(activity.id)


def test_list_requirements_empty(client: TestClient, session: Session) -> None:
    process, _plan, _subject, _activity = _setup(session)
    resp = client.get(f"/reparto/assignment-processes/{process.id}/requirements/")
    assert resp.status_code == 200
    assert resp.json() == {"data": [], "count": 0}


def test_list_requirements_unknown_process(client: TestClient) -> None:
    resp = client.get(f"/reparto/assignment-processes/{uuid.uuid4()}/requirements/")
    assert resp.status_code == 404


def test_get_requirement(client: TestClient, session: Session) -> None:
    process, _plan, _subject, activity = _setup(session)
    slot = factories.make_hour_requirement(
        session, process, activity, required_teacher_hours=2.5
    )
    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/requirements/{slot.id}"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(slot.id)
    assert body["required_teacher_hours"] == 2.5
    assert body["retired_generation"] is None
    assert body["superseded_by_requirement_id"] is None


def test_get_requirement_not_found(client: TestClient, session: Session) -> None:
    process, _plan, _subject, _activity = _setup(session)
    resp = client.get(
        f"/reparto/assignment-processes/{process.id}/requirements/{uuid.uuid4()}"
    )
    assert resp.status_code == 404


def test_get_requirement_wrong_process(client: TestClient, session: Session) -> None:
    process, _plan, _subject, activity = _setup(session)
    slot = factories.make_hour_requirement(session, process, activity)
    other = factories.make_assignment_process(session)
    resp = client.get(
        f"/reparto/assignment-processes/{other.id}/requirements/{slot.id}"
    )
    assert resp.status_code == 404


def test_manual_mutation_routes_removed(client: TestClient, session: Session) -> None:
    """Requirements are generated, not manually mutated (plan §5.9, §20.12)."""
    process, _plan, _subject, activity = _setup(session)
    slot = factories.make_hour_requirement(session, process, activity)
    base = f"/reparto/assignment-processes/{process.id}/requirements"
    assert client.post(base + "/", json={}).status_code == 405
    assert client.patch(f"{base}/{slot.id}", json={}).status_code == 405
    assert client.delete(f"{base}/{slot.id}").status_code == 405
