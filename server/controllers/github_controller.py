"""
github_controller.py — GitHub API interactions for Pipeline Autopsy.

Function organisation:
  1. Signature verification       — verify_github_signature
  2. Installation token           — _generate_app_jwt, get_installation_token
  3. Webhook URL builder          — build_webhook_url
  4. Webhook lifecycle            — register / update / delete / verify
  5. PR file filtering            — _is_relevant_yml, filter_relevant_files
  6. FQN extraction               — _extract_fqn_from_sql, _extract_fqn_from_yml,
                                    _strip_context_lines, derive_fqns
  7. PR diff parsing              — parse_pr_diff (unchanged)
  8. PR comment renderer          — render_pr_comment (new), render_placeholder_comment
  9. PR comment posting/updating  — post_pr_comment, update_pr_comment (unchanged)
"""

import os
import re
import hmac
import hashlib
import time
import requests
from typing import List, Optional, Dict, Tuple
from datetime import datetime, timezone
from dotenv import load_dotenv

from models.github import (
    PRWebhookEvent, ChangedAsset, PRAnalysis, PRRootCause,
    ChangedAssetSummary, DownstreamImpact
)
from models.base import SeverityLevel

load_dotenv()

GITHUB_APP_ID          = os.getenv("GITHUB_APP_ID", "")
GITHUB_APP_PRIVATE_KEY = os.getenv("GITHUB_APP_PRIVATE_KEY", "").replace("\\n", "\n")
GITHUB_WEBHOOK_SECRET  = os.getenv("GITHUB_WEBHOOK_SECRET", "")
GITHUB_TEST_PAT        = os.getenv("GITHUB_TEST_PAT", "")

# dbt project directories that always contain data-relevant yml files
_DBT_RELEVANT_DIRS = ("models/", "seeds/", "snapshots/", "analyses/", "macros/")

# Directories that never contain data-relevant yml files
_DBT_IRRELEVANT_DIRS = (".github/", "deploy/", "docker/", "docs/", ".circleci/", "infra/")

# dbt-specific top-level YAML keys used as content signal
_DBT_YML_KEYS = re.compile(r"^\+?(version|models|sources|seeds|snapshots|metrics|exposures)\s*:", re.MULTILINE)

# Large patch warning threshold (changed lines per file)
_LARGE_PATCH_WARN_THRESHOLD = 200


# ── 1. Signature verification ─────────────────────────────────────────────────

def verify_github_signature(signature: str, payload: bytes) -> bool:
    if not GITHUB_WEBHOOK_SECRET:
        print("WARNING: GITHUB_WEBHOOK_SECRET not set — skipping signature check")
        return True

    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    is_valid = hmac.compare_digest(signature, expected)
    if not is_valid:
        print("ERROR verify_github_signature: Invalid signature")
    return is_valid


# ── 2. Installation token ─────────────────────────────────────────────────────

def _generate_app_jwt() -> Optional[str]:
    if not GITHUB_APP_ID or not GITHUB_APP_PRIVATE_KEY:
        print("ERROR _generate_app_jwt: GITHUB_APP_ID or GITHUB_APP_PRIVATE_KEY not set")
        return None

    try:
        try:
            import jwt as pyjwt
            now     = int(time.time())
            payload = {"iat": now - 60, "exp": now + (9 * 60), "iss": GITHUB_APP_ID}
            return pyjwt.encode(payload, GITHUB_APP_PRIVATE_KEY, algorithm="RS256")
        except ImportError:
            pass

        from jose import jwt as jose_jwt
        now     = int(time.time())
        payload = {"iat": now - 60, "exp": now + (9 * 60), "iss": GITHUB_APP_ID}
        return jose_jwt.encode(payload, GITHUB_APP_PRIVATE_KEY, algorithm="RS256")

    except Exception as e:
        print(f"ERROR _generate_app_jwt: {e}")
        return None


def get_installation_token(installation_id: str) -> Optional[str]:
    if GITHUB_TEST_PAT:
        print("DEBUG get_installation_token: Using GITHUB_TEST_PAT (dev mode)")
        return GITHUB_TEST_PAT

    if not installation_id or str(installation_id) == "demo":
        print("ERROR get_installation_token: No valid installation_id and no GITHUB_TEST_PAT")
        return None

    installation_id = str(installation_id)
    app_jwt = _generate_app_jwt()
    if not app_jwt:
        return None

    try:
        url     = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept":        "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        resp = requests.post(url, headers=headers, timeout=15)

        if resp.status_code == 201:
            token = resp.json().get("token")
            print(f"DEBUG get_installation_token: Got installation token for {installation_id}")
            return token
        else:
            print(f"ERROR get_installation_token: GitHub returned {resp.status_code} — {resp.text}")
            return None

    except Exception as e:
        print(f"ERROR get_installation_token: {e}")
        return None


# ── 3. Webhook URL builder ────────────────────────────────────────────────────

def build_webhook_url(connection_id: str, user_id: str, api_base_url: str) -> str:
    api_base_url = api_base_url.rstrip("/")
    return f"{api_base_url}/api/v1/github/webhook?connection_id={connection_id}&user_id={user_id}"


# ── 4. Webhook lifecycle management ──────────────────────────────────────────

def register_github_webhook(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    webhook_url: str,
    webhook_secret: str
) -> Optional[dict]:
    if not github_token or not webhook_url or not repo_owner or not repo_name:
        print("ERROR register_github_webhook: Missing required parameters")
        return None

    try:
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/hooks"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        payload = {
            "name": "web",
            "active": True,
            "events": ["pull_request"],
            "config": {
                "url": webhook_url,
                "content_type": "json",
                "secret": webhook_secret,
                "insecure_ssl": "0",
            },
        }

        resp = requests.post(url, json=payload, headers=headers, timeout=15)

        if resp.status_code == 201:
            data = resp.json()
            webhook_id = data.get("id")
            print(f"DEBUG register_github_webhook: Registered webhook {webhook_id} on {repo_owner}/{repo_name}")
            return {
                "webhook_id": str(webhook_id),
                "url": data.get("config", {}).get("url"),
                "active": data.get("active"),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        else:
            print(f"ERROR register_github_webhook: GitHub returned {resp.status_code} — {resp.text}")
            return None

    except Exception as e:
        print(f"ERROR register_github_webhook: {e}")
        return None


def update_github_webhook(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    webhook_id: str,
    webhook_url: str,
    webhook_secret: str
) -> Optional[dict]:
    if not github_token or not webhook_url or not repo_owner or not repo_name or not webhook_id:
        print("ERROR update_github_webhook: Missing required parameters")
        return None

    try:
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/hooks/{webhook_id}"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        payload = {
            "active": True,
            "events": ["pull_request"],
            "config": {
                "url": webhook_url,
                "content_type": "json",
                "secret": webhook_secret,
                "insecure_ssl": "0",
            },
        }

        resp = requests.patch(url, json=payload, headers=headers, timeout=15)

        if resp.status_code == 200:
            data = resp.json()
            print(f"DEBUG update_github_webhook: Updated webhook {webhook_id}")
            return {
                "webhook_id": str(data.get("id")),
                "url": data.get("config", {}).get("url"),
                "active": data.get("active"),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        else:
            print(f"ERROR update_github_webhook: GitHub returned {resp.status_code} — {resp.text}")
            return None

    except Exception as e:
        print(f"ERROR update_github_webhook: {e}")
        return None


def delete_github_webhook(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    webhook_id: str
) -> bool:
    if not github_token or not repo_owner or not repo_name or not webhook_id:
        print("ERROR delete_github_webhook: Missing required parameters")
        return False

    try:
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/hooks/{webhook_id}"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        resp = requests.delete(url, headers=headers, timeout=15)

        if resp.status_code in (204, 404):
            print(f"DEBUG delete_github_webhook: Deleted webhook {webhook_id}")
            return True
        else:
            print(f"ERROR delete_github_webhook: GitHub returned {resp.status_code} — {resp.text}")
            return False

    except Exception as e:
        print(f"ERROR delete_github_webhook: {e}")
        return False


def verify_github_webhook(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    webhook_id: str
) -> Optional[dict]:
    if not github_token or not repo_owner or not repo_name or not webhook_id:
        print("ERROR verify_github_webhook: Missing required parameters")
        return None

    try:
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/hooks/{webhook_id}"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        resp = requests.get(url, headers=headers, timeout=15)

        if resp.status_code == 200:
            data = resp.json()
            print(f"DEBUG verify_github_webhook: Verified webhook {webhook_id}")
            return {
                "webhook_id": str(data.get("id")),
                "url": data.get("config", {}).get("url"),
                "active": data.get("active"),
                "deliveries_url": data.get("deliveries_url"),
            }
        else:
            print(f"WARNING verify_github_webhook: GitHub returned {resp.status_code}")
            return None

    except Exception as e:
        print(f"ERROR verify_github_webhook: {e}")
        return None


# ── 5. PR file filtering ──────────────────────────────────────────────────────

def _is_relevant_yml(filename: str, patch: Optional[str]) -> bool:
    """
    Determines whether a .yml/.yaml file is data-relevant (dbt schema/config).

    Decision order:
      1. Reject if path is in a known non-data directory → False
      2. Accept if path is in a known dbt directory → True
      3. Fall back to patch content scan for dbt-specific keys → bool
      4. If patch unavailable and path is ambiguous → False (conservative)
    """
    # Step 1 — reject known irrelevant directories
    for irrelevant in _DBT_IRRELEVANT_DIRS:
        if filename.startswith(irrelevant) or f"/{irrelevant.rstrip('/')}" in filename:
            print(f"DEBUG _is_relevant_yml: Rejected {filename} (irrelevant dir)")
            return False

    # Step 2 — accept known dbt directories immediately
    for relevant in _DBT_RELEVANT_DIRS:
        if filename.startswith(relevant) or f"/{relevant.rstrip('/')}" in filename:
            print(f"DEBUG _is_relevant_yml: Accepted {filename} (dbt dir)")
            return True

    # Step 3 — ambiguous path: scan patch content for dbt keys
    if patch:
        if _DBT_YML_KEYS.search(patch):
            print(f"DEBUG _is_relevant_yml: Accepted {filename} (dbt content keys found in patch)")
            return True
        else:
            print(f"DEBUG _is_relevant_yml: Rejected {filename} (no dbt keys in patch)")
            return False

    # Step 4 — no patch, ambiguous path: conservative reject
    print(f"DEBUG _is_relevant_yml: Rejected {filename} (ambiguous path, no patch to inspect)")
    return False


def filter_relevant_files(changed_assets: List[ChangedAsset]) -> List[ChangedAsset]:
    """
    Returns only the ChangedAssets that warrant lineage analysis:
      - All .sql files
      - .yml/.yaml files that pass _is_relevant_yml

    Preserves original order.
    """
    relevant = []
    for asset in changed_assets:
        filename = asset.filename
        if filename.endswith(".sql"):
            relevant.append(asset)
        elif filename.endswith((".yml", ".yaml")):
            if _is_relevant_yml(filename, asset.patch):
                relevant.append(asset)

    print(f"DEBUG filter_relevant_files: {len(relevant)}/{len(changed_assets)} files are data-relevant")
    return relevant


# ── 6. FQN extraction ─────────────────────────────────────────────────────────

def _strip_context_lines(patch: Optional[str]) -> str:
    """
    Removes unchanged context lines from a unified diff patch.
    Keeps only lines starting with + or - (excluding the +++ / --- file headers).
    Returns empty string if patch is None.
    """
    if not patch:
        return ""

    changed_lines = []
    for line in patch.splitlines():
        # Skip file headers (+++ and ---)
        if line.startswith("+++") or line.startswith("---"):
            continue
        # Keep actual additions and deletions
        if line.startswith("+") or line.startswith("-"):
            changed_lines.append(line)

    return "\n".join(changed_lines)


def _warn_large_patch(filename: str, stripped_patch: str) -> None:
    """Logs a warning if a file has an unusually large number of changed lines."""
    line_count = len(stripped_patch.splitlines())
    if line_count > _LARGE_PATCH_WARN_THRESHOLD:
        print(
            f"WARNING _warn_large_patch: {filename} has {line_count} changed lines "
            f"(threshold: {_LARGE_PATCH_WARN_THRESHOLD}). Sending all lines to AI."
        )


def _extract_fqn_from_sql(filename: str) -> str:
    """
    Derives a FQN from a .sql file path.

    models/finance/revenue.sql        → finance.revenue
    seeds/raw/users.sql               → raw.users
    snapshots/finance/snap_orders.sql → finance.snap_orders

    Strategy: drop the first path segment (dbt directory prefix) and extension,
    then join remaining segments with dots.
    """
    # Remove extension
    without_ext = filename.removesuffix(".sql")

    # Split path into segments
    parts = without_ext.replace("\\", "/").split("/")

    # Drop known dbt top-level directory prefix if present
    dbt_top_dirs = {"models", "seeds", "snapshots", "analyses", "macros"}
    if parts and parts[0] in dbt_top_dirs:
        parts = parts[1:]

    return ".".join(parts) if parts else without_ext


def _extract_fqn_from_yml(filename: str, patch: Optional[str]) -> Tuple[List[str], bool]:
    """
    Derives ALL FQNs defined in a .yml/.yaml file using a hybrid approach.

    Returns:
        (fqns, fqn_approximate)
        fqns            — list of FQNs, one per model/source name found in the patch.
                          Always at least one entry (fallback to path-based).
        fqn_approximate — True if patch parsing failed and we fell back to path only.

    Strategy:
      1. Extract domain from path (directory segment after dbt prefix)
      2. Scan stripped patch for ALL `- name: <model_name>` entries under
         models:/sources: blocks — a schema yml can define multiple models
      3. Combine each as `domain.model_name`
      4. If patch parse finds nothing → path-based fallback, mark as approximate
    """
    # Extract domain from path
    without_ext = filename.removesuffix(".yaml").removesuffix(".yml")
    parts = without_ext.replace("\\", "/").split("/")

    dbt_top_dirs = {"models", "seeds", "snapshots", "analyses", "macros"}
    if parts and parts[0] in dbt_top_dirs:
        parts = parts[1:]

    # Domain = directory segments only (everything except the filename itself)
    domain_parts = parts[:-1] if len(parts) > 1 else parts
    domain = ".".join(domain_parts) if domain_parts else ""

    # Attempt patch content scan — extract model/source names only (not column names)
    # Use FULL patch (not stripped) to find model definitions, since model lines
    # might be context (unchanged) and thus removed from the stripped patch
    if patch:
        lines = patch.split("\n")
        fqns: List[str] = []
        seen: set = set()
        
        for line in lines:
            # Skip file headers
            if line.startswith("+++") or line.startswith("---"):
                continue
            
            # Get the content (remove diff markers and leading/trailing whitespace)
            content = line
            if content.startswith("+") or content.startswith("-"):
                content = content[1:]
            
            # Count leading spaces to determine hierarchy level
            stripped_content = content.lstrip()
            leading_spaces = len(content) - len(stripped_content)
            
            # Match `- name: model_name` at model/source level (2-4 spaces typically)
            # NOT at column level (6+ spaces)
            if leading_spaces <= 4 and re.match(r"^-\s+name:\s+(\S+)", stripped_content):
                match = re.match(r"^-\s+name:\s+(\S+)", stripped_content)
                if match:
                    model_name = match.group(1)
                    fqn = f"{domain}.{model_name}" if domain else model_name
                    if fqn not in seen:
                        seen.add(fqn)
                        fqns.append(fqn)
        
        if fqns:
            print(
                f"DEBUG _extract_fqn_from_yml: Extracted {len(fqns)} FQN(s) "
                f"from patch for {filename}: {fqns}"
            )
            return fqns, False

    # Fallback — path-based approximation (single entry)
    file_stem = parts[-1] if parts else without_ext
    fqn = f"{domain}.{file_stem}" if domain else file_stem
    print(
        f"DEBUG _extract_fqn_from_yml: Approximate FQN [{fqn}] from path "
        f"for {filename} (no model names found in patch)"
    )
    return [fqn], True


def derive_fqns(
    relevant_assets: List[ChangedAsset]
) -> Dict[str, Tuple[str, bool]]:
    """
    Derives FQNs for all relevant changed files.

    For .sql files: one entry per file (filename → (fqn, False))
    For .yml files: one entry per model name found in the patch.
                    A yml defining 3 models produces 3 entries, each keyed
                    as `{filename}::{model_name}` to avoid key collisions.

    Returns:
        Dict mapping key → (fqn, fqn_approximate)

        Key format:
          .sql  → original filename     e.g. "models/finance/revenue.sql"
          .yml  → "filename::model_fqn" e.g. "models/finance/schema.yml::finance.orders"

        fqn_approximate=True for yml files where patch parsing failed (path fallback used).
    """
    result: Dict[str, Tuple[str, bool]] = {}

    for asset in relevant_assets:
        filename = asset.filename
        stripped = _strip_context_lines(asset.patch)
        _warn_large_patch(filename, stripped)

        if filename.endswith(".sql"):
            fqn = _extract_fqn_from_sql(filename)
            result[filename] = (fqn, False)

        elif filename.endswith((".yml", ".yaml")):
            fqns, approximate = _extract_fqn_from_yml(filename, asset.patch)

            if len(fqns) == 1:
                # Single model — use filename as key directly for clean display
                result[filename] = (fqns[0], approximate)
            else:
                # Multiple models — suffix key with model FQN to avoid collisions
                for fqn in fqns:
                    composite_key = f"{filename}::{fqn}"
                    result[composite_key] = (fqn, approximate)

    print(f"DEBUG derive_fqns: Derived {len(result)} FQN entries from {len(relevant_assets)} files")
    return result


# ── 7. PR diff parsing ────────────────────────────────────────────────────────

def parse_pr_diff(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    pr_number: int
) -> List[ChangedAsset]:
    """
    Fetches all changed files for a PR and returns as ChangedAsset list.
    Filtering is done separately via filter_relevant_files.
    """
    try:
        url     = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/files"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept":        "application/vnd.github.v3+json",
        }
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code != 200:
            print(f"ERROR parse_pr_diff: Status {response.status_code}")
            return []

        all_assets = []
        for file_info in response.json():
            all_assets.append(ChangedAsset(
                filename=file_info.get("filename", ""),
                status=file_info.get("status", "modified"),
                additions=file_info.get("additions", 0),
                deletions=file_info.get("deletions", 0),
                changes=file_info.get("changes", 0),
                patch=file_info.get("patch", ""),
            ))

        print(f"DEBUG parse_pr_diff: Fetched {len(all_assets)} total changed files")
        return all_assets

    except Exception as e:
        print(f"ERROR parse_pr_diff: {e}")
        return []


# ── 8. PR comment renderer ────────────────────────────────────────────────────

_SEVERITY_EMOJI = {
    SeverityLevel.CRITICAL: "🔴",
    SeverityLevel.HIGH:     "🟠",
    SeverityLevel.MEDIUM:   "🟡",
    SeverityLevel.LOW:      "🟢",
}


def render_placeholder_comment(
    relevant_files: List[ChangedAsset],
    investigation_id: str
) -> str:
    """
    Initial comment posted immediately when the PR webhook fires.
    Updated later by render_pr_comment once analysis is complete.
    """
    file_lines = "\n".join(
        f"- `{f.filename}` ({f.status}, +{f.additions}/-{f.deletions})"
        for f in relevant_files
    )
    return (
        f"## 🔍 Pipeline Autopsy — Analysis Started\n\n"
        f"Detected **{len(relevant_files)} data file(s)** changed:\n"
        f"{file_lines}\n\n"
        f"Running lineage impact analysis... *(investigation `{investigation_id}`)*"
    )


def render_pr_comment(
    pr_root_cause: PRRootCause,
    investigation_id: str
) -> str:
    """
    Renders the full PR comment from a completed PRRootCause analysis.

    Structure:
      Header          — summary, severity, safe-to-merge verdict
      What Changed    — table of all changed assets with patch evidence
      Downstream      — per-asset blocks with per-cause errors and fixes
      Footer          — confidence, investigation ID
    """
    lines: List[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    severity_emoji = _SEVERITY_EMOJI.get(pr_root_cause.overall_severity, "⚪")
    merge_verdict  = "✅ Safe to merge" if pr_root_cause.safe_to_merge else "❌ Do NOT merge"

    lines += [
        f"## {severity_emoji} Pipeline Autopsy — PR Analysis",
        "",
        f"**{pr_root_cause.pr_summary}**",
        "",
        f"| | |",
        f"|---|---|",
        f"| Severity | {severity_emoji} {pr_root_cause.overall_severity.value.upper()} |",
        f"| Verdict | {merge_verdict} |",
        f"| Assets changed | {len(pr_root_cause.changed_assets)} |",
        f"| Assets impacted | {pr_root_cause.impact_count} |",
        f"| Confidence | {pr_root_cause.confidence:.0%} |",
        "",
    ]

    # ── What Changed ──────────────────────────────────────────────────────────
    lines += ["---", "", "### 📝 What Changed", ""]
    lines += ["| Asset | Change | Evidence |", "|---|---|---|"]

    for asset in pr_root_cause.changed_assets:
        approx_flag = " *(approx)*" if asset.fqn_approximate else ""
        # Escape pipe characters in patch evidence for markdown table safety
        evidence = asset.patch_evidence.replace("|", "\\|").replace("\n", " · ")
        lines.append(
            f"| `{asset.fqn}`{approx_flag} | {asset.change_type} — {asset.change_description} | `{evidence}` |"
        )

    lines.append("")

    # ── Downstream Breakage ───────────────────────────────────────────────────
    if pr_root_cause.downstream_impacts:
        lines += ["---", "", "### 💥 Downstream Breakage", ""]

        for impact in pr_root_cause.downstream_impacts:
            sev_emoji = _SEVERITY_EMOJI.get(impact.severity, "⚪")
            lines += [
                f"#### {sev_emoji} `{impact.fqn}` — {impact.display_name} ({impact.severity.value.upper()})",
                "",
            ]

            for i, cause in enumerate(impact.causes, 1):
                lines += [
                    f"**Cause {i} — from `{cause.source_asset_fqn}`**",
                    "",
                    f"- **Error type:** `{cause.error_type}`",
                    f"- **What's broken:** {cause.error_description}",
                    f"- **Where:** `{cause.error_location.file}` "
                    f"· {cause.error_location.clause} clause"
                    + (f" · ~line {cause.error_location.approximate_line}" if cause.error_location.approximate_line else ""),
                    "",
                    f"**Fix ({cause.fix.fix_type}):** {cause.fix.description}",
                    f"*File to edit:* `{cause.fix.target_file}`",
                ]
                if cause.fix.code_snippet:
                    lines += [
                        "```sql",
                        cause.fix.code_snippet,
                        "```",
                    ]
                lines.append("")

    else:
        lines += [
            "---", "",
            "### ✅ No Downstream Breakage Detected",
            "",
            "No downstream assets are impacted by these changes.",
            "",
        ]

    # ── Footer ────────────────────────────────────────────────────────────────
    lines += [
        "---",
        "",
        f"*Investigation `{investigation_id}` · Confidence {pr_root_cause.confidence:.0%} · Powered by Pipeline Autopsy*",
    ]

    return "\n".join(lines)


# ── 9. PR comment posting / updating ──────────────────────────────────────────

def post_pr_comment(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    comment_body: str
) -> Optional[str]:
    try:
        url     = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{pr_number}/comments"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept":        "application/vnd.github.v3+json",
        }
        response = requests.post(url, json={"body": comment_body}, headers=headers, timeout=30)

        if response.status_code == 201:
            comment_id = response.json().get("id")
            print(f"DEBUG post_pr_comment: Posted comment {comment_id}")
            return str(comment_id)
        else:
            print(f"ERROR post_pr_comment: Status {response.status_code}")
            return None

    except Exception as e:
        print(f"ERROR post_pr_comment: {e}")
        return None


def update_pr_comment(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    comment_id: str,
    comment_body: str
) -> bool:
    try:
        url     = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/comments/{comment_id}"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept":        "application/vnd.github.v3+json",
        }
        response = requests.patch(url, json={"body": comment_body}, headers=headers, timeout=30)

        if response.status_code == 200:
            print(f"DEBUG update_pr_comment: Updated comment {comment_id}")
            return True
        else:
            print(f"ERROR update_pr_comment: Status {response.status_code}")
            return False

    except Exception as e:
        print(f"ERROR update_pr_comment: {e}")
        return False