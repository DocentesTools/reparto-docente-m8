"""Authorization-boundary sweeps over the whole route surface (plan §21).

These tests are deliberately written against the generated OpenAPI document
rather than a hand-kept list of paths: a route added tomorrow is swept the day
it is added, which is the only way a "no route may rely on bare authentication"
rule stays true after the commit that introduced it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from auth_sdk_m8.schemas.user import UserModel
from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.core import deps
from reparto_service.db_models.departments import Department
from reparto_service.enums import MeetingSessionStatus
from reparto_service.main import app
from tests import factories
from tests.conftest import identity_client as _identity_client
from tests.conftest import make_user

#: Framework-owned, deliberately public endpoints. Everything else under the
#: API prefix is domain surface and must sit behind the §21.1 reader floor.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/reparto/health/",
        "/reparto/meta",
        "/reparto/ping",
        "/reparto/openapi.json",
        "/reparto/docs",
        "/reparto/redoc",
        "/docs/oauth2-redirect",
    }
)


def domain_operations() -> Iterator[tuple[str, str]]:
    """Yield ``(method, path)`` for every non-public operation in the schema."""
    for path, operations in sorted(app.openapi()["paths"].items()):
        if path in PUBLIC_PATHS:
            continue
        for method in sorted(operations):
            yield method.upper(), path


DOMAIN_OPERATIONS: list[tuple[str, str]] = list(domain_operations())


def concrete(path: str) -> str:
    """Replace every ``{param}`` placeholder with a syntactically valid id."""
    while "{" in path:
        head, _, rest = path.partition("{")
        _, _, tail = rest.partition("}")
        path = f"{head}{uuid.uuid4()}{tail}"
    return path


def test_the_sweep_covers_the_whole_domain_surface() -> None:
    """Guard the guard: an empty sweep would pass every assertion below."""
    assert len(DOMAIN_OPERATIONS) > 100
    assert ("GET", "/reparto/assignment-processes/{process_id}/audit-events/") in (
        DOMAIN_OPERATIONS
    )
    assert (
        "POST",
        "/reparto/assignment-processes/{process_id}/exports/planning-draft",
    ) in DOMAIN_OPERATIONS


@pytest.mark.parametrize(("method", "path"), DOMAIN_OPERATIONS)
def test_every_domain_operation_declares_a_security_scheme(
    method: str, path: str
) -> None:
    """No operation may be reachable without presenting a token (`RBAC-01`).

    A missing ``security`` block is exactly the signature of the pre-§21 reads:
    a handler whose signature never mentioned the current user at all.
    """
    operation = app.openapi()["paths"][path][method.lower()]
    assert operation.get("security"), f"{method} {path} has no security requirement"


@pytest.mark.parametrize(("method", "path"), DOMAIN_OPERATIONS)
def test_unauthenticated_callers_are_rejected(
    unauth_client: TestClient, method: str, path: str
) -> None:
    """401 before anything else — including before body/path validation."""
    response = unauth_client.request(method, concrete(path))
    assert response.status_code == 401, (
        f"{method} {path} answered {response.status_code}"
    )


@pytest.mark.parametrize(("method", "path"), DOMAIN_OPERATIONS)
def test_user_role_has_no_capability_anywhere(
    user_client: TestClient, method: str, path: str
) -> None:
    """``USER`` is a platform identity with zero capability here (§21.1).

    Asserted for reads as well as mutations: the read floor is the whole point
    of the rule, and a 200 here would mean the floor is missing on that route.
    """
    response = user_client.request(method, concrete(path))
    assert response.status_code == 403, (
        f"{method} {path} answered {response.status_code}"
    )


# ── Own-data mutations: WRITER may act only on its own records (§21.3) ────────


def _linked_participant(
    session: Session, user: UserModel
) -> tuple[AssignmentProcess, TeacherProfile, ProcessTeacher]:
    """Build a process in which *user* is a participating teacher."""
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(
        session, display_name="Own", user_id=uuid.UUID(str(user.id))
    )
    return process, profile, factories.make_process_teacher(session, process, profile)


def test_a_writer_may_act_on_their_own_turn(
    writer_client: TestClient, session: Session, writer_user: UserModel
) -> None:
    process, _profile, participant = _linked_participant(session, writer_user)
    meeting = factories.make_meeting_session(
        session, process, status=MeetingSessionStatus.OPEN
    )
    turn = factories.make_selection_turn(session, meeting, participant)

    response = writer_client.post(
        f"/reparto/assignment-processes/{process.id}"
        f"/meeting-sessions/{meeting.id}/turns/{turn.id}/start"
    )
    assert response.status_code == 200


def test_a_writer_may_not_act_on_another_participants_turn(
    writer_client: TestClient, session: Session, writer_user: UserModel
) -> None:
    process, _profile, _own = _linked_participant(session, writer_user)
    other_profile = factories.make_teacher_profile(
        session, display_name="Other", user_id=uuid.uuid4()
    )
    other = factories.make_process_teacher(session, process, other_profile)
    meeting = factories.make_meeting_session(
        session, process, status=MeetingSessionStatus.OPEN
    )
    turn = factories.make_selection_turn(session, meeting, other, position=1)

    response = writer_client.post(
        f"/reparto/assignment-processes/{process.id}"
        f"/meeting-sessions/{meeting.id}/turns/{turn.id}/skip",
        json={"reason": "Not mine to skip"},
    )
    assert response.status_code == 403
    assert "your own participation" in response.json()["detail"]


def test_a_writer_with_no_linked_profile_owns_nothing(
    writer_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session, user_id=uuid.uuid4())
    participant = factories.make_process_teacher(session, process, profile)
    meeting = factories.make_meeting_session(
        session, process, status=MeetingSessionStatus.OPEN
    )
    turn = factories.make_selection_turn(session, meeting, participant)

    response = writer_client.post(
        f"/reparto/assignment-processes/{process.id}"
        f"/meeting-sessions/{meeting.id}/turns/{turn.id}/start"
    )
    assert response.status_code == 404


def test_a_writer_may_edit_their_own_profile(
    writer_client: TestClient, session: Session, writer_user: UserModel
) -> None:
    profile = factories.make_teacher_profile(
        session, display_name="Before", user_id=uuid.UUID(str(writer_user.id))
    )
    response = writer_client.patch(
        f"/reparto/teacher-profiles/{profile.id}",
        json={"display_name": "After", "notes": "Room 12"},
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "After"


def test_a_writer_may_not_edit_another_teachers_profile(
    writer_client: TestClient, session: Session
) -> None:
    profile = factories.make_teacher_profile(session, user_id=uuid.uuid4())
    response = writer_client.patch(
        f"/reparto/teacher-profiles/{profile.id}",
        json={"display_name": "Hijacked"},
    )
    assert response.status_code == 403
    assert "your own teacher profile" in response.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [{"user_id": str(uuid.uuid4())}, {"active": False}],
    ids=["relink", "deactivate"],
)
def test_a_writer_may_not_change_the_linkage_on_their_own_profile(
    writer_client: TestClient,
    session: Session,
    writer_user: UserModel,
    payload: dict[str, object],
) -> None:
    """Owning the record is not owning every field on it (§21.3).

    Re-pointing ``user_id`` would let a teacher hand their own participation to
    another account — or take somebody else's — so the linkage and the active
    flag stay department-head fields even on one's own profile.
    """
    profile = factories.make_teacher_profile(
        session, user_id=uuid.UUID(str(writer_user.id))
    )
    response = writer_client.patch(
        f"/reparto/teacher-profiles/{profile.id}", json=payload
    )
    assert response.status_code == 403
    assert "Only a department head" in response.json()["detail"]


def test_a_department_head_may_edit_any_profile_field(
    client: TestClient, session: Session
) -> None:
    profile = factories.make_teacher_profile(session, user_id=uuid.uuid4())
    response = client.patch(
        f"/reparto/teacher-profiles/{profile.id}", json={"active": False}
    )
    assert response.status_code == 200
    assert response.json()["active"] is False


# ── The revocation-cache guarantee (`RBAC-03`, plan §21.6) ───────────────────


def test_the_role_gates_are_the_sdk_dependencies_not_local_comparisons() -> None:
    """Identity, not equivalence: a local check that *behaves* the same is not
    the same, because only the SDK path re-validates on the fresh user."""
    assert deps.require_reader is deps.auth.get_current_active_reader
    assert deps.require_writer is deps.auth.get_current_active_writer
    assert deps.require_admin is deps.auth.get_current_active_admin


@pytest.mark.parametrize(("method", "path"), DOMAIN_OPERATIONS)
def test_no_domain_route_resolves_its_principal_from_the_cached_path(
    cached_path_only_client: TestClient, method: str, path: str
) -> None:
    """The behavioural half of `RBAC-03`.

    ``get_current_user`` may answer from the positive revocation cache, so a
    role check hanging off it can keep honouring a role that was revoked up to
    a TTL ago. Here *only* that dependency is satisfied and the SDK's fresh
    path is left to the real bearer-token flow: every domain route must still
    answer 401, which is only true if none of them takes its principal from the
    cached dependency.
    """
    response = cached_path_only_client.request(method, concrete(path))
    assert response.status_code == 401, (
        f"{method} {path} authenticated through the cached user path"
    )


# ── The full §21.1 role matrix over every operation ──────────────────────────

#: Own-data mutations: a `WRITER` may perform these, on their own records only
#: (plan §21.3). Everything else that mutates is department-head or platform
#: administration, and everything that reads sits at the `READER` floor.
OWN_DATA_OPERATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "POST",
            "/reparto/assignment-processes/{process_id}/assignments/direct-choice",
        ),
        ("PATCH", "/reparto/teacher-profiles/{profile_id}"),
        *(
            (
                "POST",
                (
                    "/reparto/assignment-processes/{process_id}/meeting-sessions/"
                    f"{{meeting_session_id}}/turns/{{turn_id}}/{action}"
                ),
            )
            for action in ("start", "complete", "skip")
        ),
    }
)

#: Exports are reads that happen to be POSTs (plan §7.8): a draft or provisional
#: artifact is a view of the plan, so it sits at the read floor like every other
#: view. `§20.25`'s "never blocked by feasibility" governs *feasibility* gating
#: and says nothing about authentication — the two are orthogonal.
READ_ONLY_POSTS: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "POST",
            f"/reparto/assignment-processes/{{process_id}}/exports/planning-{mode}",
        )
        for mode in ("draft", "provisional", "final")
    }
)

ROLE_RANK: dict[str, int] = {
    "user": 0,
    "reader": 1,
    "writer": 2,
    "admin": 3,
    "superadmin": 4,
}


#: Reads that sit *above* the floor. The feasibility witness is a provisional
#: slot-to-teacher mapping (§20.20) — it says who *would* get what before anyone
#: has chosen, so it is department-head-only however harmless a `GET` looks.
ADMIN_ONLY_READS: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "GET",
            (
                "/reparto/assignment-processes/{process_id}"
                "/teaching-plan/feasibility/witness"
            ),
        )
    }
)


def required_role(method: str, path: str) -> str:
    """Return the minimum role the §21.1/§21.3 tables give this operation."""
    if (method, path) in ADMIN_ONLY_READS:
        return "admin"
    if method == "GET" or (method, path) in READ_ONLY_POSTS:
        return "reader"
    if (method, path) in OWN_DATA_OPERATIONS:
        return "writer"
    return "admin"


def test_every_operation_is_classified_by_the_role_tables() -> None:
    """The own-data and read-only sets must name operations that still exist.

    A renamed route would otherwise silently fall through to the ``admin``
    default and the matrix below would keep passing while testing less.
    """
    known = set(DOMAIN_OPERATIONS)
    assert OWN_DATA_OPERATIONS <= known, OWN_DATA_OPERATIONS - known
    assert READ_ONLY_POSTS <= known, READ_ONLY_POSTS - known
    assert ADMIN_ONLY_READS <= known, ADMIN_ONLY_READS - known


@pytest.fixture
def matrix_process(session: Session) -> AssignmentProcess:
    """A process every non-admin identity in this module can see.

    Read scope (§21.4) would otherwise answer 404 before the role gate is
    reached, and a 404 proves nothing about roles.
    """
    return factories.make_assignment_process(session)


#: The SSE endpoint is the one operation this sweep cannot drive: an authorized
#: caller gets an open stream that never completes, so the request would hang
#: rather than answer. Its authorization is covered by the reader floor and the
#: USER sweep above, by ``test_the_event_stream_is_scoped_too`` for read scope,
#: and by the audience tests for what a given role actually receives.
STREAMING_OPERATIONS: frozenset[tuple[str, str]] = frozenset(
    {("GET", "/reparto/assignment-processes/{process_id}/events")}
)

MATRIX_OPERATIONS: list[tuple[str, str]] = [
    operation
    for operation in DOMAIN_OPERATIONS
    if operation not in STREAMING_OPERATIONS
]


def test_only_the_streaming_route_is_left_out_of_the_matrix() -> None:
    assert STREAMING_OPERATIONS <= set(DOMAIN_OPERATIONS)
    assert len(MATRIX_OPERATIONS) == len(DOMAIN_OPERATIONS) - 1


@pytest.mark.parametrize(("method", "path"), MATRIX_OPERATIONS)
@pytest.mark.parametrize("role", ["reader", "writer", "admin", "superadmin"])
def test_the_role_matrix_holds_for_every_operation(
    request: pytest.FixtureRequest,
    session: Session,
    matrix_process: AssignmentProcess,
    role: str,
    method: str,
    path: str,
) -> None:
    """403 exactly when the caller's role is below the operation's floor.

    The assertion is deliberately "403 or not 403" rather than an exact status:
    what is under test is the authorization boundary, and a caller who clears
    it may still be answered 404 or 422 by the domain. Requiring a 200 would
    mean building valid state for a hundred-odd operations, and would fail for
    reasons that have nothing to do with authorization.
    """
    identity = make_user(role)
    if ROLE_RANK[role] < ROLE_RANK["admin"]:
        factories.enrol(session, matrix_process, identity, display_name=role)
    client = _identity_client(session, identity)

    concrete_path = concrete(path.replace("{process_id}", str(matrix_process.id)))
    response = client.request(method, concrete_path)

    expected_forbidden = ROLE_RANK[role] < ROLE_RANK[required_role(method, path)]
    assert (response.status_code == 403) is expected_forbidden, (
        f"{method} {path} answered {response.status_code} for {role}"
    )


# ── Ownership proven against a second account of the same role ───────────────


def test_a_second_writer_cannot_reach_the_first_writers_records(
    session: Session, writer_user: UserModel
) -> None:
    """The `WRITER` gate is not the whole rule — ownership is the rest of it."""
    process = factories.make_assignment_process(session)
    mine = factories.enrol(session, process, writer_user, display_name="First")
    intruder = make_user("writer")
    factories.enrol(session, process, intruder, display_name="Second")
    meeting = factories.make_meeting_session(
        session, process, status=MeetingSessionStatus.OPEN
    )
    turn = factories.make_selection_turn(session, meeting, mine)
    client = _identity_client(session, intruder)

    turn_action = client.post(
        f"/reparto/assignment-processes/{process.id}"
        f"/meeting-sessions/{meeting.id}/turns/{turn.id}/start"
    )
    profile_edit = client.patch(
        f"/reparto/teacher-profiles/{mine.teacher_profile_id}",
        json={"display_name": "Renamed by a stranger"},
    )

    assert turn_action.status_code == 403
    assert profile_edit.status_code == 403


def test_direct_choice_cannot_name_another_participant() -> None:
    """Ownership is structural here, not merely checked.

    The request schema has no participant field at all, so there is no payload
    a teacher could build that binds a slot to somebody else — the controller
    resolves the caller's own participation row and nothing else.
    """
    schema = app.openapi()["components"]["schemas"]["AssignmentDirectChoice"]
    assert "process_teacher_id" not in schema["properties"]
    assert "teacher_profile_id" not in schema["properties"]


def test_a_recorded_head_is_re_evaluated_from_the_role_on_every_request(
    session: Session, writer_user: UserModel
) -> None:
    """§21.2's live re-verification, from the route's side.

    An account recorded as a department's head but holding a sub-``ADMIN`` role
    is refused — the binding is read fresh from the caller's own role on this
    request, never cached from the moment the binding was made.
    """
    process = factories.make_assignment_process(session)
    department = session.get(Department, process.department_id)
    assert department is not None
    department.department_head_user_id = uuid.UUID(str(writer_user.id))
    session.add(department)
    session.commit()
    factories.enrol(session, process, writer_user)
    client = _identity_client(session, writer_user)

    response = client.post(f"/reparto/assignment-processes/{process.id}/teaching-plan")
    assert response.status_code == 403
