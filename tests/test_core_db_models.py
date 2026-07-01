"""Tests for ``reparto_service.core.db_models`` helpers."""

from __future__ import annotations

import uuid as _uuid

from reparto_service.core.db_models import UUIDString, prefixed_tables


def test_prefixed_tables_prepends_settings_prefix():
    from reparto_service.core.config import settings

    assert prefixed_tables("foo") == f"{settings.TABLES_PREFIX}_foo"


def test_prefixed_tables_uses_explicit_name():
    """The helper concatenates the prefix + the explicit name verbatim."""
    assert prefixed_tables("process_teacher") == "reparto_process_teacher"


def test_uuidstring_bind_param_none_returns_none():
    col = UUIDString()
    assert col.process_bind_param(None, None) is None


def test_uuidstring_bind_param_uuid_returns_string():
    col = UUIDString()
    uid = _uuid.uuid4()
    assert col.process_bind_param(uid, None) == str(uid)


def test_uuidstring_bind_param_string_returns_string():
    col = UUIDString()
    raw = "11111111-1111-1111-1111-111111111111"
    assert col.process_bind_param(raw, None) == raw


def test_uuidstring_result_value_none_returns_none():
    col = UUIDString()
    assert col.process_result_value(None, None) is None


def test_uuidstring_result_value_string_returns_uuid():
    col = UUIDString()
    uid = _uuid.uuid4()
    result = col.process_result_value(str(uid), None)
    assert result == uid
    assert isinstance(result, _uuid.UUID)
