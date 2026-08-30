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
from tests.conftest import identity_client as _identity_client
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


# ── Scope is not a tier (remediation `W5.3`) ─────────────────────────────────


def test_a_participant_is_refused_the_department_head_tier(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    """Clearing the scope gate does not hand a participant the head's payload.

    The two rules that govern "what may this teacher read" used to disagree:
    read scope let a participant through to every process of their department,
    and §20.25's tier projection redacted the same figures out of the stream
    and the shared screen. The dashboard and the participant list carry that
    department-head tier — per-participant hours, the findings that name them,
    and the extra-hours reason — so they now sit at the administrator floor and
    the two rules answer the same way.
    """
    process, _theirs = _two_departments(session)
    factories.enrol(session, process, reader)

    for path in (
        f"{_PROCESSES}/{process.id}/dashboard",
        f"{_PROCESSES}/{process.id}/teachers/",
    ):
        assert reader_client.get(path).status_code == 403, path


def test_a_participant_is_refused_a_colleagues_participation_row(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    """The detail route is the list route one row at a time, and gated alike."""
    process, _theirs = _two_departments(session)
    factories.enrol(session, process, reader)
    colleague = factories.make_process_teacher(
        session, process, factories.make_teacher_profile(session, display_name="Other")
    )

    response = reader_client.get(f"{_PROCESSES}/{process.id}/teachers/{colleague.id}")
    assert response.status_code == 403


def test_the_teacher_and_shared_screen_tiers_are_untouched(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    """Narrowing cost no screen: both lower tiers already had their own endpoint.

    ``/lan/me`` is the teacher's own row and the identifier-free aggregates;
    ``/summary`` is the nameless aggregate the projected screen reads
    (`RBAC-07`). Neither carries another participant's figures, so neither
    moved.
    """
    process, _theirs = _two_departments(session)
    factories.enrol(session, process, reader)

    lan = reader_client.get(f"{_PROCESSES}/{process.id}/lan/me")
    summary = reader_client.get(f"{_PROCESSES}/{process.id}/summary")

    assert lan.status_code == 200
    assert summary.status_code == 200
    assert "participants" not in summary.json()


def test_the_department_head_floor_never_reveals_an_out_of_scope_process(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    """Scope is still resolved first, so the new 403 tells a stranger nothing.

    A participant of one department asking for another department's dashboard —
    or for a process id that does not exist at all — is answered 404, exactly as
    before: the role floor is reached only once the caller is already known to
    belong (§21.4's "an out-of-scope row is a 404, not a 403").
    """
    mine, theirs = _two_departments(session)
    factories.enrol(session, mine, reader)

    assert reader_client.get(f"{_PROCESSES}/{mine.id}/dashboard").status_code == 403
    assert reader_client.get(f"{_PROCESSES}/{theirs.id}/dashboard").status_code == 404
    assert (
        reader_client.get(f"{_PROCESSES}/{uuid.uuid4()}/dashboard").status_code == 404
    )


# ── The after-the-fact reads of the same tier (remediation `W7.1`) ───────────


#: The seven reads `W7.1` moved to the administrator floor, as templates over a
#: process id. `W5.3` narrowed the two *live* department-head reads and said in
#: the same breath that this did not leave the reader surface teacher-tier-clean;
#: these are the rest, and they were taken as one decision rather than seven.
_AFTER_THE_FACT_READS: tuple[str, ...] = (
    "{process}/assignments/validations",
    "{process}/teaching-plan/validations",
    "{process}/audit-events/",
    "{process}/versions",
    "{process}/versions/{left}/compare/{right}",
    "{process}/compare-previous-year",
    "{process}/exports",
)


def _after_the_fact_paths(process_id: uuid.UUID) -> list[str]:
    """Render the seven templates against one process."""
    return [
        template.format(
            process=f"{_PROCESSES}/{process_id}",
            left=uuid.uuid4(),
            right=uuid.uuid4(),
        )
        for template in _AFTER_THE_FACT_READS
    ]


def test_a_participant_is_refused_the_tier_after_the_fact(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    """Reviewing the record later is the same tier as watching it live.

    Each of these carries what §20.25 calls the department-head tier. The two
    validation reports name the participant a finding is about and quote their
    hours (`W5.1`). The audit trail stores the extra-hours event with
    ``reason`` — the key
    :data:`reparto_service.services.sse.DEPARTMENT_HEAD_ONLY_PAYLOAD_KEYS`
    withholds from a teacher on the live stream *even about themselves* —
    beside that participant's base, extra and target weekly hours. A version
    snapshot is a whole-process dump carrying the same field, and both
    comparison routes read two of them; the export list inventories the
    artefacts built from all of it.
    """
    process, _theirs = _two_departments(session)
    factories.enrol(session, process, reader)

    for path in _after_the_fact_paths(process.id):
        assert reader_client.get(path).status_code == 403, path


@pytest.mark.parametrize("role", ["reader", "writer"])
def test_the_after_the_fact_floor_is_the_tier_not_the_verb(
    session: Session, role: str
) -> None:
    """A ``WRITER`` is refused too: the floor is confidentiality, not mutation.

    ``WRITER`` in this service means "may mutate my own records" (§21.3), which
    says nothing about reading a colleague's figures. Pinning both roles keeps
    the rule from decaying into "reads are for readers, writes are for writers".
    """
    user = make_user(role)
    process, _theirs = _two_departments(session)
    factories.enrol(session, process, user)

    client = _identity_client(session, user)
    for path in _after_the_fact_paths(process.id):
        assert client.get(path).status_code == 403, path


def test_the_after_the_fact_floor_never_reveals_an_out_of_scope_process(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    """Scope still answers before the role, so the new 403 tells a stranger nothing.

    Every one of the seven hangs off a router that already declares
    ``require_visible_process``, and FastAPI resolves a route-level dependency
    before the handler's own. A process in another department — or one that
    does not exist — is 404 as it was, and §21.4's ordering holds.
    """
    mine, theirs = _two_departments(session)
    factories.enrol(session, mine, reader)

    for path in _after_the_fact_paths(theirs.id):
        assert reader_client.get(path).status_code == 404, path
    for path in _after_the_fact_paths(uuid.uuid4()):
        assert reader_client.get(path).status_code == 404, path


def test_the_nameless_readiness_reads_stay_at_the_reader_floor(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    """Narrowing the reports cost no screen, because the counts have their own read.

    ``…/teaching-plan/summary`` answers "is this plan balanced" without naming
    anyone, which is the question a participant or a projected screen actually
    asks of a validation report. That it stays readable is what makes the
    narrowing above a tier decision rather than a loss of function.
    """
    process, _theirs = _two_departments(session)
    factories.make_teaching_plan(session, process)
    factories.enrol(session, process, reader)

    summary = reader_client.get(f"{_PROCESSES}/{process.id}/teaching-plan/summary")
    assert summary.status_code == 200
    assert "messages" not in summary.json()
