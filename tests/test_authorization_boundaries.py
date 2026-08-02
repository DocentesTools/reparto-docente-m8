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
from reparto_service.enums import MeetingSessionStatus
from reparto_service.main import app
from tests import factories

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
