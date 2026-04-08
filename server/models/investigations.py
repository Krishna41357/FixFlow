"""
investigations.py — Investigation and RootCause schemas for Pipeline Autopsy.

An Investigation is the central document — it ties together:
  - the FailureEvent that triggered it
  - the LineageSubgraph the engine traversed
  - the RootCause the AI produced
  - the current processing status

This is what the chat UI displays and what gets posted to GitHub PRs.
"""

from typing import Optional, List
from pydantic import BaseModel, Field, computed_field

from .base import MongoBase, InvestigationStatus, SeverityLevel, utc_now
from .lineage import LineageSubgraph
from .events import AffectedAsset


# ── Root cause (AI reasoning output) ─────────────────────────────────────────

class SuggestedFix(BaseModel):
    """One concrete actionable fix."""
    description: str = Field(..., max_length=500)
    fix_type: str = Field(
        ...,
        description="rename_column | update_ref | revert_change | add_test | contact_owner"
    )
    target_asset_fqn: Optional[str] = None
    code_snippet: Optional[str] = None   # SQL or YAML example when relevant


class RootCause(BaseModel):
    """
    Structured output from the AI reasoning layer.
    Parsed from the LLM response — we prompt for JSON so we can
    populate each field reliably rather than parsing free text.
    """
    one_line_summary: str = Field(
        ..., max_length=200,
        description="Single sentence. E.g. 'Column user_id renamed to customer_id in raw.users broke 3 downstream models.'"
    )
    detailed_explanation: str = Field(
        ..., max_length=2000,
        description="Full explanation of what changed, when, and why it cascaded"
    )
    break_point_fqn: str = Field(
        ..., description="FQN of the asset where the change originated"
    )
    break_point_change: str = Field(
        ..., description="Human-readable description of the exact change"
    )
    affected_assets: List[AffectedAsset] = Field(default_factory=list)
    suggested_fixes: List[SuggestedFix] = Field(default_factory=list)
    owner_to_contact: Optional[str] = Field(
        None, description="Email of the person who owns the break-point asset"
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="How confident the AI is — based on completeness of lineage data"
    )

    @computed_field
    @property
    def affected_count(self) -> int:
        return len(self.affected_assets)

    @computed_field
    @property
    def has_critical_impact(self) -> bool:
        return any(a.severity == SeverityLevel.CRITICAL for a in self.affected_assets)


# ── Investigation (master document) ──────────────────────────────────────────

class InvestigationInDB(MongoBase):
    """
    Master Investigation document stored in MongoDB.
    Created immediately when an event is received (status=pending),
    then updated as the pipeline progresses.
    """
    # Links
    event_id: str
    connection_id: str
    user_id: Optional[str] = None   # None for webhook-triggered

    # What failed
    failing_asset_fqn: str
    failure_message: str
    event_type: str

    # Processing state
    status: InvestigationStatus = InvestigationStatus.PENDING
    error_message: Optional[str] = None   # populated if status=failed

    # Results (populated as pipeline completes each stage)
    lineage_subgraph: Optional[LineageSubgraph] = None
    root_cause: Optional[RootCause] = None

    # GitHub-specific (only for github_pr events)
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    pr_comment_id: Optional[int] = None   # GitHub comment ID after bot posts

    # Timing
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    completed_at: Optional[str] = None
    processing_time_ms: Optional[int] = None


class InvestigationResponse(BaseModel):
    """
    Full investigation returned to the chat UI.
    This is the main payload the frontend renders.
    """
    id: str
    event_id: str
    failing_asset_fqn: str
    failure_message: str
    event_type: str
    status: InvestigationStatus

    # Only present when status=completed
    root_cause: Optional[RootCause] = None
    lineage_subgraph: Optional[LineageSubgraph] = None

    # GitHub
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None

    created_at: str
    completed_at: Optional[str] = None
    processing_time_ms: Optional[int] = None

    @computed_field
    @property
    def is_complete(self) -> bool:
        return self.status == InvestigationStatus.COMPLETED

    @computed_field
    @property
    def summary(self) -> Optional[str]:
        """One-line summary for list views — None until complete."""
        if self.root_cause:
            return self.root_cause.one_line_summary
        return None


class InvestigationListItem(BaseModel):
    """Compact investigation for sidebar/list view — no heavy subgraph."""
    id: str
    failing_asset_fqn: str
    event_type: str
    status: InvestigationStatus
    summary: Optional[str] = None
    affected_count: int = 0
    has_critical_impact: bool = False
    created_at: str
    processing_time_ms: Optional[int] = None