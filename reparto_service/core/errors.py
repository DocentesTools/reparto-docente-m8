"""Stable domain-error envelope for Reparto HTTP responses."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import TypeAlias

from fastapi import HTTPException


ScalarJsonValue: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = ScalarJsonValue | list["JsonValue"] | dict[str, "JsonValue"]

_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.]{0,79}$")


def _json_value(value: object) -> JsonValue:
    """Normalize a domain parameter into a JSON-safe, language-neutral value."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence):
        return [_json_value(item) for item in value]
    return str(value)


class DomainHTTPException(HTTPException):
    """Raise a service-owned error with the stable C7 wire envelope."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        params: Mapping[str, object],
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if _ERROR_CODE_PATTERN.fullmatch(code) is None:
            raise ValueError(f"Invalid domain error code: {code!r}")
        normalized_params = {key: _json_value(value) for key, value in params.items()}
        self.code = code
        self.message = message
        self.params = normalized_params
        detail: dict[str, JsonValue] = {
            "code": self.code,
            "message": self.message,
            "params": self.params,
        }
        super().__init__(
            status_code=status_code,
            detail=detail,
            headers=dict(headers) if headers is not None else None,
        )


__all__ = ["DomainHTTPException", "JsonValue", "ScalarJsonValue"]
