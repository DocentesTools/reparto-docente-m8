"""Per-tenant read scoping (plan §21.4).

The recorded decision is "`READER`/`WRITER` see only the departments they
belong to; `ADMIN`/`SUPERADMIN` see the deployment", with membership derived
from participation. These tests pin all three halves of that: what a
non-member cannot see, what a member can, and that membership is by department
rather than by process.
"""

from __future__ import annotations

import uuid

import pytest
from auth_sdk_m8.schemas.user import UserModel
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.services import read_scope
from tests import factories
from tests.conftest import make_user

_PROCESSES = "/reparto/assignment-processes"


def _two_departments(
    session: Session,
) -> tuple[AssignmentProcess, AssignmentProcess]:
    """Two processes in two departments of two different schools."""
    mine = factories.make_assignment_process(session)
    other_school = factories.make_school(session, name="Other IES")
    other_department = factories.make_department(
        session, other_school, name="Lengua", slug="lengua"
    )
    theirs = factories.make_assignment_process(
        session, school=other_school, department=other_department
    )
    return mine, theirs


# ── The scope itself ─────────────────────────────────────────────────────────


def test_a_department_head_is_unrestricted(
    session: Session, admin_user: UserModel
) -> None:
    assert read_scope.is_unrestricted(admin_user) is True
    assert read_scope.visible_department_ids(session, admin_user) is None
    assert read_scope.visible_school_ids(session, admin_user) is None


@pytest.mark.parametrize("role", ["writer", "reader"])
def test_a_non_member_belongs_nowhere(session: Session, role: str) -> None:
    user = make_user(role)
    factories.make_assignment_process(session)
    assert read_scope.visible_department_ids(session, user) == set()
    assert read_scope.visible_school_ids(session, user) == set()


def test_membership_follows_participation(session: Session, reader: UserModel) -> None:
    mine, theirs = _two_departments(session)
    factories.enrol(session, mine, reader)

    visible = read_scope.visible_department_ids(session, reader)
    assert visible == {mine.department_id}
    assert theirs.department_id not in visible
    assert read_scope.visible_school_ids(session, reader) == {mine.school_id}


def test_membership_is_by_department_not_by_process(
    session: Session, reader: UserModel
) -> None:
    """Last year's process of the same department stays readable.

    That is the point of the previous-year comparison: scoping by process would
    have silently broken it for every teacher.
    """
    this_year = factories.make_assignment_process(session)
    last_year = factories.make_assignment_process(
        session,
        academic_year=factories.make_academic_year(session, label="2025/2026"),
    )
    last_year.school_id = this_year.school_id
    last_year.department_id = this_year.department_id
    session.add(last_year)
    session.commit()
    factories.enrol(session, this_year, reader)

    read_scope.ensure_process_visible(session, reader, last_year.id)


def test_an_out_of_scope_process_is_indistinguishable_from_a_missing_one(
    session: Session, reader: UserModel
) -> None:
    mine, theirs = _two_departments(session)
    factories.enrol(session, mine, reader)

    with pytest.raises(HTTPException) as existing:
        read_scope.ensure_process_visible(session, reader, theirs.id)
    with pytest.raises(HTTPException) as absent:
        read_scope.ensure_process_visible(session, reader, uuid.uuid4())

    assert existing.value.status_code == absent.value.status_code == 404


# ── Applied to the process-nested surface ────────────────────────────────────


def test_a_non_member_cannot_read_anything_under_a_process(
    reader_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_meeting_session(session, process)

    for path in (
        f"{_PROCESSES}/{process.id}",
        f"{_PROCESSES}/{process.id}/summary",
        f"{_PROCESSES}/{process.id}/dashboard",
        f"{_PROCESSES}/{process.id}/audit-events/",
        f"{_PROCESSES}/{process.id}/meeting-sessions/",
        f"{_PROCESSES}/{process.id}/teachers/",
    ):
        assert reader_client.get(path).status_code == 404, path


def test_a_member_reads_their_own_process(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    mine, theirs = _two_departments(session)
    factories.enrol(session, mine, reader)

    assert reader_client.get(f"{_PROCESSES}/{mine.id}").status_code == 200
    assert reader_client.get(f"{_PROCESSES}/{theirs.id}").status_code == 404
    assert (
        reader_client.get(f"{_PROCESSES}/{theirs.id}/audit-events/").status_code == 404
    )


def test_the_event_stream_is_scoped_too(
    reader_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    assert reader_client.get(f"{_PROCESSES}/{process.id}/events").status_code == 404


# ── Applied to the top-level lists ───────────────────────────────────────────


def test_process_school_and_department_lists_are_scoped(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    mine, theirs = _two_departments(session)
    factories.enrol(session, mine, reader)

    processes = reader_client.get(f"{_PROCESSES}/").json()
    assert processes["count"] == 1
    assert processes["data"][0]["id"] == str(mine.id)

    schools = reader_client.get("/reparto/schools/").json()
    assert [s["id"] for s in schools["data"]] == [str(mine.school_id)]
    assert schools["count"] == 1

    departments = reader_client.get("/reparto/departments/").json()
    assert [d["id"] for d in departments["data"]] == [str(mine.department_id)]
    assert departments["count"] == 1


def test_a_non_member_sees_empty_lists(
    reader_client: TestClient, session: Session
) -> None:
    _two_departments(session)
    for path in (f"{_PROCESSES}/", "/reparto/schools/", "/reparto/departments/"):
        assert reader_client.get(path).json()["count"] == 0, path


def test_out_of_scope_schools_and_departments_answer_404(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    mine, theirs = _two_departments(session)
    factories.enrol(session, mine, reader)

    assert reader_client.get(f"/reparto/schools/{mine.school_id}").status_code == 200
    assert reader_client.get(f"/reparto/schools/{theirs.school_id}").status_code == 404
    assert (
        reader_client.get(f"/reparto/departments/{mine.department_id}").status_code
        == 200
    )
    assert (
        reader_client.get(f"/reparto/departments/{theirs.department_id}").status_code
        == 404
    )


def test_a_department_head_still_sees_everything(
    client: TestClient, session: Session
) -> None:
    mine, theirs = _two_departments(session)

    assert client.get(f"{_PROCESSES}/").json()["count"] == 2
    assert client.get(f"{_PROCESSES}/{theirs.id}").status_code == 200
    assert client.get(f"/reparto/schools/{theirs.school_id}").status_code == 200
    assert client.get(f"/reparto/departments/{mine.department_id}").status_code == 200


# ── Teacher profiles ─────────────────────────────────────────────────────────


def test_teacher_profiles_are_scoped_to_colleagues_and_self(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    mine, theirs = _two_departments(session)
    own = factories.enrol(session, mine, reader)
    colleague = factories.make_process_teacher(
        session, mine, factories.make_teacher_profile(session, display_name="Colleague")
    )
    stranger = factories.make_process_teacher(
        session,
        theirs,
        factories.make_teacher_profile(session, display_name="Stranger"),
    )

    listed = reader_client.get("/reparto/teacher-profiles/").json()
    assert listed["count"] == 2
    assert {p["display_name"] for p in listed["data"]} == {
        "Scoped Teacher",
        "Colleague",
    }

    assert (
        reader_client.get(
            f"/reparto/teacher-profiles/{own.teacher_profile_id}"
        ).status_code
        == 200
    )
    assert (
        reader_client.get(
            f"/reparto/teacher-profiles/{colleague.teacher_profile_id}"
        ).status_code
        == 200
    )
    assert (
        reader_client.get(
            f"/reparto/teacher-profiles/{stranger.teacher_profile_id}"
        ).status_code
        == 404
    )


def test_a_profile_is_readable_by_its_owner_before_any_participation(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    """The record a teacher may edit must be a record they may read."""
    profile = factories.make_teacher_profile(
        session, display_name="Fresh", user_id=uuid.UUID(str(reader.id))
    )
    factories.make_teacher_profile(session, display_name="Somebody else")

    listed = reader_client.get("/reparto/teacher-profiles/").json()
    assert [p["display_name"] for p in listed["data"]] == ["Fresh"]
    assert (
        reader_client.get(f"/reparto/teacher-profiles/{profile.id}").status_code == 200
    )


# ── Deliberately unscoped reference data ─────────────────────────────────────


def test_the_calendar_and_grade_vocabulary_stay_readable(
    reader_client: TestClient, session: Session
) -> None:
    """Academic years and classroom stages are deployment-wide reference data.

    A scoped view cannot render without them, and neither names anything about
    a particular school's operations.
    """
    factories.make_academic_year(session)
    factories.make_classroom_stage(session)

    assert reader_client.get("/reparto/academic-years/").json()["count"] == 1
    assert reader_client.get("/reparto/classroom-stages/").json()["count"] == 1
