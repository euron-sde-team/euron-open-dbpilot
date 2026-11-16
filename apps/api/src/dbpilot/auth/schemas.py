from __future__ import annotations

from pydantic import Field

from dbpilot.core.schemas import ApiModel, Email
from dbpilot.orgs.schemas import UserOut


class LoginRequest(ApiModel):
    email: Email
    password: str = Field(min_length=1, max_length=200)


class SignInOut(ApiModel):
    user: UserOut
    session_token: str = Field(description="Send as Authorization: Bearer <token>")
    expires_in_s: int


class SessionOut(ApiModel):
    user: UserOut | None


class PasswordChange(ApiModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)
