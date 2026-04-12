"""
users.py — User auth and workspace connection schemas for Pipeline Autopsy.

A user has:
  - credentials (email/password → JWT)
  - one or more Connection records (OpenMetadata + dbt + GitHub per workspace)
"""

from typing import Optional, List, Literal
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator, ConfigDict

from .base import MongoBase, PyObjectId, utc_now


# ── Connection (per-workspace credentials) ────────────────────────────────────

class ConnectionCreate(BaseModel):
    """
    What the user submits on the onboarding page.
    All three fields together form one 'workspace'.
    """
    name: str = Field(..., min_length=1, max_length=80,
                      description="Human label, e.g. 'Prod workspace'")
    openmetadata_host: str = Field(
        ..., description="Base URL, e.g. https://my-org.openmetadata.org"
    )
    openmetadata_token: str = Field(
        ..., min_length=10,
        description="JWT bot token from OpenMetadata Settings → Bots"
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

    @field_validator("openmetadata_host")
    @classmethod
    def host_must_be_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("openmetadata_host must start with http:// or https://")
        return v.rstrip("/")   # strip trailing slash so we can safely append paths


class ConnectionResponse(BaseModel):
    """
    Connection as returned to the frontend.
    Token is masked — never returned after creation.
    """
    id: str
    name: str
    openmetadata_host: str
    github_repo: Optional[str] = None
    # dbt_webhook_secret deliberately omitted
    # openmetadata_token deliberately omitted
    is_active: bool
    created_at: str   # ISO string for JSON


class ConnectionInDB(MongoBase):
    """Connection document stored in MongoDB."""
    user_id: str
    name: str
    openmetadata_host: str
    openmetadata_token: str       # store encrypted in production
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