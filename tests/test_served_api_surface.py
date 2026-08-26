"""The served method+path surface, published as a tracked artifact.

`docs/served-api-surface.json` is this service's own statement of what it
serves: every ``METHOD path`` pair in the generated OpenAPI document, plus the
contract name/version and API prefix under which they are served.

It exists because a consumer cannot check its declared wrappers against a
document that is only produced by a running instance. The optional
`astro-reparto-m8` plugin declares a compatibility table of method+path pairs;
until this artifact existed, nothing compared that table with the served
surface, and the plugin shipped two `DELETE` wrappers against endpoints the
service had already replaced with a guarded `retire` action.

This test is the drift gate. The artifact is regenerated from the live app on
every run and compared; a route added, removed or re-verbed fails here until the
artifact is refreshed:

    REPARTO_WRITE_API_SURFACE=1 pytest tests/test_served_api_surface.py

Refreshing is a deliberate act with a reviewable diff, which is the point: the
artifact is only trustworthy to a cross-repository consumer while the change to
it is visible in this repository's history.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from reparto_service.core.config import settings
from reparto_service.main import app

#: Tracked artifact, relative to the repository root.
SURFACE_PATH = Path(__file__).resolve().parents[1] / "docs" / "served-api-surface.json"

#: Set to ``1`` to rewrite the artifact instead of asserting against it.
WRITE_ENV_VAR = "REPARTO_WRITE_API_SURFACE"


#: Served by Swagger from the application root rather than the API prefix, and
#: only when ``SET_DOCS`` is on. Recording it would make the artifact depend on
#: a deployment switch, so it is excluded and asserted as the *only* exclusion.
DOCS_REDIRECT = "GET /docs/oauth2-redirect"


def all_served_operations() -> list[str]:
    """Return every ``"METHOD /path"`` the app serves, in sorted order."""
    return sorted(
        f"{method.upper()} {path}"
        for path, methods in app.openapi()["paths"].items()
        for method in methods
    )


def build_surface() -> dict[str, Any]:
    """Return the served surface as the artifact records it.

    ``operations`` holds every ``"METHOD /path"`` served under ``api_prefix``.
    The prefix filter is what keeps the artifact deterministic: it is the one
    boundary a consumer joins its declared paths onto, and everything outside it
    is framework-owned and deployment-switched.
    """
    schema = app.openapi()
    prefix = settings.API_PREFIX
    operations = sorted(
        operation
        for operation in all_served_operations()
        if operation.split(" ", 1)[1].startswith(f"{prefix}/")
    )
    return {
        "contract": f"{settings.CONTRACT_NAME}@{settings.CONTRACT_VERSION}",
        "contract_range": settings.CONTRACT_RANGE,
        "api_prefix": settings.API_PREFIX,
        "openapi": schema["openapi"],
        "operations": operations,
    }


def write_surface(surface: dict[str, Any]) -> None:
    """Write *surface* to the tracked artifact with a stable, diffable shape."""
    SURFACE_PATH.write_text(
        json.dumps(surface, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )


def test_served_api_surface_artifact_matches_the_live_app() -> None:
    """The tracked artifact is exactly what this app serves."""
    surface = build_surface()
    if os.environ.get(WRITE_ENV_VAR) == "1":  # pragma: no cover - refresh path
        write_surface(surface)

    assert SURFACE_PATH.exists(), (
        f"{SURFACE_PATH.name} is missing; regenerate it with "
        f"{WRITE_ENV_VAR}=1 pytest {Path(__file__).name}"
    )
    tracked = json.loads(SURFACE_PATH.read_text(encoding="utf-8"))

    assert tracked["contract"] == surface["contract"]
    assert tracked["contract_range"] == surface["contract_range"]
    assert tracked["api_prefix"] == surface["api_prefix"]
    assert tracked["openapi"] == surface["openapi"]

    tracked_ops = set(tracked["operations"])
    served_ops = set(surface["operations"])
    assert served_ops - tracked_ops == set(), (
        "routes are served but absent from the artifact: "
        f"{sorted(served_ops - tracked_ops)}; refresh with {WRITE_ENV_VAR}=1"
    )
    assert tracked_ops - served_ops == set(), (
        "the artifact claims routes this app no longer serves: "
        f"{sorted(tracked_ops - served_ops)}; refresh with {WRITE_ENV_VAR}=1"
    )
    assert tracked["operations"] == surface["operations"], (
        "the artifact is not in the canonical sorted order; refresh with "
        f"{WRITE_ENV_VAR}=1"
    )


def test_no_domain_operation_is_served_outside_the_api_prefix() -> None:
    """The prefix filter above hides nothing a consumer would need.

    A consumer joins its declared path onto ``api_prefix``. An operation served
    outside the prefix is unreachable that way, so the artifact would be
    complete and still leave the consumer stranded — unless the only such
    operation is the framework's own Swagger redirect.
    """
    prefix = settings.API_PREFIX
    outside = [
        operation
        for operation in all_served_operations()
        if not operation.split(" ", 1)[1].startswith(f"{prefix}/")
    ]
    assert set(outside) <= {DOCS_REDIRECT}


def test_the_activity_and_cell_retire_actions_replaced_delete() -> None:
    """The §20.12 guarded ``retire`` action, not an unrestricted ``DELETE``.

    Stated here as a named expectation rather than left implicit in the
    artifact, because this exact pair is what the plugin declared wrongly for
    two releases.
    """
    operations = set(build_surface()["operations"])
    for collection, identifier in (
        ("teaching-activities", "activity_id"),
        ("group-subjects", "group_subject_id"),
    ):
        item = (
            f"/reparto/assignment-processes/{{process_id}}/"
            f"{collection}/{{{identifier}}}"
        )
        assert f"POST {item}/retire" in operations
        assert f"DELETE {item}" not in operations
