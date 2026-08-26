"""Admission-control coverage for full feasibility solves."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import cast

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from reparto_service.services.feasibility_controls import (
    _advisory_lock_key,
    serialize_feasibility_solve,
)


@dataclass
class _Dialect:
    name: str = "postgresql"


@dataclass
class _ScalarResult:
    value: bool

    def scalar_one(self) -> bool:
        return self.value


@dataclass
class _Connection:
    results: list[bool]
    dialect: _Dialect = field(default_factory=_Dialect)
    statements: list[str] = field(default_factory=list)

    def execute(self, statement: object, parameters: dict[str, int]) -> _ScalarResult:
        self.statements.append(str(statement))
        assert isinstance(parameters["lock_key"], int)
        return _ScalarResult(self.results.pop(0))


@dataclass
class _Session:
    database_connection: _Connection

    def connection(self) -> _Connection:
        return self.database_connection


def _postgres_session(*results: bool) -> tuple[Session, _Connection]:
    connection = _Connection(list(results))
    return cast(Session, _Session(connection)), connection


def test_advisory_key_is_stable_signed_and_process_specific() -> None:
    first = uuid.uuid4()
    first_key = _advisory_lock_key(first)
    assert first_key == _advisory_lock_key(first)
    assert -(2**63) <= first_key < 2**63
    assert first_key != _advisory_lock_key(uuid.uuid4())


def test_local_solve_slots_fail_fast_per_process(session: Session) -> None:
    process_id = uuid.uuid4()
    with serialize_feasibility_solve(session, process_id):
        with (
            pytest.raises(HTTPException) as exc_info,
            serialize_feasibility_solve(session, process_id),
        ):
            raise AssertionError("a duplicate process solve must not start")
        assert exc_info.value.status_code == 429
        assert exc_info.value.headers == {"Retry-After": "1"}
        with serialize_feasibility_solve(session, uuid.uuid4()):
            pass
    with serialize_feasibility_solve(session, process_id):
        pass


def test_postgres_advisory_slot_acquires_transaction_lock() -> None:
    session, connection = _postgres_session(True)
    with serialize_feasibility_solve(session, uuid.uuid4()):
        pass
    assert connection.statements == ["SELECT pg_try_advisory_xact_lock(:lock_key)"]


def test_postgres_advisory_slot_rejects_without_unlock() -> None:
    session, connection = _postgres_session(False)
    with (
        pytest.raises(HTTPException) as exc_info,
        serialize_feasibility_solve(session, uuid.uuid4()),
    ):
        raise AssertionError("an unacquired advisory slot must not start")
    assert exc_info.value.status_code == 429
    assert len(connection.statements) == 1


def test_postgres_transaction_slot_relies_on_rollback_after_failure() -> None:
    session, connection = _postgres_session(True)
    with (
        pytest.raises(RuntimeError, match="solver failed"),
        serialize_feasibility_solve(session, uuid.uuid4()),
    ):
        raise RuntimeError("solver failed")
    assert connection.statements == ["SELECT pg_try_advisory_xact_lock(:lock_key)"]
