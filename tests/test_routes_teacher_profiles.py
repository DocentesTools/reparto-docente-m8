"""API tests for ``/reparto/teacher-profiles``."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def test_list_profiles_empty(client: TestClient) -> None:
    resp = client.get("/reparto/teacher-profiles/")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_create_profile_success(client: TestClient) -> None:
    resp = client.post(
        "/reparto/teacher-profiles/",
        json={"display_name": "Anna Test"},
    )
    assert resp.status_code == 201
    assert resp.json()["display_name"] == "Anna Test"


def test_create_profile_blocks_reader(reader_client: TestClient) -> None:
    resp = reader_client.post(
        "/reparto/teacher-profiles/",
        json={"display_name": "X"},
    )
    assert resp.status_code == 403


def test_get_profile_not_found(client: TestClient) -> None:
    resp = client.get(f"/reparto/teacher-profiles/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_update_profile(client: TestClient) -> None:
    create = client.post(
        "/reparto/teacher-profiles/",
        json={"display_name": "Original"},
    )
    pid = create.json()["id"]
    resp = client.patch(
        f"/reparto/teacher-profiles/{pid}",
        json={"display_name": "Updated"},
    )
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Updated"


def test_delete_profile(client: TestClient) -> None:
    create = client.post(
        "/reparto/teacher-profiles/",
        json={"display_name": "ToDelete"},
    )
    pid = create.json()["id"]
    resp = client.delete(f"/reparto/teacher-profiles/{pid}")
    assert resp.status_code == 200
    # Confirm gone
    resp = client.get(f"/reparto/teacher-profiles/{pid}")
    assert resp.status_code == 404


def test_filter_by_active_flag(
    client: TestClient,
) -> None:
    client.post(
        "/reparto/teacher-profiles/",
        json={"display_name": "Active", "active": True},
    )
    client.post(
        "/reparto/teacher-profiles/",
        json={"display_name": "Inactive", "active": False},
    )
    resp = client.get("/reparto/teacher-profiles/?active=true")
    body = resp.json()
    assert body["count"] == 1
    assert body["data"][0]["display_name"] == "Active"
