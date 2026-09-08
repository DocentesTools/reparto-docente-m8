"""Typed assertions for Reparto-owned FastAPI exception details."""

from __future__ import annotations

from typing import cast

from fastapi import HTTPException


def domain_error_message(error: HTTPException) -> str:
    """Return the message from a Reparto structured error detail."""
    detail = cast(object, error.detail)
    assert isinstance(detail, dict)
    message = detail.get("message")
    assert isinstance(message, str)
    return message


__all__ = ["domain_error_message"]
