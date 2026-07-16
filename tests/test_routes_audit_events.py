"""API tests for the process-scoped audit trail (plan §8.14, §13.1).

Covers the "Extend audit events" task: every three-stage mutation is recorded
with a canonical :class:`~reparto_service.enums.AuditEventType` value, and the
read endpoint can filter the trail by ``event_type`` / ``entity_type``.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.enums import AuditEventType
from tests import factories

_BASE = "/reparto/assignment-processes"


def _event_types(payload: dict) -> list[str]:
    return [event["event_type"] for event in payload["data"]]


def test_three_stage_mutations_are_audited_with_canonical_types(
    client: TestClient, session: Session
) -> None:
    """Drive one mutation per stage and assert the recorded event types.

    This exercises both the registry (enum) call sites and the surviving raw
    string call sites through :meth:`record_audit_event`, proving the enum is
    normalised to the exact string the trail has always stored.
    """
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)

    # Stage 1 — configuration.
    teacher_resp = client.post(
        f"{_BASE}/{process.id}/teachers/",
        json={
            "assignment_process_id": str(process.id),
            "teacher_profile_id": str(profile.id),
            "base_weekly_hours": 18,
        },
    )
    assert teacher_resp.status_code == 201
    teacher_id = teacher_resp.json()["id"]

    subject_resp = client.post(
        f"{_BASE}/{process.id}/subjects/",
        json={"assignment_process_id": str(process.id), "name": "Maths"},
    )
    assert subject_resp.status_code == 201

    stage = factories.make_classroom_stage(session)
    group_resp = client.post(
        f"{_BASE}/{process.id}/groups/",
        json={
            "assignment_process_id": str(process.id),
            "classroom_stage_id": str(stage.id),
            "grade": 1,
            "group_code": "A",
            "label": "1 ESO A",
        },
    )
    assert group_resp.status_code == 201

    # Stage 2 — department teaching-load planning.
    allocation_resp = client.post(
        f"{_BASE}/{process.id}/allocation-revisions/",
        json={"allocated_group_weekly_hours": 120, "reason": "First allocation"},
    )
    assert allocation_resp.status_code == 201

    plan_resp = client.post(f"{_BASE}/{process.id}/teaching-plan")
    assert plan_resp.status_code == 201

    group_subject_resp = client.post(
        f"{_BASE}/{process.id}/group-subjects/",
        json={
            "assignment_process_id": str(process.id),
            "teaching_group_id": group_resp.json()["id"],
            "subject_id": subject_resp.json()["id"],
        },
    )
    assert group_subject_resp.status_code == 201

    # An audited extra-hours change (plan §3.8, §7.6).
    extra_resp = client.post(
        f"{_BASE}/{process.id}/teachers/{teacher_id}/extra-hours",
        json={"extra_weekly_hours": 4, "reason": "Cover maternity leave"},
    )
    assert extra_resp.status_code == 200

    audit_resp = client.get(f"{_BASE}/{process.id}/audit-events/")
    assert audit_resp.status_code == 200
    assert _event_types(audit_resp.json()) == [
        AuditEventType.PROCESS_TEACHER_CREATED.value,
        AuditEventType.SUBJECT_CREATED.value,
        AuditEventType.TEACHING_GROUP_CREATED.value,
        AuditEventType.ALLOCATION_REVISED.value,
        AuditEventType.TEACHING_PLAN_CREATED.value,
        AuditEventType.GROUP_SUBJECT_CREATED.value,
        AuditEventType.PROCESS_TEACHER_EXTRA_HOURS_UPDATED.value,
    ]


def test_process_lifecycle_audit_records_reason(
    client: TestClient, session: Session
) -> None:
    from reparto_service.enums import AssignmentProcessStatus

    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    transition_resp = client.post(
        f"{_BASE}/{process.id}/transition",
        json={"target_status": "ready_for_meeting"},
    )
    assert transition_resp.status_code == 200

    audit_resp = client.get(f"{_BASE}/{process.id}/audit-events/")
    assert audit_resp.status_code == 200
    event = audit_resp.json()["data"][0]
    assert event["event_type"] == AuditEventType.PROCESS_TRANSITIONED.value
    assert event["before_json"]["status"] == "draft"
    assert event["after_json"]["status"] == "ready_for_meeting"
    assert event["reason"] == "ready_for_meeting"


def _seed_mixed_trail(client: TestClient, session: Session):
    """Seed a process whose trail holds two entity types and three event types."""
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    teacher_resp = client.post(
        f"{_BASE}/{process.id}/teachers/",
        json={
            "assignment_process_id": str(process.id),
            "teacher_profile_id": str(profile.id),
            "base_weekly_hours": 10,
        },
    )
    teacher_id = teacher_resp.json()["id"]
    client.post(
        f"{_BASE}/{process.id}/allocation-revisions/",
        json={"allocated_group_weekly_hours": 120, "reason": "First"},
    )
    client.post(
        f"{_BASE}/{process.id}/teachers/{teacher_id}/extra-hours",
        json={"extra_weekly_hours": 2, "reason": "Extra load"},
    )
    return process


def test_filter_by_event_type(client: TestClient, session: Session) -> None:
    process = _seed_mixed_trail(client, session)
    resp = client.get(
        f"{_BASE}/{process.id}/audit-events/",
        params={"event_type": AuditEventType.ALLOCATION_REVISED.value},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert _event_types(body) == [AuditEventType.ALLOCATION_REVISED.value]


def test_filter_by_entity_type(client: TestClient, session: Session) -> None:
    process = _seed_mixed_trail(client, session)
    resp = client.get(
        f"{_BASE}/{process.id}/audit-events/",
        params={"entity_type": "process_teacher"},
    )
    assert resp.status_code == 200
    assert _event_types(resp.json()) == [
        AuditEventType.PROCESS_TEACHER_CREATED.value,
        AuditEventType.PROCESS_TEACHER_EXTRA_HOURS_UPDATED.value,
    ]


def test_filter_by_event_and_entity_type_combined(
    client: TestClient, session: Session
) -> None:
    process = _seed_mixed_trail(client, session)
    resp = client.get(
        f"{_BASE}/{process.id}/audit-events/",
        params={
            "event_type": AuditEventType.PROCESS_TEACHER_EXTRA_HOURS_UPDATED.value,
            "entity_type": "process_teacher",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["data"][0]["reason"] == "Extra load"


def test_filter_by_unknown_event_type_is_rejected(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = client.get(
        f"{_BASE}/{process.id}/audit-events/",
        params={"event_type": "not.a.real.event"},
    )
    assert resp.status_code == 422


def test_list_events_missing_process_is_404(
    client: TestClient, session: Session
) -> None:
    resp = client.get(f"{_BASE}/{uuid.uuid4()}/audit-events/")
    assert resp.status_code == 404


def test_audit_event_type_registry_is_canonical() -> None:
    """The registry values are unique, dotted ``entity.action`` strings."""
    values = [member.value for member in AuditEventType]
    assert len(values) == len(set(values))
    for value in values:
        entity, _, action = value.partition(".")
        assert entity and action, value
        assert value == value.lower()
    # The six categories named by the "Extend audit events" acceptance are all
    # present, including the lock/unlock names reserved for the plan-lock task.
    for reserved in (
        AuditEventType.ALLOCATION_REVISED,
        AuditEventType.GROUP_SUBJECT_BULK_APPLIED,
        AuditEventType.TEACHING_PLAN_LOCKED,
        AuditEventType.TEACHING_PLAN_UNLOCKED,
        AuditEventType.REQUIREMENTS_GENERATED,
        AuditEventType.REQUIREMENTS_RECONCILED,
        AuditEventType.PROCESS_TEACHER_EXTRA_HOURS_UPDATED,
    ):
        assert reserved.value in values
