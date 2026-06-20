"""
users.py — User auth and workspace connection schemas for Pipeline Autopsy.

A user has:
  - credentials (email/password → JWT)
  - one or more Connection records (GitHub repo per workspace)
"""

from typing import Optional, List, Literal
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator, ConfigDict

from .base import MongoBase, PyObjectId, utc_now


# ── Connection (per-workspace credentials) ────────────────────────────────────

class ConnectionCreate(BaseModel):
    """
    What the user submits on the onboarding page.
    A workspace needs a name and a GitHub repo.
    OpenMetadata fields are kept for backward compatibility but are optional.
    """
    name: str = Field(..., min_length=1, max_length=80,
                      description="Human label, e.g. 'Prod workspace'")
    openmetadata_host: str = Field(
        "", description="(Legacy, optional) Base URL of OpenMetadata instance"
    )
    openmetadata_token: str = Field(
        "", description="(Legacy, optional) OpenMetadata API token"
    )
    dbt_webhook_secret: Optional[str] = Field(
        None,
        description="Shared secret to validate incoming dbt webhook payloads"
    )
    github_repo: Optional[str] = Field(
        None,
        pattern=r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$",
        description="Repo slug, e.g. acme-corp/data-warehouse"
    )


class ConnectionResponse(BaseModel):
    """
    Connection as returned to the frontend.
    Token is masked — never returned after creation.
    """
    id: str
    name: str
    openmetadata_host: str = ""
    github_repo: Optional[str] = None
    # dbt_webhook_secret deliberately omitted
    # openmetadata_token deliberately omitted
    is_active: bool
    created_at: str   # ISO string for JSON


class ConnectionInDB(MongoBase):
    """Connection document stored in MongoDB."""
    user_id: str
    name: str
    openmetadata_host: str = ""     # legacy, kept for backward compat
    openmetadata_token: str = ""    # legacy, kept for backward compat
    dbt_webhook_secret: Optional[str] = None
    github_repo: Optional[str] = None
    github_installation_id: Optional[int] = None  # set after GitHub App install
    is_active: bool = True
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())


# ── User ─────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    """Registration payload."""
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=50)
    full_name: Optional[str] = Field(None, max_length=100)
    password: str = Field(..., min_length=8)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        return v


class UserLogin(BaseModel):
    """Login payload."""
    email: EmailStr
    password: str


class UserInDB(MongoBase):
    """User document stored in MongoDB."""
    email: str
    username: str
    full_name: Optional[str] = None
    hashed_password: str
    is_active: bool = True
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    # connection IDs — actual Connection docs stored separately
    connection_ids: List[str] = Field(default_factory=list)


class UserResponse(BaseModel):
    """Safe user payload — no password, no tokens."""
    id: str
    email: str
    username: str
    is_active: bool
    created_at: str
    connection_count: int = 0

    model_config = ConfigDict(populate_by_name=True)


# ── Auth tokens ───────────────────────────────────────────────────────────────

class Token(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class TokenData(BaseModel):
    """Decoded JWT payload. Both fields required — empty token is invalid."""
    user_id: str
    email: str