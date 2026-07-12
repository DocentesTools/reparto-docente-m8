"""Classroom-stage and stage-backed teaching-group contract tests."""

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from tests import factories


def payload(stage: str = " Secundaria ") -> dict[str, object]:
    return {"stage": stage, "min_grade": 1, "max_grade": 4, "label": " ESO "}


def test_stage_crud_as_admin(admin_client: TestClient) -> None:
    created = admin_client.post("/reparto/classroom-stages/", json=payload())
    assert created.status_code == 201
    stage = created.json()
    assert stage["stage"] == "Secundaria"
    assert stage["label"] == "ESO"
    assert stage["created_at"] and stage["updated_at"]

    listed = admin_client.get("/reparto/classroom-stages/")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert (
        admin_client.get(f"/reparto/classroom-stages/{stage['id']}").json()["stage"]
        == "Secundaria"
    )

    updated = admin_client.patch(
        f"/reparto/classroom-stages/{stage['id']}",
        json={"stage": " Educación Secundaria ", "max_grade": 6},
    )
    assert updated.status_code == 200
    assert updated.json()["stage"] == "Educación Secundaria"
    assert updated.json()["max_grade"] == 6
    assert (
        admin_client.delete(f"/reparto/classroom-stages/{stage['id']}").status_code
        == 200
    )


def test_stage_create_as_superuser(superuser_client: TestClient) -> None:
    assert (
        superuser_client.post(
            "/reparto/classroom-stages/", json=payload("Primaria")
        ).status_code
        == 201
    )


def test_stage_mutations_reject_writer_and_reader(
    client: TestClient, reader_client: TestClient
) -> None:
    assert client.post("/reparto/classroom-stages/", json=payload()).status_code == 403
    assert (
        reader_client.post("/reparto/classroom-stages/", json=payload()).status_code
        == 403
    )


def test_stage_validation_and_duplicates(admin_client: TestClient) -> None:
    for invalid in [
        {**payload(), "stage": "   "},
        {**payload(), "label": "   "},
        {**payload(), "min_grade": 0},
        {**payload(), "max_grade": 0},
        {**payload(), "min_grade": 5, "max_grade": 4},
    ]:
        assert (
            admin_client.post("/reparto/classroom-stages/", json=invalid).status_code
            == 422
        )
    assert (
        admin_client.post("/reparto/classroom-stages/", json=payload()).status_code
        == 201
    )
    duplicate = admin_client.post("/reparto/classroom-stages/", json=payload())
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "classroom_stage_exists"


def test_stage_update_validates_final_range(
    admin_client: TestClient, session: Session
) -> None:
    stage = factories.make_classroom_stage(session)
    response = admin_client.patch(
        f"/reparto/classroom-stages/{stage.id}", json={"min_grade": 5}
    )
    assert response.status_code == 422


def test_referenced_stage_cannot_be_deleted(
    admin_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    group = factories.make_teaching_group(session, process)
    response = admin_client.delete(
        f"/reparto/classroom-stages/{group.classroom_stage_id}"
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "classroom_stage_in_use"


def test_group_requires_stage_and_generates_label(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    stage = factories.make_classroom_stage(session)
    missing = client.post(
        f"/reparto/assignment-processes/{process.id}/groups/",
        json={
            "assignment_process_id": str(process.id),
            "grade": 1,
            "group_code": "a",
        },
    )
    assert missing.status_code == 422
    created = client.post(
        f"/reparto/assignment-processes/{process.id}/groups/",
        json={
            "assignment_process_id": str(process.id),
            "classroom_stage_id": str(stage.id),
            "grade": 1,
            "group_code": "a",
        },
    )
    assert created.status_code == 201
    assert created.json()["label"] == "1° ESO A"
    assert created.json()["classroom_stage"]["stage"] == "Secundaria"
    assert "stage" not in created.json()


def test_group_grade_and_unknown_stage_validation(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    stage = factories.make_classroom_stage(session)
    base = {
        "assignment_process_id": str(process.id),
        "classroom_stage_id": str(stage.id),
        "group_code": "A",
    }
    for grade in [0, 5]:
        response = client.post(
            f"/reparto/assignment-processes/{process.id}/groups/",
            json={**base, "grade": grade},
        )
        assert response.status_code == 422
    unknown = client.post(
        f"/reparto/assignment-processes/{process.id}/groups/",
        json={**base, "classroom_stage_id": str(uuid.uuid4()), "grade": 1},
    )
    assert unknown.status_code == 404


def test_group_update_validates_final_state_and_preserves_custom_label(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    group = factories.make_teaching_group(session, process, label="Custom room")
    invalid = client.patch(
        f"/reparto/assignment-processes/{process.id}/groups/{group.id}",
        json={"grade": 5},
    )
    assert invalid.status_code == 422
    updated = client.patch(
        f"/reparto/assignment-processes/{process.id}/groups/{group.id}",
        json={"grade": 2},
    )
    assert updated.status_code == 200
    assert updated.json()["label"] == "Custom room"
    regenerated = client.patch(
        f"/reparto/assignment-processes/{process.id}/groups/{group.id}",
        json={"label": "", "group_code": "b"},
    )
    assert regenerated.json()["label"] == "2° ESO B"


def test_bulk_create_is_inclusive_and_atomic(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    stage = factories.make_classroom_stage(session)
    url = f"/reparto/assignment-processes/{process.id}/groups/bulk"
    body = {
        "classroom_stage_id": str(stage.id),
        "grade": 1,
        "group_start": "a",
        "group_end": "c",
    }
    created = client.post(url, json=body)
    assert created.status_code == 201
    assert [item["group_code"] for item in created.json()["data"]] == [
        "A",
        "B",
        "C",
    ]
    assert [item["label"] for item in created.json()["data"]] == [
        "1° ESO A",
        "1° ESO B",
        "1° ESO C",
    ]
    conflict = client.post(url, json={**body, "group_start": "C", "group_end": "D"})
    assert conflict.status_code == 409
    listed = client.get(f"/reparto/assignment-processes/{process.id}/groups/").json()
    assert listed["count"] == 3


def test_bulk_rejects_invalid_ranges(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    stage = factories.make_classroom_stage(session)
    url = f"/reparto/assignment-processes/{process.id}/groups/bulk"
    for start, end in [("C", "A"), ("1", "2"), ("Á", "B")]:
        response = client.post(
            url,
            json={
                "classroom_stage_id": str(stage.id),
                "grade": 1,
                "group_start": start,
                "group_end": end,
            },
        )
        assert response.status_code == 422
