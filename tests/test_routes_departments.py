"""API tests for ``/reparto/departments``."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from tests import factories


def test_list_departments_empty(client: TestClient) -> None:
    resp = client.get("/reparto/departments/")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_create_department_success(client: TestClient, session: Session) -> None:
    school = factories.make_school(session)
    resp = client.post(
        "/reparto/departments/",
        json={"school_id": str(school.id), "name": "Matemáticas"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Matemáticas"
    assert body["slug"] == "matematicas"


def test_create_department_missing_school(client: TestClient) -> None:
    resp = client.post(
        "/reparto/departments/",
        json={"school_id": str(uuid.uuid4()), "name": "X"},
    )
    assert resp.status_code == 404


def test_filter_departments_by_school(client: TestClient, session: Session) -> None:
    s1 = factories.make_school(session, name="A")
    s2 = factories.make_school(session, name="B")
    factories.make_department(session, s1, name="D1")
    factories.make_department(session, s2, name="D2")
    resp = client.get(f"/reparto/departments/?school_id={s1.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["data"][0]["name"] == "D1"


def test_update_department(client: TestClient, session: Session) -> None:
    school = factories.make_school(session)
    dept = factories.make_department(session, school, name="Old")
    resp = client.patch(
        f"/reparto/departments/{dept.id}",
        json={"name": "New"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"
