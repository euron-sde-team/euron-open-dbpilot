"""People and the organizations (tenants) they belong to.

An organization owns projects; every connection, query, backup and audit entry is reached
through a project. Org owners and admins act as owners of every project in the org;
members see only the projects they are added to.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from dbpilot.core.db import Base, TimestampMixin, new_id

ORG_ROLES = ("member", "admin", "owner")
USER_ROLES = ("user", "admin")


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        # Sign-in looks people up case-insensitively; one account per address, any case.
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
        CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # "admin" is an instance administrator (created by ``dbpilot create-admin``).
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bumped on password change; sessions carrying an older value stop working.
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class OrgMember(TimestampMixin, Base):
    __tablename__ = "org_members"
    __table_args__ = (
        CheckConstraint("role IN ('member', 'admin', 'owner')", name="ck_org_members_role"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
