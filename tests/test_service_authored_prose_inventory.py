"""Structural gate for public response fields that can carry human text.

The inventory deliberately classifies every string-capable field reachable
from a declared FastAPI response model, not only fields currently named
``message`` or ``reason``. That wider boundary makes a new prose field fail
closed until its authorship and catalog/code owner are recorded.
"""

from __future__ import annotations

import json
from pathlib import Path

from reparto_service.main import app

INVENTORY_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "service-authored-prose-inventory.json"
)
OWNERSHIP_CLASSES = frozenset(
    {
        "service-authored-prose",
        "mixed-authorship",
        "user-authored-content",
        "human-readable-identity",
        "machine-data",
    }
)
CATALOG_OWNED_CLASSES = frozenset({"service-authored-prose", "mixed-authorship"})


def _mapping(value: object, *, label: str) -> dict[str, object]:
    """Narrow a JSON/OpenAPI value to a string-keyed object."""
    assert isinstance(value, dict), f"{label} must be an object"
    assert all(isinstance(key, str) for key in value), f"{label} keys must be strings"
    return value


def _sequence(value: object, *, label: str) -> list[object]:
    """Narrow a JSON/OpenAPI value to an array."""
    assert isinstance(value, list), f"{label} must be an array"
    return value


def _directly_can_carry_text(schema: dict[str, object]) -> bool:
    """Return whether a property itself can hold free text or opaque JSON."""
    if schema.get("type") == "string":
        if schema.get("format") in {"date", "date-time", "uuid"}:
            return False
        return True
    for union_key in ("anyOf", "oneOf"):
        union = schema.get(union_key)
        if union is not None:
            return any(
                _directly_can_carry_text(_mapping(item, label=union_key))
                for item in _sequence(union, label=union_key)
            )
    if schema.get("type") == "array" and schema.get("items") is not None:
        return _directly_can_carry_text(_mapping(schema["items"], label="array items"))
    if schema.get("type") == "object" and "additionalProperties" in schema:
        additional = schema["additionalProperties"]
        if additional is True:
            return True
        return _directly_can_carry_text(
            _mapping(additional, label="additionalProperties")
        )
    return False


def _discover_response_text_fields(openapi: dict[str, object]) -> set[str]:
    """Derive string-capable fields from the public OpenAPI response graph."""
    components_root = _mapping(openapi.get("components"), label="components")
    components = _mapping(components_root.get("schemas"), label="component schemas")
    discovered: set[str] = set()
    seen_components: set[str] = set()

    def walk(schema_value: object, *, owner: str) -> None:
        schema = _mapping(schema_value, label=f"schema for {owner}")
        reference = schema.get("$ref")
        if isinstance(reference, str):
            prefix = "#/components/schemas/"
            assert reference.startswith(prefix), f"unsupported schema ref: {reference}"
            component_name = reference.removeprefix(prefix)
            if component_name in seen_components:
                return
            seen_components.add(component_name)
            walk(components[component_name], owner=component_name)
            return

        properties_value = schema.get("properties")
        if properties_value is not None:
            properties = _mapping(properties_value, label=f"properties for {owner}")
            for field_name, field_schema_value in properties.items():
                field_schema = _mapping(
                    field_schema_value, label=f"schema for {owner}.{field_name}"
                )
                if _directly_can_carry_text(field_schema):
                    discovered.add(f"{owner}.{field_name}")
                walk(field_schema, owner=f"{owner}.{field_name}")

        for branch_key in ("anyOf", "oneOf", "allOf"):
            branches = schema.get(branch_key)
            if branches is not None:
                for branch in _sequence(branches, label=branch_key):
                    walk(branch, owner=owner)
        items = schema.get("items")
        if items is not None:
            walk(items, owner=owner)

    paths = _mapping(openapi.get("paths"), label="paths")
    for path, path_value in paths.items():
        path_item = _mapping(path_value, label=f"path {path}")
        for method, operation_value in path_item.items():
            if method.upper() not in {
                "DELETE",
                "GET",
                "HEAD",
                "OPTIONS",
                "PATCH",
                "POST",
                "PUT",
                "TRACE",
            }:
                continue
            operation = _mapping(operation_value, label=f"{method} {path}")
            responses = _mapping(operation.get("responses"), label="responses")
            for response_value in responses.values():
                response = _mapping(response_value, label="response")
                content_value = response.get("content")
                if content_value is None:
                    continue
                content = _mapping(content_value, label="response content")
                for media_value in content.values():
                    media = _mapping(media_value, label="response media")
                    response_schema = media.get("schema")
                    if response_schema is not None:
                        walk(response_schema, owner=f"{method.upper()} {path}")
    return discovered


def _public_response_text_fields() -> set[str]:
    """Return every text-capable field reachable from a route response model."""
    return _discover_response_text_fields(app.openapi())


def _load_inventory() -> dict[str, object]:
    """Load the tracked prose inventory as a validated top-level object."""
    raw: object = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert all(isinstance(key, str) for key in raw)
    return raw


def _object_list(value: object, *, label: str) -> list[dict[str, object]]:
    """Narrow a JSON value to a list of string-keyed objects."""
    assert isinstance(value, list), f"{label} must be a list"
    narrowed: list[dict[str, object]] = []
    for item in value:
        assert isinstance(item, dict), f"{label} entries must be objects"
        assert all(isinstance(key, str) for key in item), (
            f"{label} entry keys must be strings"
        )
        narrowed.append(item)
    return narrowed


def _string_list(value: object, *, label: str) -> list[str]:
    """Narrow a JSON value to an array of unique strings."""
    values = _sequence(value, label=label)
    assert all(isinstance(item, str) for item in values), (
        f"{label} entries must be strings"
    )
    narrowed = [item for item in values if isinstance(item, str)]
    assert len(narrowed) == len(set(narrowed)), f"{label} entries must be unique"
    return narrowed


def test_every_public_text_field_has_an_inventory_classification() -> None:
    """A response-model string cannot enter the API outside the inventory."""
    inventory = _load_inventory()
    entries = _object_list(inventory.get("fields"), label="fields")
    inventoried_fields = {entry.get("field") for entry in entries}
    machine_fields = set(
        _string_list(inventory.get("machine_fields"), label="machine_fields")
    )

    assert all(isinstance(field, str) for field in inventoried_fields)
    assert len(inventoried_fields) == len(entries), "inventory fields must be unique"
    assert inventoried_fields.isdisjoint(machine_fields)
    assert _public_response_text_fields() == inventoried_fields | machine_fields


def test_inventory_records_ownership_and_catalog_owner() -> None:
    """Service and mixed prose always name the code/catalog that owns it."""
    inventory = _load_inventory()
    assert inventory.get("schema_version") == 1
    entries = _object_list(inventory.get("fields"), label="fields")
    entries += _object_list(
        inventory.get("non_model_surfaces"), label="non_model_surfaces"
    )

    for entry in entries:
        ownership = entry.get("ownership")
        assert ownership in OWNERSHIP_CLASSES
        catalog_owner = entry.get("catalog_owner")
        if ownership in CATALOG_OWNED_CLASSES:
            assert isinstance(catalog_owner, str) and catalog_owner.strip()
        else:
            assert catalog_owner is None
        notes = entry.get("notes")
        assert isinstance(notes, str) and notes.strip()


def test_gate_detects_a_new_uncatalogued_prose_field() -> None:
    """Prove the structural gate sees a newly added arbitrary prose field."""
    synthetic_openapi: dict[str, object] = {
        "components": {
            "schemas": {
                "ExpandedResponse": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer"},
                        "newly_added_caption": {"type": "string"},
                    },
                }
            }
        },
        "paths": {
            "/probe": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ExpandedResponse"
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
    }

    assert _discover_response_text_fields(synthetic_openapi) == {
        "ExpandedResponse.newly_added_caption"
    }


def test_required_service_prose_surfaces_are_inventoried() -> None:
    """Pin the two non-exception fields that exposed the original C6 gap."""
    inventory = _load_inventory()
    entries = _object_list(inventory.get("fields"), label="fields")
    by_field = {entry.get("field"): entry for entry in entries}

    assert by_field["GroupSubjectBulkConflict.reason"]["ownership"] == (
        "service-authored-prose"
    )
    assert by_field["GroupSubjectBulkValidationError.message"]["ownership"] == (
        "service-authored-prose"
    )
    assert "C11-non-exception-prose" in str(
        by_field["GroupSubjectBulkConflict.reason"]["catalog_owner"]
    )
    assert "C11-non-exception-prose" in str(
        by_field["GroupSubjectBulkValidationError.message"]["catalog_owner"]
    )
