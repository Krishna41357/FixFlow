"""
github.py — GitHub PR bot schemas + OAuth user registration for Pipeline Autopsy.

Schema organisation:
  1. PR diff parsing         — ChangedAsset (unchanged)
  2. PR AI response schemas  — NEW: ErrorLocation, CauseFix, AssetCause,
                               DownstreamImpact, ChangedAssetSummary, PRRootCause
  3. Legacy PRAnalysis       — kept for backwards compat, not used by new PR bot
  4. GitHub OAuth / App      — GitHubOAuthProfile, GitHubInstallation,
                               GitHubAppRegistration (all unchanged)
  5. Request / Response      — GitHubWebhookConfigRequest,
                               GitHubRegistrationStatusResponse (unchanged)

Reuse policy (no duplication):
  - SuggestedFix   → imported from models.investigations
  - AffectedAsset  → imported from models.events
  - SeverityLevel  → imported from models.base
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from .base import SeverityLevel
from .investigations import RootCause, SuggestedFix   # reused, not redefined
from .events import AffectedAsset                      # reused, not redefined


# ── 0. GitHub Webhook Event (PRWebhookEvent) ────────────────────────────────────

class PRWebhookEvent(BaseModel):
    """
    GitHub pull_request webhook event structure.
    Contains parsed payload from GitHub's pull_request webhook.
    """
    action: str                           # opened | synchronize | closed | etc.
    installation: Dict[str, Any] = {}     # {"id": 12345, ...} optional
    repository: Dict[str, Any]            # {"name": ..., "full_name": ..., "owner": ...}
    pull_request: Dict[str, Any]          # {"number": ..., "title": ..., ...}


# ── 1. PR diff parsing ────────────────────────────────────────────────────────

class ChangedAsset(BaseModel):
    """
    One file changed in a PR, as returned by GitHub's
    GET /repos/{owner}/{repo}/pulls/{pr}/files endpoint.
    """
    filename: str
    status: str                         # added | modified | removed | renamed
    additions: int
    deletions: int
    changes: int
    patch: Optional[str] = None         # raw unified diff; None for binary files


# ── 2. PR AI response schemas (new) ──────────────────────────────────────────

class ErrorLocation(BaseModel):
    """
    Where in the codebase the downstream breakage manifests.
    Populated by the AI from patch evidence and lineage context.
    approximate_line is best-effort — not required if AI cannot determine it.
    """
    file: str = Field(..., description="Relative path to the file that needs fixing")
    clause: str = Field(
        ...,
        description="SQL/YAML clause where error occurs: SELECT | JOIN | WHERE | FROM | source | ref"
    )
    approximate_line: Optional[int] = Field(
        None,
        description="Best-effort line number. Omitted if AI cannot determine it."
    )


class CauseFix(BaseModel):
    """
    One concrete fix for one specific cause of breakage.
    Mirrors SuggestedFix from investigations.py but scoped to a
    specific downstream file — kept separate to avoid polluting
    the manual investigation schema with PR-specific fields.
    """
    description: str = Field(..., max_length=500)
    fix_type: str = Field(
        ...,
        description="update_sql_ref | add_cast | rename_column | revert_change | update_source_yaml | contact_owner"
    )
    target_file: str = Field(
        ..., description="Relative path to the file that needs the fix applied"
    )
    code_snippet: Optional[str] = Field(
        None, description="Ready-to-apply SQL or YAML snippet"
    )


class AssetCause(BaseModel):
    """
    One reason why a downstream asset is broken, traced back to
    one specific changed asset in the PR.

    A single downstream asset can have multiple AssetCauses if
    multiple PR files each contribute a separate breakage.
    """
    source_asset_fqn: str = Field(
        ..., description="FQN of the PR-changed asset that caused this break"
    )
    error_type: str = Field(
        ...,
        description="missing_column | type_mismatch | renamed_column | dropped_source | ref_not_found"
    )
    error_description: str = Field(
        ..., max_length=500,
        description="Human-readable explanation of exactly what is broken and why"
    )
    error_location: ErrorLocation
    fix: CauseFix


class DownstreamImpact(BaseModel):
    """
    One downstream asset that will break if the PR is merged.
    Contains ALL causes (one per upstream PR file that affects it)
    so the PR author sees the complete picture in one block.
    """
    fqn: str
    display_name: str
    severity: SeverityLevel
    causes: List[AssetCause] = Field(
        default_factory=list,
        description="One entry per upstream changed asset that breaks this downstream asset"
    )

    @property
    def affected_by(self) -> List[str]:
        """FQNs of all upstream assets that cause this breakage."""
        return [c.source_asset_fqn for c in self.causes]


class ChangedAssetSummary(BaseModel):
    """
    Summary of one PR file after filtering and FQN extraction.
    Carried into the AI prompt and the PR comment header table.
    """
    fqn: str = Field(..., description="Derived fully qualified name")
    filename: str = Field(..., description="Original file path from GitHub")
    change_type: str = Field(
        ...,
        description="column_added | column_dropped | column_type_changed | source_renamed | model_renamed | schema_change | sql_logic_change"
    )
    change_description: str = Field(
        ..., max_length=300,
        description="Human-readable one-liner of what changed"
    )
    patch_evidence: str = Field(
        ..., description="Relevant +/- lines from the diff that evidence the change"
    )
    fqn_approximate: bool = Field(
        False,
        description="True if FQN was derived from path only (yml patch parse failed)"
    )


class PRRootCause(BaseModel):
    """
    Full AI analysis result for one PR.
    One instance per PR — covers all changed files and all downstream impacts.

    This is what gets stored on the Investigation and rendered
    into the GitHub PR comment. It does NOT replace RootCause
    (which is used by the manual investigation flow).
    """
    pr_summary: str = Field(
        ..., max_length=300,
        description="One sentence covering what changed and how many assets are affected"
    )
    overall_severity: SeverityLevel
    safe_to_merge: bool
    confidence: float = Field(..., ge=0.0, le=1.0)

    # What the PR actually changed (one entry per relevant file)
    changed_assets: List[ChangedAssetSummary] = Field(default_factory=list)

    # What will break downstream (deduplicated by FQN, causes annotated)
    downstream_impacts: List[DownstreamImpact] = Field(default_factory=list)

    @property
    def impact_count(self) -> int:
        return len(self.downstream_impacts)

    @property
    def has_critical_impact(self) -> bool:
        return any(i.severity == SeverityLevel.CRITICAL for i in self.downstream_impacts)

    @property
    def all_affected_fqns(self) -> List[str]:
        return [i.fqn for i in self.downstream_impacts]


# ── 3. Legacy PRAnalysis (kept for backwards compat) ─────────────────────────

class PRAnalysis(BaseModel):
    """
    Legacy PR analysis schema — kept so existing stored documents
    can still be deserialised. New PR bot uses PRRootCause instead.
    """
    pr_number: int
    pr_url: str
    repo_full_name: str
    changed_assets: List[ChangedAsset]
    impacted_assets: List[AffectedAsset] = Field(default_factory=list)
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
                lines.append(
                    f"- **{asset.display_name}** ({asset.asset_type}) "
                    f"- {asset.severity.value} - {owner} - {asset.failure_reason}"
                )
            lines.append("")
        if self.suggested_fixes:
            lines += ["### Suggested fixes", ""]
            for i, fix in enumerate(self.suggested_fixes, 1):
                lines.append(f"{i}. {fix}")
            lines.append("")
        lines.append(f"*Confidence: {self.confidence:.0%} - Powered by Pipeline Autopsy*")
        return "\n".join(lines)


class PRAnalysisInDB(BaseModel):
    """Legacy — kept for existing stored documents."""
    investigation_id: str
    pr_analysis: PRAnalysis
    github_comment_id: Optional[int] = None
    posted_at: Optional[str] = None


# ── 4. GitHub OAuth / App registration models (unchanged) ────────────────────

class GitHubOAuthProfile(BaseModel):
    github_id: int
    github_login: str
    github_name: Optional[str] = None
    github_email: Optional[str] = None
    github_avatar_url: Optional[str] = None
    github_html_url: Optional[str] = None


class GitHubInstallation(BaseModel):
    installation_id: str
    account_login: str
    account_type: str                        # "Organization" | "User"
    account_avatar_url: Optional[str] = None
    app_slug: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    webhook_id: Optional[str] = None
    webhook_configured: bool = False
    repositories: List[str] = Field(default_factory=list)


class GitHubAppRegistration(BaseModel):
    oauth_profile: GitHubOAuthProfile
    installations: List[GitHubInstallation] = Field(default_factory=list)
    selected_installation_id: Optional[str] = None
    registered_at: str
    last_synced_at: Optional[str] = None


# ── 5. Request / Response schemas (unchanged) ─────────────────────────────────

class GitHubWebhookConfigRequest(BaseModel):
    connection_id: str
    installation_id: str
    webhook_url: str
    webhook_secret: Optional[str] = None


class GitHubRegistrationStatusResponse(BaseModel):
    oauth_connected: bool = False
    github_login: Optional[str] = None
    github_avatar_url: Optional[str] = None
    installations: List[GitHubInstallation] = Field(default_factory=list)
    selected_installation_id: Optional[str] = None
    webhook_configured: bool = False
    webhook_url: Optional[str] = None