"""The teacher-profile claim-code flow (remediation `W1.4`).

The department head cannot look a colleague's user id up: `fa-auth-m8`
restricts its accounts directory to superusers by its own design, and this
service may not be built around widening it. So a head mints a single-use code
and the teacher redeems it with their own token.

These tests hold the three properties that make that safe — the code is never
readable twice, it is consumed by the redemption that succeeds, and it can only
ever bind the account presenting it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from reparto_service.db_models.audit_events import AuditEvent
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.enums import AuditEventType
from reparto_service.services.claim_codes import (
    CLAIM_CODE_ALPHABET,
    CLAIM_CODE_LENGTH,
    hash_claim_code,
)
from tests.conftest import identity_client, make_user
from tests.factories import (
    make_assignment_process,
    make_process_teacher,
    make_teacher_profile,
)


def _issue_code(client: TestClient, profile_id: str) -> dict[str, str]:
    response = client.post(f"/reparto/teacher-profiles/{profile_id}/claim-code")
    assert response.status_code == 201, response.text
    return response.json()


def _claim(session: Session, user, code: str):
    return identity_client(session, user).post(
        "/reparto/teacher-profiles/claim", json={"claim_code": code}
    )


def test_issue_claim_code_returns_a_transcribable_code(
    client: TestClient, session: Session
) -> None:
    profile = make_teacher_profile(session, display_name="Unclaimed")
    body = _issue_code(client, str(profile.id))

    assert body["teacher_profile_id"] == str(profile.id)
    assert body["display_name"] == "Unclaimed"
    assert body["claim_code"] == body["claim_code"].upper()
    assert len(body["claim_code"].replace("-", "")) == CLAIM_CODE_LENGTH
    assert set(body["claim_code"].replace("-", "")) <= set(CLAIM_CODE_ALPHABET)
    assert datetime.fromisoformat(body["expires_at"]) > datetime.now(tz=timezone.utc)


def test_the_code_is_stored_hashed_and_never_served_again(
    client: TestClient, session: Session
) -> None:
    """A lost code is reissued, never recovered — including by a database read."""
    profile = make_teacher_profile(session)
    code = _issue_code(client, str(profile.id))["claim_code"]

    session.expire_all()
    stored = session.get(TeacherProfile, profile.id)
    assert stored is not None
    assert stored.claim_code_hash == hash_claim_code(code)
    assert code.replace("-", "") not in str(stored.claim_code_hash)

    read_back = client.get(f"/reparto/teacher-profiles/{profile.id}").json()
    assert "claim_code" not in read_back
    assert "claim_code_hash" not in read_back
    assert "claim_code_expires_at" not in read_back


def test_issue_claim_code_is_department_head_only(
    writer_client: TestClient, session: Session
) -> None:
    profile = make_teacher_profile(session)
    response = writer_client.post(f"/reparto/teacher-profiles/{profile.id}/claim-code")
    assert response.status_code == 403


def test_issue_claim_code_404s_for_an_unknown_profile(client: TestClient) -> None:
    response = client.post(f"/reparto/teacher-profiles/{uuid.uuid4()}/claim-code")
    assert response.status_code == 404


def test_issue_claim_code_refuses_an_already_linked_profile(
    client: TestClient, session: Session
) -> None:
    """A code over a live linkage would hand one teacher's participation away."""
    profile = make_teacher_profile(session, user_id=uuid.uuid4())
    response = client.post(f"/reparto/teacher-profiles/{profile.id}/claim-code")
    assert response.status_code == 409
    assert "unlink it before" in response.json()["detail"]


def test_reissuing_replaces_the_outstanding_code(
    client: TestClient, session: Session
) -> None:
    """One live code per profile: reissuing is how a leaked code is revoked."""
    profile = make_teacher_profile(session)
    first = _issue_code(client, str(profile.id))["claim_code"]
    second = _issue_code(client, str(profile.id))["claim_code"]
    assert first != second

    assert _claim(session, make_user("writer"), first).status_code == 400
    assert _claim(session, make_user("writer"), second).status_code == 200


def test_a_teacher_claims_their_own_profile_with_no_superuser_anywhere(
    client: TestClient, session: Session
) -> None:
    """The whole `L1` walkthrough: a head issues, a teacher claims, nobody looks up."""
    profile = make_teacher_profile(session, display_name="Claimant")
    code = _issue_code(client, str(profile.id))["claim_code"]
    teacher_user = make_user("writer")

    response = _claim(session, teacher_user, code)

    assert response.status_code == 200
    assert response.json()["user_id"] == str(teacher_user.id)
    assert response.json()["id"] == str(profile.id)


def test_a_reader_may_claim(client: TestClient, session: Session) -> None:
    """The floor is the reader floor: the code is the credential, not the role.

    A read-only participant would otherwise never reach their own view, since
    the linkage is the only thing that makes *My view* resolvable at all.
    """
    profile = make_teacher_profile(session)
    code = _issue_code(client, str(profile.id))["claim_code"]
    reader_user = make_user("reader")

    response = _claim(session, reader_user, code)

    assert response.status_code == 200
    assert response.json()["user_id"] == str(reader_user.id)


def test_a_claim_is_single_use(client: TestClient, session: Session) -> None:
    profile = make_teacher_profile(session)
    code = _issue_code(client, str(profile.id))["claim_code"]

    assert _claim(session, make_user("writer"), code).status_code == 200
    assert _claim(session, make_user("writer"), code).status_code == 400

    session.expire_all()
    stored = session.get(TeacherProfile, profile.id)
    assert stored is not None
    assert stored.claim_code_hash is None
    assert stored.claim_code_expires_at is None


def test_a_claim_code_is_accepted_however_it_was_transcribed(
    client: TestClient, session: Session
) -> None:
    """Case and separators belong to the transcriber, not to the credential."""
    profile = make_teacher_profile(session)
    code = _issue_code(client, str(profile.id))["claim_code"]

    response = _claim(session, make_user("writer"), code.lower().replace("-", " "))
    assert response.status_code == 200


@pytest.mark.parametrize("presented", ["unknown", "expired"])
def test_an_unusable_code_is_refused_without_saying_why(
    client: TestClient, session: Session, presented: str
) -> None:
    """One wording for unknown, expired and already-linked.

    Distinguishing them would tell a caller holding a wrong code which half of
    it to vary.
    """
    profile = make_teacher_profile(session)
    code = _issue_code(client, str(profile.id))["claim_code"]
    session.expire_all()
    stored = session.get(TeacherProfile, profile.id)
    assert stored is not None
    stored.claim_code_expires_at = datetime.now(tz=timezone.utc) - timedelta(minutes=1)
    session.add(stored)
    session.commit()

    response = _claim(
        session,
        make_user("writer"),
        code if presented == "expired" else "ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ",
    )
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Claim code is not valid, or has expired or been used."
    )


def test_a_code_minted_before_the_profile_was_linked_is_dead(
    client: TestClient, session: Session
) -> None:
    """The mint-time guard is not the only one: the linkage is re-read on claim."""
    profile = make_teacher_profile(session)
    code = _issue_code(client, str(profile.id))["claim_code"]
    relinked = client.post(
        f"/reparto/teacher-profiles/{profile.id}/link-user",
        json={"user_id": str(uuid.uuid4())},
    )
    assert relinked.status_code == 200

    assert _claim(session, make_user("writer"), code).status_code == 400


def test_a_claim_reuses_the_one_profile_per_account_rule(
    client: TestClient, session: Session
) -> None:
    """The 409 is `link_user`'s, not a second copy of it — and it costs nothing.

    A caller refused because they already hold a profile is not the teacher the
    head issued the code to, so the code stays redeemable for the one who is.
    """
    teacher_user = make_user("writer")
    make_teacher_profile(
        session, display_name="Already mine", user_id=uuid.UUID(str(teacher_user.id))
    )
    profile = make_teacher_profile(session, display_name="Another roster row")
    code = _issue_code(client, str(profile.id))["claim_code"]

    refused = _claim(session, teacher_user, code)
    assert refused.status_code == 409
    assert "already linked to another teacher profile" in refused.json()["detail"]

    assert _claim(session, make_user("writer"), code).status_code == 200


def test_both_halves_are_audited_on_every_participating_process(
    client: TestClient, session: Session
) -> None:
    """`AuditEvent` is process-scoped, so the trail lands where it is read."""
    process = make_assignment_process(session)
    profile = make_teacher_profile(session, display_name="Participant")
    make_process_teacher(session, process, profile)
    code = _issue_code(client, str(profile.id))["claim_code"]
    teacher_user = make_user("writer")
    assert _claim(session, teacher_user, code).status_code == 200

    events = session.exec(
        select(AuditEvent).where(AuditEvent.entity_type == "teacher_profile")
    ).all()
    by_type = {event.event_type: event for event in events}
    assert set(by_type) == {
        AuditEventType.TEACHER_PROFILE_CLAIM_CODE_ISSUED.value,
        AuditEventType.TEACHER_PROFILE_CLAIMED.value,
    }
    for event in events:
        assert event.assignment_process_id == process.id
        assert event.entity_id == profile.id
        assert "claim_code" not in str(event.after_json)
        assert "claim_code" not in str(event.before_json)
    claimed = by_type[AuditEventType.TEACHER_PROFILE_CLAIMED.value]
    assert claimed.actor_user_id == uuid.UUID(str(teacher_user.id))
    assert claimed.before_json is not None
    assert claimed.before_json["user_id"] is None
    assert claimed.after_json is not None
    assert claimed.after_json["user_id"] == str(teacher_user.id)


def test_a_profile_in_no_process_writes_no_audit_row(
    client: TestClient, session: Session
) -> None:
    """There is no reparto for the event to belong to, and none is invented."""
    profile = make_teacher_profile(session)
    code = _issue_code(client, str(profile.id))["claim_code"]
    assert _claim(session, make_user("writer"), code).status_code == 200

    rows = session.exec(
        select(AuditEvent).where(AuditEvent.entity_type == "teacher_profile")
    ).all()
    assert list(rows) == []
