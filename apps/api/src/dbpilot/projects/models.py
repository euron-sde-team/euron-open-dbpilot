"""Projects: one workspace per team. Every connection, query, backup and audit entry is
scoped to a project; members hold a project role (viewer < admin < owner)."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from dbpilot.core.db import Base, TimestampMixin, new_id

PROJECT_ROLES = ("viewer", "admin", "owner")


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_projects_org_slug"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class ProjectMember(TimestampMixin, Base):
    __tablename__ = "project_members"
    __table_args__ = (
        CheckConstraint("role IN ('viewer', 'admin', 'owner')", name="ck_project_members_role"),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
