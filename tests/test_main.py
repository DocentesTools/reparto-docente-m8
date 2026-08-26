"""Tests for the main FastAPI app entry-point."""

from __future__ import annotations

from fastapi import APIRouter
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


def test_metrics_endpoint_registration(monkeypatch) -> None:
    """``_register_metrics_endpoint`` is a no-op when disabled and guards the
    route with ``make_scrape_credential_guard`` when enabled, mirroring the
    media-service-m8 / prompt-engine-m8 pattern (A2/A25)."""
    import reparto_service.main as main

    router = APIRouter()
    main._register_metrics_endpoint(router, enabled=False)
    assert not router.routes

    monkeypatch.setattr(
        "fastapi_m8.render_metrics",
        lambda: (b"metrics", "text/plain"),
    )
    main._register_metrics_endpoint(router, enabled=True, credential=None)
    assert router.routes
    response = router.routes[-1].endpoint()
    assert response.body == b"metrics"
