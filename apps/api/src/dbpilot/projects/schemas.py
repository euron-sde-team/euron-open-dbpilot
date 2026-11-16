from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from dbpilot.core.schemas import ApiModel, Email

ProjectRole = Literal["viewer", "admin", "owner"]


class ProjectCreate(ApiModel):
    org_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=2000)


class ProjectUpdate(ApiModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=2000)


class ProjectOut(ApiModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    slug: str
    description: str
    role: ProjectRole = Field(description="Your role in this project")
    connections: int = Field(0, description="Connections you can see")
    members: int = 0
    created_at: datetime


class MemberAdd(ApiModel):
    """A person already in the project's organization."""

    email: Email
    role: ProjectRole = "viewer"


class MemberUpdate(ApiModel):
    role: ProjectRole


class MemberOut(ApiModel):
    user_id: uuid.UUID
    email: str
    name: str
    role: ProjectRole
    joined_at: datetime
