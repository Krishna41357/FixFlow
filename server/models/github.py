"""
github.py - GitHub PR bot schemas for Pipeline Autopsy.
"""

from typing import List, Optional
from pydantic import BaseModel, Field
from .base import SeverityLevel
from .investigations import RootCause


class ChangedAsset(BaseModel):
    filename: str
    status: str
    additions: int
    deletions: int
    changes: int
    patch: Optional[str] = None


class GitHubUser(BaseModel):
    login: str
    id: int


class GitHubRepo(BaseModel):
    name: str
    full_name: str
    owner: GitHubUser


class GitHubPullRequest(BaseModel):
    number: int
    title: str
    html_url: str
    user: GitHubUser
    base: dict
    head: dict


class PRWebhookEvent(BaseModel):
    action: str
    installation: Optional[dict] = None
    repository: GitHubRepo
    pull_request: GitHubPullRequest
    sender: Optional[GitHubUser] = None


class ImpactedAsset(BaseModel):
    fqn: str
    display_name: str
    asset_type: str
    severity: SeverityLevel
    owner_email: Optional[str] = None
    failure_reason: str


class PRAnalysis(BaseModel):
    pr_number: int
    pr_url: str
    repo_full_name: str
    changed_assets: List[ChangedAsset]
    impacted_assets: List[ImpactedAsset] = Field(default_factory=list)
    root_cause_summary: Optional[str] = None
    suggested_fixes: List[str] = Field(default_factory=list)
    is_safe_to_merge: bool = False
    confidence: float = Field(..., ge=0.0, le=1.0)

    def render_github_comment(self) -> str:
        status_line = (
            "No downstream impact detected. Safe to merge."
            if self.is_safe_to_merge
            else f"{len(self.impacted_assets)} downstream asset(s) will break if merged."
        )
        lines = ["## Pipeline Autopsy - impact analysis", "", f"**Status:** {status_line}", ""]
        if self.root_cause_summary:
            lines += [f"**Summary:** {self.root_cause_summary}", ""]
        if self.impacted_assets:
            lines += ["### Impacted assets", ""]
            for asset in self.impacted_assets:
                owner = asset.owner_email or "n/a"
                lines.append(f"- **{asset.display_name}** ({asset.asset_type}) - {asset.severity.value} - {owner} - {asset.failure_reason}")
            lines.append("")
        if self.suggested_fixes:
            lines += ["### Suggested fixes", ""]
            for i, fix in enumerate(self.suggested_fixes, 1):
                lines.append(f"{i}. {fix}")
            lines.append("")
        lines.append(f"*Confidence: {self.confidence:.0%} - Powered by Pipeline Autopsy*")
        return "\n".join(lines)


class PRAnalysisInDB(BaseModel):
    investigation_id: str
    pr_analysis: PRAnalysis
    github_comment_id: Optional[int] = None
    posted_at: Optional[str] = None
