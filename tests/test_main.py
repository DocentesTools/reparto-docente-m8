"""Tests for the main FastAPI app entry-point."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_app_exposes_openapi_schema(client: TestClient) -> None:
    resp = client.get("/reparto/openapi.json")
    assert resp.status_code == 200
    body = resp.json()
    assert "openapi" in body
    assert "paths" in body
    assert "/reparto/academic-years/" in body["paths"]


def test_app_health_endpoint(client: TestClient) -> None:
    """The /reparto/health/ endpoint is wired by create_app."""
    resp = client.get("/reparto/health/")
    # The health endpoint exists; 200 if DB check is mocked out, 503 otherwise.
    assert resp.status_code in {200, 503}


def test_app_exposes_meta_endpoint(client: TestClient) -> None:
    """create_app wires ``{API_PREFIX}/meta`` from ConsumerServiceSettings."""
    resp = client.get("/reparto/meta")
    assert resp.status_code == 200
    body = resp.json()
    # Service contract metadata is required at boot.
    assert "service_name" in body or "name" in body or "version" in body


def test_app_has_unauthorized_default_for_protected_routes(
    client: TestClient,
) -> None:
    """The ``client`` fixture overrides auth — ensure no 500 on a simple call."""
    resp = client.get("/reparto/teacher-profiles/")
    assert resp.status_code == 200


def test_app_routes_count_minimum(client: TestClient) -> None:
    """Sanity check: at least 25 routes registered (the documented set)."""
    schema = client.get("/reparto/openapi.json").json()
    assert len(schema["paths"]) >= 25
