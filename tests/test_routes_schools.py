"""API tests for ``/reparto/schools``."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def test_list_schools_empty(client: TestClient) -> None:
    resp = client.get("/reparto/schools/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0


def test_create_school_success(client: TestClient) -> None:
    resp = client.post(
        "/reparto/schools/",
        json={"name": "IES Test", "locality": "Test"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "IES Test"
    assert body["locality"] == "Test"
    assert body["region"] == "Andalucía"


def test_create_school_blocks_reader(reader_client: TestClient) -> None:
    resp = reader_client.post(
        "/reparto/schools/",
        json={"name": "IES Other"},
    )
    assert resp.status_code == 403


def test_get_school_not_found(client: TestClient) -> None:
    resp = client.get(f"/reparto/schools/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_update_school(client: TestClient) -> None:
    create = client.post(
        "/reparto/schools/",
        json={"name": "IES A"},
    )
    school_id = create.json()["id"]
    resp = client.patch(f"/reparto/schools/{school_id}", json={"locality": "Sevilla"})
    assert resp.status_code == 200
    assert resp.json()["locality"] == "Sevilla"
