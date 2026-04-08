"""
events.py — Failure event schemas for Pipeline Autopsy.

A FailureEvent is created whenever something triggers an investigation:
  - dbt webhook fires (test failure)
  - GitHub PR opened (schema change detected)
  - User submits a manual query via chat

All three normalise into the same FailureEvent before being queued.
"""

from typing import Optional, List, Any, Dict
from pydantic import BaseModel, Field, field_validator

from .base import MongoBase, EventType, SeverityLevel, utc_now


# ── Raw payloads (what arrives at our endpoints) ──────────────────────────────

class DbtTestFailure(BaseModel):
    """
    A single failed test node inside a dbt webhook payload.
    Based on dbt Cloud's job.run.completed event schema.
    """
    node_id: str = Field(..., description="e.g. model.project.orders_daily")
    status: str
    failures: int = Field(..., ge=0)
    message: str
    compiled_sql: Optional[str] = None


class DbtWebhookPayload(BaseModel):
    """
    Raw payload from dbt Cloud webhook.
    We validate only the fields we actually use.
    """
    event: str = Field(..., description="e.g. job.run.completed")
    run_status: str
    job_name: str
    run_id: str
    project_name: Optional[str] = None
    run_results: List[DbtTestFailure] = Field(default_factory=list)

    @field_validator("run_results")
    @classmethod
    def at_least_one_failure(cls, v: List[DbtTestFailure]) -> List[DbtTestFailure]:
        failures = [r for r in v if r.status in ("fail", "error")]
        if not failures:
            raise ValueError("Webhook payload contains no failed or errored test results")
        return v


class GitHubPRPayload(BaseModel):
    """
    Parsed fields from GitHub's pull_request webhook event.
    Our handler extracts these from the raw GitHub payload.
    """
    action: str                     # "opened", "synchronize", "reopened"
    pr_number: int
    pr_title: str
    pr_url: str
    repo_full_name: str             # e.g. "acme-corp/data-warehouse"
    base_branch: str
    head_branch: str
    author: str
    changed_files: List[str] = Field(default_factory=list)
    installation_id: int


class ManualQueryPayload(BaseModel):
    """What the user types in the chat UI."""
    asset_name: str = Field(
        ..., min_length=1, max_length=300,
        description="Fully qualified name or free-text asset reference"
    )
    question: Optional[str] = Field(
        None, max_length=500,
        description="Optional context — what the user wants to know"
    )
    connection_id: str = Field(..., description="Which workspace to query against")


# ── Normalised FailureEvent (stored in DB, queued for processing) ─────────────

class AffectedAsset(BaseModel):
    """
    A data asset known to be impacted by the root cause.
    Populated during lineage traversal.
    """
    fqn: str                        # fully qualified name from OpenMetadata
    asset_type: str                 # table, view, dashboard, pipeline
    display_name: str
    severity: SeverityLevel
    owner_email: Optional[str] = None
    owner_team: Optional[str] = None
    description: Optional[str] = None


class FailureEventCreate(BaseModel):
    """
    Payload to manually create a FailureEvent (used internally by our
    webhook handlers and the manual query route after parsing raw payloads).
    """
    event_type: EventType
    connection_id: str
    failing_asset_fqn: str = Field(
        ..., description="Fully qualified name of the broken asset"
    )
    failure_message: str = Field(
        ..., max_length=2000,
        description="Raw error text from dbt, GitHub diff summary, or user query"
    )
    source_metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Original raw payload fields for audit trail"
    )
    # GitHub-specific (only populated for github_pr events)
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    repo_full_name: Optional[str] = None


class FailureEventInDB(MongoBase):
    """FailureEvent document stored in MongoDB."""
    event_type: EventType
    connection_id: str
    user_id: Optional[str] = None   # None for webhook-triggered events
    failing_asset_fqn: str
    failure_message: str
    source_metadata: Dict[str, Any] = Field(default_factory=dict)
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    repo_full_name: Optional[str] = None
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    # Set once investigation is created
    investigation_id: Optional[str] = None


class FailureEventResponse(BaseModel):
    """FailureEvent as returned to the frontend."""
    id: str
    event_type: EventType
    failing_asset_fqn: str
    failure_message: str
    created_at: str
    investigation_id: Optional[str] = None