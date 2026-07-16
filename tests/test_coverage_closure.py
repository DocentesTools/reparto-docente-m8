"""Focused coverage tests for defensive reparto_service branches."""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.controllers.assignment_processes import (
    AssignmentProcessController,
)
from reparto_service.core import events
from reparto_service.db_models.departments import DepartmentCreate
from tests import factories


def test_update_year_rejects_inverted_dates(
    client: TestClient, session: Session
) -> None:
    year = factories.make_academic_year(session)
    resp = client.patch(
        f"/reparto/academic-years/{year.id}",
        json={"end_date": "2026-01-01"},
    )
    assert resp.status_code == 400
    assert "end_date" in resp.json()["detail"]


def test_update_year_rejects_inverted_start_date(
    client: TestClient, session: Session
) -> None:
    year = factories.make_academic_year(session)
    resp = client.patch(
        f"/reparto/academic-years/{year.id}",
        json={"start_date": "2028-01-01"},
    )
    assert resp.status_code == 400


def test_get_update_delete_child_resources_not_found(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    missing = uuid.uuid4()

    checks = [
        ("get", f"/reparto/assignment-processes/{process.id}/subjects/{missing}", None),
        (
            "patch",
            f"/reparto/assignment-processes/{process.id}/subjects/{missing}",
            {"name": "X"},
        ),
        (
            "get",
            f"/reparto/assignment-processes/{process.id}/groups/{missing}",
            None,
        ),
        (
            "patch",
            f"/reparto/assignment-processes/{process.id}/groups/{missing}",
            {"label": "X"},
        ),
        (
            "delete",
            f"/reparto/assignment-processes/{process.id}/groups/{missing}",
            None,
        ),
        (
            "get",
            f"/reparto/assignment-processes/{process.id}/requirements/{missing}",
            None,
        ),
        (
            "get",
            f"/reparto/assignment-processes/{process.id}/teachers/{missing}",
            None,
        ),
        (
            "patch",
            f"/reparto/assignment-processes/{process.id}/teachers/{missing}",
            {"base_weekly_hours": 2},
        ),
    ]

    for method, url, payload in checks:
        resp = (
            getattr(client, method)(url, json=payload)
            if payload
            else getattr(client, method)(url)
        )
        assert resp.status_code == 404


def test_create_child_resources_reject_wrong_process_payload(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)

    subject_resp = client.post(
        f"/reparto/assignment-processes/{process.id}/subjects/",
        json={"assignment_process_id": str(uuid.uuid4()), "name": "Math"},
    )
    stage = factories.make_classroom_stage(session)
    group_resp = client.post(
        f"/reparto/assignment-processes/{process.id}/groups/",
        json={
            "assignment_process_id": str(uuid.uuid4()),
            "classroom_stage_id": str(stage.id),
            "grade": 1,
            "group_code": "A",
            "label": "1 ESO A",
        },
    )

    assert subject_resp.status_code == 400
    assert group_resp.status_code == 400


def test_duplicate_updates_are_rejected(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    school = factories.make_school(session, name="Duplicate School")
    first_department = factories.make_department(session, school, name="Math")
    second_department = factories.make_department(session, school, name="Physics")
    first_subject = factories.make_subject(session, process, name="Math")
    second_subject = factories.make_subject(session, process, name="Physics")
    first_group = factories.make_teaching_group(session, process, label="1 ESO A")
    second_group = factories.make_teaching_group(
        session, process, group_code="B", label="1 ESO B"
    )

    department_resp = client.patch(
        f"/reparto/departments/{second_department.id}",
        json={"slug": first_department.slug},
    )
    subject_resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/subjects/{second_subject.id}",
        json={"name": first_subject.name},
    )
    group_resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/groups/{second_group.id}",
        json={"label": first_group.label},
    )

    assert department_resp.status_code == 400
    assert subject_resp.status_code == 400
    assert group_resp.status_code == 409


def test_duplicate_create_group_is_rejected_and_delete_group_succeeds(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    group = factories.make_teaching_group(session, process, label="1 ESO A")

    duplicate = client.post(
        f"/reparto/assignment-processes/{process.id}/groups/",
        json={
            "assignment_process_id": str(process.id),
            "classroom_stage_id": str(group.classroom_stage_id),
            "grade": 1,
            "group_code": "B",
            "label": group.label,
        },
    )
    deleted = client.delete(
        f"/reparto/assignment-processes/{process.id}/groups/{group.id}"
    )

    assert duplicate.status_code == 409
    assert deleted.status_code == 200


def test_delete_subject_succeeds(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)

    subject_delete = client.delete(
        f"/reparto/assignment-processes/{process.id}/subjects/{subject.id}"
    )

    assert subject_delete.status_code == 200


def test_read_routes_for_departments_schools_and_children(
    client: TestClient, session: Session
) -> None:
    school = factories.make_school(session)
    department = factories.make_department(session, school)
    process = factories.make_assignment_process(
        session, school=school, department=department
    )
    profile = factories.make_teacher_profile(session)
    teacher = factories.make_process_teacher(session, process, profile)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    plan = factories.make_teaching_plan(session, process)
    activity = factories.make_teaching_activity(session, plan, subject)
    requirement = factories.make_hour_requirement(session, process, activity)

    responses = [
        client.get(f"/reparto/departments/{department.id}"),
        client.get(f"/reparto/schools/{school.id}"),
        client.get(f"/reparto/assignment-processes/{process.id}/teachers/{teacher.id}"),
        client.get(f"/reparto/assignment-processes/{process.id}/subjects/{subject.id}"),
        client.get(f"/reparto/assignment-processes/{process.id}/groups/{group.id}"),
        client.get(
            f"/reparto/assignment-processes/{process.id}/requirements/{requirement.id}"
        ),
    ]

    assert [resp.status_code for resp in responses] == [200] * len(responses)


def test_teacher_profile_get_route_and_explicit_department_slug(
    client: TestClient, session: Session
) -> None:
    profile = factories.make_teacher_profile(session)
    department = DepartmentCreate(
        school_id=uuid.uuid4(),
        name="Mathematics",
        slug="math",
    )
    resp = client.get(f"/reparto/teacher-profiles/{profile.id}")

    assert resp.status_code == 200
    assert resp.json()["id"] == str(profile.id)
    assert department.slug == "math"


def test_create_department_duplicate_and_process_teacher_duplicate(
    client: TestClient, session: Session
) -> None:
    school = factories.make_school(session)
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    factories.make_department(session, school, name="Math")
    factories.make_process_teacher(session, process, profile)

    department_resp = client.post(
        "/reparto/departments/",
        json={"school_id": str(school.id), "name": "Math"},
    )
    teacher_resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teachers/",
        json={
            "assignment_process_id": str(process.id),
            "teacher_profile_id": str(profile.id),
            "base_weekly_hours": 10,
        },
    )

    assert department_resp.status_code == 400
    assert teacher_resp.status_code == 400


def test_school_update_and_process_list_filter(
    client: TestClient, session: Session
) -> None:
    school = factories.make_school(session)
    year = factories.make_academic_year(session)
    process = factories.make_assignment_process(session, academic_year=year)

    school_resp = client.patch(f"/reparto/schools/{school.id}", json={"name": "New"})
    process_resp = client.get(
        f"/reparto/assignment-processes/?academic_year_id={year.id}"
    )

    assert school_resp.status_code == 200
    assert school_resp.json()["name"] == "New"
    assert process_resp.status_code == 200
    assert process_resp.json()["data"][0]["id"] == str(process.id)


@pytest.mark.parametrize(
    ("kind", "expected_detail"),
    [
        ("teachers", "already has teachers"),
        ("subjects", "already has subjects"),
        ("groups", "already has teaching groups"),
        ("cells", "already has group-subject cells"),
        ("plan", "already has a teaching plan"),
    ],
)
def test_copy_target_empty_reports_each_non_empty_kind(
    session: Session, kind: str, expected_detail: str
) -> None:
    """Each blocking kind is reported by name, so a head knows what to clear."""
    process = factories.make_assignment_process(session)
    if kind == "teachers":
        profile = factories.make_teacher_profile(session)
        factories.make_process_teacher(session, process, profile)
    elif kind == "subjects":
        factories.make_subject(session, process)
    elif kind == "groups":
        factories.make_teaching_group(session, process)
    elif kind == "cells":
        # A cell needs its group and subject, which are checked first, so the
        # cell is seeded through a second process and re-pointed at this one.
        other = factories.make_assignment_process(session)
        subject = factories.make_subject(session, other)
        group = factories.make_teaching_group(session, other)
        cell = factories.make_group_subject(session, other, group, subject)
        cell.assignment_process_id = process.id
        session.add(cell)
        session.commit()
    else:
        factories.make_teaching_plan(session, process)

    with pytest.raises(HTTPException) as exc_info:
        AssignmentProcessController._ensure_target_empty(session, process.id)

    assert expected_detail in str(exc_info.value.detail)


# The complete-slot assignment surface — create/get/update/cancel, the unknown
# requirement and cross-process guards, the cancelled-assignment-frees-the-slot
# path and both DB invariants — is covered end to end by
# ``test_routes_assignments.py``. Copy-from-previous-year no longer copies
# assignments at all (``_copy_assignments`` is gone, plan §10.1); what it does
# copy is covered by ``test_routes_process_lifecycle.py``.


@pytest.mark.anyio
async def test_auth_event_stream_handlers(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="reparto_service.core.events")
    auth = MagicMock()
    await events.handle_auth_event(
        SimpleNamespace(payload={"event_type": "session.revoked", "jti": "j1"}),
        auth=auth,
    )
    await events.handle_auth_event(
        SimpleNamespace(payload={"event_type": "session.revoked", "user_id": "u1"}),
        auth=auth,
    )
    await events.handle_auth_event(
        SimpleNamespace(payload={"event_type": "user.deleted", "user_id": "u2"}),
        auth=auth,
    )
    await events.handle_auth_event(
        SimpleNamespace(payload={"event_type": "unknown"}),
        auth=auth,
    )

    auth.evict_jti.assert_called_once_with("j1")
    auth.evict_user.assert_any_call("u1")
    auth.evict_user.assert_any_call("u2")
    assert "unknown_event_type" in caplog.text


@pytest.mark.anyio
async def test_auth_event_stream_logs_handler_and_gap_failures(caplog) -> None:
    auth = MagicMock()
    auth.evict_jti.side_effect = RuntimeError("boom")
    auth.flush_cache.side_effect = RuntimeError("gap")

    await events.handle_auth_event(
        SimpleNamespace(payload={"event_type": "session.revoked", "jti": "j1"}),
        auth=auth,
    )
    await events.handle_auth_gap(auth=auth)

    assert "handler failed" in caplog.text
    assert "gap handler failed" in caplog.text


@pytest.mark.anyio
async def test_stream_lifespan_starts_and_stops_client(monkeypatch) -> None:
    client = MagicMock()
    client.stop = AsyncMock()
    build_client = MagicMock(return_value=client)
    monkeypatch.setattr(events, "build_event_stream_client", build_client)
    settings = SimpleNamespace(INTROSPECTION_URL="http://auth.local/introspect")
    auth = MagicMock()

    extras = events.make_lifespan_extras(settings, auth)
    assert extras is not None
    async with extras(MagicMock()):
        await build_client.call_args.kwargs["on_event"](
            SimpleNamespace(payload={"event_type": "user.deleted", "user_id": "u1"})
        )
        await build_client.call_args.kwargs["on_gap"]()

    client.start.assert_called_once_with()
    client.stop.assert_awaited_once_with()


def test_lifespan_extras_disabled_without_introspection() -> None:
    settings = SimpleNamespace(INTROSPECTION_URL=None)
    assert events.make_lifespan_extras(settings, MagicMock()) is None


@pytest.mark.anyio
async def test_check_db_success_and_failure(monkeypatch) -> None:
    import reparto_service.main as service_main

    class SessionContext:
        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def exec(self, statement) -> None:
            del statement

    class WorkingEngine:
        def session(self):
            return SessionContext()

    monkeypatch.setattr(service_main, "engine", WorkingEngine())
    success = await service_main.check_db()
    assert success.name == "database"
    assert success.status.value == "ok"

    class BrokenEngine:
        def session(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(service_main, "engine", BrokenEngine())
    failure = await service_main.check_db()
    assert failure.name == "database"
    assert failure.status.value == "fail"
    assert "db down" in str(failure.error)
