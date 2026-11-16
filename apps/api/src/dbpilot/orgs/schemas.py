from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from dbpilot.core.schemas import ApiModel, Email

OrgRole = Literal["member", "admin", "owner"]


class UserOut(ApiModel):
    id: uuid.UUID
    email: str
    name: str
    role: Literal["user", "admin"] = Field(description="admin is an instance administrator")
    created_at: datetime


class OrgCreate(ApiModel):
    name: str = Field(min_length=1, max_length=120)


class OrgUpdate(ApiModel):
    name: str = Field(min_length=1, max_length=120)


class OrgOut(ApiModel):
    id: uuid.UUID
    name: str
    slug: str
    role: OrgRole = "member"
    projects: int = 0
    members: int = 0
    created_at: datetime


class OrgMemberAdd(ApiModel):
    """Add a person by email. A new account needs a name and a temporary password."""

    email: Email
    role: OrgRole = "member"
    name: str | None = Field(None, min_length=1, max_length=200)
    password: str | None = Field(None, min_length=10, max_length=200)


class OrgMemberUpdate(ApiModel):
    role: OrgRole


class OrgMemberOut(ApiModel):
    user_id: uuid.UUID
    email: str
    name: str
    role: OrgRole
    joined_at: datetime
