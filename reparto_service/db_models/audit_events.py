"""Audit event table and public schemas.

Audit rows capture the domain mutation trail required by plan section 8.14:
who changed which process entity, the before/after payloads, and an optional
reason for overrides or lifecycle decisions.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import Field
from sqlalchemy import JSON
from sqlmodel import Column, Field as SQLField, SQLModel

from auth_sdk_m8.models.shared import TimestampMixin
from reparto_service.core.db_models import UUIDString, prefixed_tables


class AuditEventBase(SQLModel):
    """Shared audit-event fields."""

    assignment_process_id: uuid.UUID = Field(description="Owning process ID.")
    actor_user_id: uuid.UUID = Field(description="Auth user who made the change.")
    actor_role: str = Field(description="Role claim observed during the change.")
    event_type: str = Field(description="Domain event type.")
    entity_type: str = Field(description="Mutated entity type.")
    entity_id: Optional[uuid.UUID] = Field(default=None)
    before_json: Optional[dict[str, Any]] = Field(default=None)
    after_json: Optional[dict[str, Any]] = Field(default=None)
    reason: Optional[str] = Field(default=None, max_length=500)


class AuditEvent(TimestampMixin, AuditEventBase, SQLModel, table=True):
    """SQLModel table for persisted domain audit events."""

    __tablename__ = prefixed_tables("audit_event")

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Audit event ID.",
    )
    assignment_process_id: uuid.UUID = SQLField(
        sa_column=Column(
            "assignment_process_id", UUIDString(), nullable=False, index=True
        ),
        description="Owning process ID.",
    )
    actor_user_id: uuid.UUID = SQLField(
        sa_column=Column("actor_user_id", UUIDString(), nullable=False, index=True),
        description="Auth user who made the change.",
    )
    entity_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column("entity_id", UUIDString(), nullable=True, index=True),
    )
    before_json: Optional[dict[str, Any]] = SQLField(
        default=None, sa_column=Column("before_json", JSON, nullable=True)
    )
    after_json: Optional[dict[str, Any]] = SQLField(
        default=None, sa_column=Column("after_json", JSON, nullable=True)
    )


class AuditEventPublic(AuditEventBase, SQLModel):
    """Public representation of an audit event."""

    id: uuid.UUID = Field(description="Audit event ID.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class AuditEventsPublic(SQLModel):
    """List wrapper for audit events."""

    data: list[AuditEventPublic] = Field(description="List of audit events.")
    count: int = Field(description="Total audit-event count.")
