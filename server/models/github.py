"""
github.py — GitHub PR bot schemas for Pipeline Autopsy.

When a PR is opened on a connected repo, we:
  1. Parse the changed files to extract affected assets
  2. Run lineage + AI analysis
  3. Post a structured comment back to the PR

This file defines the data shapes for that entire flow.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

from .base import SeverityLevel
from .investigations import RootCause


# ── Incoming webhook ──────────────────────────────────────────────────────────

class ChangedAsset(BaseModel):
    """
    A data asset whose definition changed in the PR.
    Extracted by parsing the diff of SQL/YAML files.
    """
    fqn: str
    file_path: str                  # path in the repo, e.g. models/orders.sql
    change_summary: str             # "column user_id renamed to customer_id"
    raw_diff_snippet: Optional[str] = Field(
        None, max_length=1000,
        description="Relevant lines from the diff for context"
    )


class PRWebhookEvent(BaseModel):
    """
    Normalised GitHub PR event, after our handler parses the raw payload.
    Stored as source_metadata on the FailureEvent.
    """
    installation_id: int
    repo_full_name: str
    pr_number: int
    pr_title: str
    pr_url: str
    author: str
    base_branch: str
    head_sha: str
    changed_assets: List[ChangedAsset] = Field(default_factory=list)

    @field_validator("changed_assets")
    @classmethod
    def must_have_data_changes(cls, v: List[ChangedAsset]) -> List[ChangedAsset]:
        if not v:
            raise ValueError(
                "PR contains no changed data assets — "
                "should be filtered before reaching this schema"
            )
        return v


# ── PR analysis result ────────────────────────────────────────────────────────

class ImpactedAsset(BaseModel):
    """
    A downstream asset that will break if this PR is merged as-is.
    Shown in the GitHub comment table.
    """
    fqn: str
    display_name: str
    asset_type: str
    severity: SeverityLevel
    owner_email: Optional[str] = None
    failure_reason: str = Field(
        ..., description="E.g. 'References user_id which is being renamed'"
    )


class PRAnalysis(BaseModel):
    """
    Full analysis result for one PR.
    Used to render the GitHub comment and stored with the Investigation.
    """
    pr_number: int
    pr_url: str
    repo_full_name: str
    changed_assets: List[ChangedAsset]
    impacted_assets: List[ImpactedAsset] = Field(default_factory=list)
    root_cause_summary: Optional[str] = None
    suggested_fixes: List[str] = Field(default_factory=list)
    is_safe_to_merge: bool = Field(
        False,
        description="True only when no downstream assets are impacted"
    )
    confidence: float = Field(..., ge=0.0, le=1.0)

    def render_github_comment(self) -> str:
        """
        Renders the markdown comment body posted to the PR.
        Called by the GitHub bot handler after analysis completes.
        """
        status_line = (
            "No downstream impact detected. Safe to merge."
            if self.is_safe_to_merge
            else f"{len(self.impacted_assets)} downstream asset(s) will break if merged."
        )

        lines = [
            "## Pipeline Autopsy — impact analysis",
            "",
            f"**Status:** {status_line}",
            "",
        ]

        if self.root_cause_summary:
            lines += [f"**Summary:** {self.root_cause_summary}", ""]

        if self.impacted_assets:
            lines += [
                "### Impacted assets",
                "",
                "| Asset | Type | Severity | Owner | Reason |",
                "|-------|------|----------|-------|--------|",
            ]
            for asset in self.impacted_assets:
                owner = asset.owner_email or "—"
                lines.append(
                    f"| `{asset.display_name}` "
                    f"| {asset.asset_type} "
                    f"| **{asset.severity.value}** "
                    f"| {owner} "
                    f"| {asset.failure_reason} |"
                )
            lines.append("")

        if self.suggested_fixes:
            lines += ["### Suggested fixes", ""]
            for i, fix in enumerate(self.suggested_fixes, 1):
                lines.append(f"{i}. {fix}")
            lines.append("")

        lines.append(
            f"*Confidence: {self.confidence:.0%} · "
            f"Powered by [Pipeline Autopsy](https://github.com/your-repo)*"
        )

        return "\n".join(lines)


class PRAnalysisInDB(BaseModel):
    """PRAnalysis stored alongside the Investigation document."""
    investigation_id: str
    pr_analysis: PRAnalysis
    github_comment_id: Optional[int] = None   # set after posting
    posted_at: Optional[str] = None