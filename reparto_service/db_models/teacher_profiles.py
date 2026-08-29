"""TeacherProfile table model and request/response schemas.

Represents a teacher inside the local docentes domain. The profile is
intentionally minimal: a display name, an optional link to an auth user
(``user_id``) and operational flags. Personal data like DNI, address or
phone numbers is intentionally NOT stored (plan 8.5, 19).

The linkage is established by the *teacher*, not by a lookup: a department
head mints a single-use claim code and the teacher redeems it with their own
token (remediation `W1.4`). That is why the claim-code columns are on the
table and on none of the schemas below — the accounts directory belongs to
``fa-auth-m8`` and is superuser-only by its own design, so nothing here may
be built around discovering a colleague's user id.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi_m8 import TimestampMixin
from pydantic import Field
from sqlalchemy import DateTime, String
from sqlmodel import Column, Field as SQLField, SQLModel

from reparto_service.core.db_models import UUIDString, prefixed_tables


# ── Base, Create, Update schemas ──────────────────────────────────────────────


class TeacherProfileBase(SQLModel):
    """Shared fields for teacher profile schemas."""

    display_name: str = Field(
        min_length=1,
        max_length=150,
        description="Display name shown to other users.",
    )
    user_id: Optional[uuid.UUID] = Field(
        default=None,
        description=(
            "Optional link to the auth-service user id. Unset until the "
            "department head binds the profile to a real account."
        ),
    )
    active: bool = Field(
        default=True,
        description="Whether the profile is still active in the department.",
    )
    notes: Optional[str] = Field(default=None, description="Free-form notes.")


class TeacherProfileCreate(TeacherProfileBase):
    """Schema for creating a new teacher profile."""


class TeacherProfileUpdate(SQLModel):
    """Partial update schema — every field is optional."""

    display_name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    user_id: Optional[uuid.UUID] = Field(default=None)
    active: Optional[bool] = Field(default=None)
    notes: Optional[str] = Field(default=None)


# ── Database model ───────────────────────────────────────────────────────────


class TeacherProfileLinkUser(SQLModel):
    """Request schema for binding a teacher profile to an auth user."""

    user_id: uuid.UUID = Field(description="Auth-service user id to bind.")


class TeacherProfileClaim(SQLModel):
    """Request schema for redeeming a claim code (remediation `W1.4`).

    Deliberately carries no ``user_id``: the account a claim binds is the
    caller's own, read from the token. There is no payload a teacher could
    build that points the profile at somebody else.
    """

    claim_code: str = Field(
        min_length=1,
        max_length=64,
        description="The single-use code the department head issued.",
    )


class TeacherProfileClaimCode(SQLModel):
    """Response schema for a freshly minted claim code.

    Returned **once**, by the mint endpoint only. The code is stored hashed, so
    neither this service nor a later read can produce it again: a lost code is
    reissued, never recovered.
    """

    teacher_profile_id: uuid.UUID = Field(description="Profile the code claims.")
    display_name: str = Field(description="Display name of that profile.")
    claim_code: str = Field(description="The code itself — shown once.")
    expires_at: datetime = Field(description="When the code stops being redeemable.")


class TeacherProfile(TimestampMixin, TeacherProfileBase, SQLModel, table=True):
    """SQLModel table for a teacher profile."""

    __tablename__ = prefixed_tables("teacher_profile")

    id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column("id", UUIDString(), primary_key=True),
        description="Teacher profile ID.",
    )
    user_id: Optional[uuid.UUID] = SQLField(
        default=None,
        sa_column=Column("user_id", UUIDString(), nullable=True, index=True),
        description="Optional link to the auth-service user id.",
    )
    #: SHA-256 of the normalised claim code, never the code (remediation
    #: `W1.4`). Absent from every schema above, so no read path can serve it:
    #: the column exists only to be compared against a hash the claimant
    #: presents. Cleared on redemption, which is what makes a code single-use.
    claim_code_hash: Optional[str] = SQLField(
        default=None,
        sa_column=Column(
            "claim_code_hash", String(64), nullable=True, unique=True, index=True
        ),
        description="SHA-256 hash of the outstanding claim code, if any.",
    )
    claim_code_expires_at: Optional[datetime] = SQLField(
        default=None,
        sa_column=Column(
            "claim_code_expires_at", DateTime(timezone=True), nullable=True
        ),
        description="When the outstanding claim code stops being redeemable.",
    )


# ── Public/read schemas ──────────────────────────────────────────────────────


class TeacherProfilePublic(TeacherProfileBase, SQLModel):
    """Public representation of a teacher profile."""

    id: uuid.UUID = Field(description="Teacher profile ID.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last update timestamp (UTC).")


class TeacherProfilesPublic(SQLModel):
    """List wrapper for public teacher profiles."""

    data: list[TeacherProfilePublic] = Field(description="List of teacher profiles.")
    count: int = Field(description="Total teacher profiles count.")
