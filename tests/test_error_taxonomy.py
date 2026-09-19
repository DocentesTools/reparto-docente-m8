"""Contract tests for the C7 Reparto-owned HTTP error taxonomy."""

from __future__ import annotations

import ast
import json
import re
import uuid
from collections import defaultdict
from enum import Enum
from pathlib import Path

import pytest
from fastapi import status

from reparto_service.core.errors import DomainHTTPException


ROOT = Path(__file__).parents[1]
SOURCE_ROOT = ROOT / "reparto_service"
CATALOG_PATH = ROOT / "docs" / "error-taxonomy.json"
CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.]{0,79}$")


class _ExampleEnum(Enum):
    VALUE = "enum-value"


def _message_template(node: ast.expr) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            value.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
            else "{}"
            for value in node.values
        )
    return ast.unparse(node)


def _source_catalog() -> list[dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        stack: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Name) and node.func.id == "HTTPException":
                    pytest.fail(f"Raw HTTPException call at {path}:{node.lineno}")
                if not (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "DomainHTTPException"
                ):
                    self.generic_visit(node)
                    return
                keywords = {
                    keyword.arg: keyword.value
                    for keyword in node.keywords
                    if keyword.arg
                }
                assert set(keywords) in (
                    {"status_code", "code", "message", "params"},
                    {"status_code", "code", "message", "params", "headers"},
                )
                code = ast.literal_eval(keywords["code"])
                assert isinstance(code, str)
                assert CODE_PATTERN.fullmatch(code)
                params = keywords["params"]
                assert isinstance(params, ast.Dict)
                param_names: list[str] = []
                for key in params.keys:
                    assert key is not None
                    param_name = ast.literal_eval(key)
                    assert isinstance(param_name, str)
                    param_names.append(param_name)
                param_names.sort()
                message = keywords["message"]
                if isinstance(message, ast.Constant):
                    assert isinstance(message.value, str)
                    assert not param_names
                elif isinstance(message, ast.JoinedStr):
                    interpolation_count = sum(
                        isinstance(value, ast.FormattedValue)
                        for value in message.values
                    )
                    assert len(param_names) == interpolation_count
                else:
                    assert param_names
                current: dict[str, object] = {
                    "code": code,
                    "message_template": _message_template(message),
                    "param_names": param_names,
                    "status": ast.unparse(keywords["status_code"]),
                    "locations": [],
                }
                record = records.setdefault(code, current)
                assert {
                    key: record[key]
                    for key in ("code", "message_template", "param_names", "status")
                } == {
                    key: current[key]
                    for key in ("code", "message_template", "param_names", "status")
                }
                locations = record["locations"]
                assert isinstance(locations, list)
                locations.append(f"{path.relative_to(ROOT).as_posix()}:{stack[-1]}")
                self.generic_visit(node)

        Visitor().visit(tree)
    return [records[code] for code in sorted(records)]


def test_domain_error_has_exact_wire_shape_and_json_safe_params() -> None:
    trace_id = uuid.uuid4()
    error = DomainHTTPException(
        status_code=status.HTTP_409_CONFLICT,
        code="test.sample",
        message="English compatibility message",
        params={
            "none": None,
            "text": "value",
            "integer": 2,
            "decimal": 2.5,
            "flag": True,
            "enum": _ExampleEnum.VALUE,
            "mapping": {"trace": trace_id},
            "sequence": (trace_id, "value"),
            "fallback": trace_id,
        },
        headers={"Retry-After": "1"},
    )

    assert error.detail == {
        "code": "test.sample",
        "message": "English compatibility message",
        "params": {
            "none": None,
            "text": "value",
            "integer": 2,
            "decimal": 2.5,
            "flag": True,
            "enum": "enum-value",
            "mapping": {"trace": str(trace_id)},
            "sequence": [str(trace_id), "value"],
            "fallback": str(trace_id),
        },
    }
    assert error.headers == {"Retry-After": "1"}


@pytest.mark.parametrize("code", ["UPPER", "starts-with-hyphen", "bad code", "a" * 81])
def test_domain_error_rejects_unstable_code_shapes(code: str) -> None:
    with pytest.raises(ValueError, match="Invalid domain error code"):
        DomainHTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=code,
            message="message",
            params={},
        )


def test_catalog_is_additive_contract_and_matches_every_service_owned_site() -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert catalog["schema_version"] == 1
    assert catalog["contract"] == "reparto-domain-error-taxonomy-v1"
    assert catalog["compatibility"] == "additive-only"
    assert catalog["wire_shape"] == {
        "detail": {"code": "string", "message": "string", "params": "object"}
    }
    assert catalog["codes"] == _source_catalog()
    assert len(catalog["codes"]) == 157
    assert sum(len(record["locations"]) for record in catalog["codes"]) == 164


def test_reused_codes_keep_one_status_message_and_parameter_contract() -> None:
    grouped: dict[str, set[tuple[str, str, tuple[str, ...]]]] = defaultdict(set)
    for record in _source_catalog():
        param_names = record["param_names"]
        assert isinstance(param_names, list)
        grouped[str(record["code"])].add(
            (
                str(record["status"]),
                str(record["message_template"]),
                tuple(str(name) for name in param_names),
            )
        )
    assert all(len(contracts) == 1 for contracts in grouped.values())
