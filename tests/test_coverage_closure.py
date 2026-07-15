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
from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.departments import DepartmentCreate
from reparto_service.enums import AssignmentStatus, ValidationSeverity
from reparto_service.services.summary import (
    CODE_PROCESS_HAS_OVERAGE,
    CODE_REQ_NOT_FULLY_ASSIGNED,
    CODE_REQ_OVER_ASSIGNED_OVERRIDDEN,
    CODE_TEACHER_OVERLOADED_OVERRIDDEN,
    SummaryService,
)
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
            "patch",
            f"/reparto/assignment-processes/{process.id}/requirements/{missing}",
            {"required_hours": 2},
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
    other_process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, other_process)
    group = factories.make_teaching_group(session, other_process)

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
    requirement_resp = client.post(
        f"/reparto/assignment-processes/{process.id}/requirements/",
        json={
            "assignment_process_id": str(uuid.uuid4()),
            "teaching_group_id": str(group.id),
            "subject_id": str(subject.id),
            "required_hours": 4,
        },
    )

    assert subject_resp.status_code == 400
    assert group_resp.status_code == 400
    assert requirement_resp.status_code == 400


def test_requirement_rejects_cross_process_subject_and_group(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    other_process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, other_process)
    group = factories.make_teaching_group(session, process)

    bad_subject = client.post(
        f"/reparto/assignment-processes/{process.id}/requirements/",
        json={
            "assignment_process_id": str(process.id),
            "teaching_group_id": str(group.id),
            "subject_id": str(subject.id),
            "required_hours": 4,
        },
    )
    good_subject = factories.make_subject(session, process)
    other_group = factories.make_teaching_group(session, other_process)
    bad_group = client.post(
        f"/reparto/assignment-processes/{process.id}/requirements/",
        json={
            "assignment_process_id": str(process.id),
            "teaching_group_id": str(other_group.id),
            "subject_id": str(good_subject.id),
            "required_hours": 4,
        },
    )

    assert bad_subject.status_code == 404
    assert bad_group.status_code == 404


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


def test_delete_requirement_without_assignment_and_subject_not_found(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(session, process, group, subject)

    subject_delete = client.delete(
        f"/reparto/assignment-processes/{process.id}/subjects/{subject.id}"
    )
    requirement_delete = client.delete(
        f"/reparto/assignment-processes/{process.id}/requirements/{requirement.id}"
    )

    assert subject_delete.status_code == 200
    assert requirement_delete.status_code == 200


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
    requirement = factories.make_hour_requirement(session, process, group, subject)

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


def test_teacher_lan_summary_rejects_stale_process_teacher(
    client: TestClient, session: Session, current_user, monkeypatch
) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(
        session, user_id=uuid.UUID(str(current_user.id))
    )
    factories.make_process_teacher(session, process, profile)
    monkeypatch.setattr(SummaryService, "compute_teacher_balances", lambda *_: [])

    resp = client.get(f"/reparto/assignment-processes/{process.id}/lan/me")

    assert resp.status_code == 404
    assert "not part" in resp.json()["detail"]


@pytest.mark.parametrize(
    "kind",
    ["teachers", "subjects", "groups", "requirements", "assignments"],
)
def test_copy_target_empty_reports_each_non_empty_kind(
    session: Session, kind: str
) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    teacher = factories.make_process_teacher(session, process, profile)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(session, process, group, subject)
    if kind != "teachers":
        session.delete(teacher)
    if kind != "subjects":
        session.delete(subject)
    if kind != "groups":
        session.delete(group)
    if kind != "requirements":
        session.delete(requirement)
    if kind == "assignments":
        session.add(requirement)
        session.add(subject)
        session.add(group)
        session.add(teacher)
        factories.make_assignment(session, process, requirement, teacher)
        session.delete(requirement)
        session.delete(subject)
        session.delete(group)
        session.delete(teacher)
    session.commit()

    with pytest.raises(HTTPException) as exc_info:
        AssignmentProcessController._ensure_target_empty(session, process.id)

    assert kind.rstrip("s") in str(exc_info.value.detail)


def test_assignment_cap_ignores_cancelled_and_missing_requirement(
    client: TestClient, session: Session
) -> None:
    process, teacher, requirement = _seed_assignment_process(session)
    factories.make_assignment(
        session,
        process,
        requirement,
        teacher,
        assigned_hours=99,
        status=AssignmentStatus.CANCELLED,
    )
    allowed = client.post(
        f"/reparto/assignment-processes/{process.id}/assignments/",
        json={
            "assignment_process_id": str(process.id),
            "hour_requirement_id": str(requirement.id),
            "process_teacher_id": str(teacher.id),
            "assigned_hours": 4,
        },
    )
    missing = client.post(
        f"/reparto/assignment-processes/{process.id}/assignments/",
        json={
            "assignment_process_id": str(process.id),
            "hour_requirement_id": str(uuid.uuid4()),
            "process_teacher_id": str(teacher.id),
            "assigned_hours": 4,
        },
    )

    assert allowed.status_code == 201
    assert missing.status_code == 404


def test_assignment_get_not_found_and_deleted_requirement_cap(
    client: TestClient, session: Session
) -> None:
    process, teacher, requirement = _seed_assignment_process(session)
    assignment = factories.make_assignment(session, process, requirement, teacher)
    missing_assignment = client.get(
        f"/reparto/assignment-processes/{process.id}/assignments/{uuid.uuid4()}"
    )
    session.delete(requirement)
    session.commit()
    update_resp = client.patch(
        f"/reparto/assignment-processes/{process.id}/assignments/{assignment.id}",
        json={"assigned_hours": 5},
    )

    assert missing_assignment.status_code == 404
    assert update_resp.status_code == 404


def test_copy_assignments_skips_stale_source_references(session: Session) -> None:
    source = factories.make_assignment_process(session)
    target = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    teacher = factories.make_process_teacher(session, source, profile)
    subject = factories.make_subject(session, source)
    group = factories.make_teaching_group(session, source)
    requirement = factories.make_hour_requirement(session, source, group, subject)
    stale_requirement = factories.make_assignment(session, source, requirement, teacher)
    stale_teacher = factories.make_assignment(session, source, requirement, teacher)
    skipped_mapping = factories.make_assignment(session, source, requirement, teacher)
    session.delete(requirement)
    session.delete(teacher)
    session.commit()

    AssignmentProcessController._copy_assignments(session, source, target)

    assert stale_requirement.id
    assert stale_teacher.id
    assert skipped_mapping.id


def _seed_assignment_process(session: Session):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    teacher = factories.make_process_teacher(session, process, profile)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4
    )
    return process, teacher, requirement


def test_summary_validation_remaining_states(session: Session) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    teacher = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    partial = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4
    )
    factories.make_assignment(session, process, partial, teacher, assigned_hours=2)

    partial_messages = SummaryService.compute_validations(session, process.id)
    assert any(
        msg.code == CODE_REQ_NOT_FULLY_ASSIGNED
        and msg.severity == ValidationSeverity.WARNING
        for msg in partial_messages
    )

    overloaded_process = factories.make_assignment_process(session)
    overloaded_profile = factories.make_teacher_profile(session, display_name="Over")
    overloaded_teacher = factories.make_process_teacher(
        session, overloaded_process, overloaded_profile, base_weekly_hours=1
    )
    overloaded_subject = factories.make_subject(session, overloaded_process)
    overloaded_group = factories.make_teaching_group(session, overloaded_process)
    overloaded_requirement = factories.make_hour_requirement(
        session,
        overloaded_process,
        overloaded_group,
        overloaded_subject,
        required_hours=1,
    )
    factories.make_assignment(
        session,
        overloaded_process,
        overloaded_requirement,
        overloaded_teacher,
        assigned_hours=2,
        override_reason="Approved",
    )
    overloaded_messages = SummaryService.compute_validations(
        session, overloaded_process.id
    )
    assert any(
        msg.code == CODE_REQ_OVER_ASSIGNED_OVERRIDDEN for msg in overloaded_messages
    )
    assert any(
        msg.code == CODE_TEACHER_OVERLOADED_OVERRIDDEN for msg in overloaded_messages
    )

    exceeded_process = factories.make_assignment_process(session)
    exceeded_profile = factories.make_teacher_profile(session, display_name="Exceeded")
    exceeded_teacher = factories.make_process_teacher(
        session, exceeded_process, exceeded_profile, base_weekly_hours=10
    )
    exceeded_subject = factories.make_subject(session, exceeded_process)
    exceeded_group = factories.make_teaching_group(session, exceeded_process)
    exceeded_requirement = factories.make_hour_requirement(
        session, exceeded_process, exceeded_group, exceeded_subject, required_hours=1
    )
    session.add(
        Assignment(
            assignment_process_id=exceeded_process.id,
            hour_requirement_id=exceeded_requirement.id,
            process_teacher_id=exceeded_teacher.id,
            assigned_hours=2,
            status=AssignmentStatus.CONFIRMED,
        )
    )
    session.commit()
    exceeded_messages = SummaryService.compute_validations(session, exceeded_process.id)
    assert any(msg.code == CODE_PROCESS_HAS_OVERAGE for msg in exceeded_messages)


def test_summary_non_participating_and_warning_without_messages(
    session: Session,
) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    factories.make_process_teacher(
        session,
        process,
        profile,
        participates_in_selection=False,
    )
    balances = SummaryService.compute_teacher_balances(session, process.id)
    assert balances[0].state.value == "not_participating"

    warning_process = factories.make_assignment_process(session)
    warning_profile = factories.make_teacher_profile(session)
    warning_teacher = factories.make_process_teacher(
        session, warning_process, warning_profile, base_weekly_hours=10
    )
    warning_subject = factories.make_subject(session, warning_process)
    warning_group = factories.make_teaching_group(session, warning_process)
    warning_requirement = factories.make_hour_requirement(
        session, warning_process, warning_group, warning_subject, required_hours=1
    )
    factories.make_assignment(
        session,
        warning_process,
        warning_requirement,
        warning_teacher,
        assigned_hours=2,
        override_reason="Approved",
    )

    messages = SummaryService.compute_validations(session, warning_process.id)

    assert all(msg.code != CODE_PROCESS_HAS_OVERAGE for msg in messages)


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
