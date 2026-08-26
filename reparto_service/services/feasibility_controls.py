"""Denial-of-service controls for full assignment-feasibility solves.

The bounded solver already limits one evaluation by instance size, search steps
and wall-clock time.  This module adds the missing process-level admission
control: at most one full solve for a process may run at once, and additional
requests fail fast instead of forming an unbounded queue.

PostgreSQL uses a transaction advisory lock so the guarantee spans API workers
and releases atomically on commit or rollback.
SQLite and other standalone/test engines use a process-local lock with the same
non-blocking contract.  Neither path locks assignment or planning rows while the
solver runs.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlmodel import Session

logger = logging.getLogger(__name__)

_LOCK_NAMESPACE = b"reparto:feasibility-solve:v1:"
_local_locks_guard = threading.Lock()
_local_locks: dict[uuid.UUID, threading.Lock] = {}


def _advisory_lock_key(process_id: uuid.UUID) -> int:
    """Return a stable signed 64-bit PostgreSQL advisory-lock key."""

    digest = hashlib.sha256(_LOCK_NAMESPACE + process_id.bytes).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _solve_in_progress() -> HTTPException:
    """Build the fail-fast response used when a process solve is already active."""

    logger.warning("feasibility_solve_rejected reason=solve_in_progress")
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(
            "A feasibility evaluation is already running for this process; "
            "retry after it completes."
        ),
        headers={"Retry-After": "1"},
    )


@contextmanager
def _local_solve_slot(process_id: uuid.UUID) -> Iterator[None]:
    """Acquire one non-blocking process-local solve slot."""

    with _local_locks_guard:
        lock = _local_locks.setdefault(process_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise _solve_in_progress()
    try:
        yield
    finally:
        with _local_locks_guard:
            _local_locks.pop(process_id, None)
            lock.release()


@contextmanager
def serialize_feasibility_solve(
    session: Session, process_id: uuid.UUID
) -> Iterator[None]:
    """Admit one full solve for ``process_id`` without waiting or row locks."""

    connection = session.connection()
    if connection.dialect.name != "postgresql":
        with _local_solve_slot(process_id):
            yield
        return

    lock_key = _advisory_lock_key(process_id)
    acquired = bool(
        connection.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        ).scalar_one()
    )
    if not acquired:
        raise _solve_in_progress()
    yield


__all__ = ["serialize_feasibility_solve"]
