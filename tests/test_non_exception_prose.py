"""C11 stable-code contract for service prose outside HTTP exceptions."""

from __future__ import annotations

import json
from pathlib import Path
from string import Formatter

from reparto_service.schemas.non_exception_prose import (
    NON_EXCEPTION_PROSE_PARAM_CONTRACT,
)


CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "non-exception-prose-taxonomy.json"
)


def _template_fields(template: str) -> frozenset[str]:
    return frozenset(
        field for _, field, _, _ in Formatter().parse(template) if field is not None
    )


def test_non_exception_prose_taxonomy_matches_the_typed_contract() -> None:
    taxonomy = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert taxonomy["schema_version"] == 1
    assert "additive-only" in taxonomy["compatibility"]
    records = {record["code"]: record for record in taxonomy["codes"]}

    assert set(records) == set(NON_EXCEPTION_PROSE_PARAM_CONTRACT)
    for code, param_names in NON_EXCEPTION_PROSE_PARAM_CONTRACT.items():
        record = records[code]
        assert frozenset(record["param_names"]) == param_names
        assert _template_fields(record["message_template"]) == param_names
        assert record["field"] in {
            "GroupSubjectBulkConflict.reason",
            "GroupSubjectBulkValidationError.message",
        }
