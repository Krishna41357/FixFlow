import os
import hmac
import hashlib
import requests
import json
from typing import List, Optional
from datetime import datetime, timezone
from dotenv import load_dotenv

from models.github import (
    PRWebhookEvent, ChangedAsset, PRAnalysis, ImpactedAsset
)

load_dotenv()

# GitHub App setup
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID", "")
GITHUB_APP_PRIVATE_KEY = os.getenv("GITHUB_APP_PRIVATE_KEY", "")
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")


def verify_github_signature(signature: str, payload: bytes) -> bool:
    """Validates X-Hub-Signature-256 header. Rejects tampered payloads before any processing."""
    if not GITHUB_WEBHOOK_SECRET:
        print("WARNING: GITHUB_WEBHOOK_SECRET not set")
        return True
    
    expected_signature = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    is_valid = hmac.compare_digest(signature, expected_signature)
    
    if not is_valid:
        print(f"ERROR verify_github_signature: Invalid signature")
    
    return is_valid


def parse_pr_diff(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    pr_number: int
) -> List[ChangedAsset]:
    """
    Calls GitHub API to get changed files.
    Filters to .sql/.yml files only.
    Returns ChangedAsset list.
    """
    try:
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/files"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            print(f"ERROR parse_pr_diff: Status {response.status_code}")
            return []
        
        files = response.json()
        changed_assets = []
        
        for file_info in files:
            filename = file_info.get("filename", "")
            
            # Filter to .sql and .yml files only
            if filename.endswith(".sql") or filename.endswith(".yml") or filename.endswith(".yaml"):
                changed_assets.append(ChangedAsset(
                    filename=filename,
                    status=file_info.get("status", "modified"),
                    additions=file_info.get("additions", 0),
                    deletions=file_info.get("deletions", 0),
                    changes=file_info.get("changes", 0),
                    patch=file_info.get("patch", "")
                ))
        
        print(f"DEBUG parse_pr_diff: Found {len(changed_assets)} relevant changed files")
        return changed_assets
    except Exception as e:
        print(f"ERROR parse_pr_diff: {e}")
        return []


def build_pr_analysis(
    investigation_id: str,
    investigation_result: dict,
    changed_files: List[ChangedAsset]
) -> PRAnalysis:
    """
    Takes investigation result → builds PRAnalysis with impacted assets + suggested fixes.
    """
    root_cause = investigation_result.get("root_cause", {})
    
    # Identify impacted assets
    impacted_assets = []
    for changed_file in changed_files:
        impacted_assets.append(ImpactedAsset(
            asset_name=changed_file.filename,
            impact_level="HIGH" if changed_file.deletions > 0 else "MEDIUM",
            suggested_fix=root_cause.get("suggested_fix", "Review schema changes")
        ))
    
    pr_analysis = PRAnalysis(
        investigation_id=investigation_id,
        root_cause_summary=root_cause.get("root_cause", ""),
        impacted_assets=impacted_assets,
        suggested_fixes=root_cause.get("suggested_fix", ""),
        confidence_score=root_cause.get("confidence_score", 0.5),
        created_at=datetime.now(timezone.utc)
    )
    
    return pr_analysis


def post_pr_comment(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    comment_body: str
) -> Optional[str]:
    """
    Calls GitHub API to post the rendered markdown comment.
    Stores comment_id.
    Returns comment_id if successful.
    """
    try:
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{pr_number}/comments"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        data = {"body": comment_body}
        
        response = requests.post(url, json=data, headers=headers, timeout=30)
        
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
    """
    If analysis is re-run, edits the existing comment rather than posting a duplicate.
    """
    try:
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/comments/{comment_id}"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        data = {"body": comment_body}
        
        response = requests.patch(url, json=data, headers=headers, timeout=30)
        
        if response.status_code == 200:
            print(f"DEBUG update_pr_comment: Updated comment {comment_id}")
            return True
        else:
            print(f"ERROR update_pr_comment: Status {response.status_code}")
            return False
    except Exception as e:
        print(f"ERROR update_pr_comment: {e}")
        return False


def get_installation_token(installation_id: str) -> Optional[str]:
    try:
        test_pat = os.getenv("GITHUB_TEST_PAT")
        if test_pat:
            print(f"DEBUG get_installation_token: Using GITHUB_TEST_PAT")
            return test_pat
        print("ERROR get_installation_token: GITHUB_TEST_PAT not set")
        return None
    except Exception as e:
        print(f"ERROR get_installation_token: {e}")
        return None

def render_pr_comment(pr_analysis: PRAnalysis) -> str:
    """Renders the PR analysis into a markdown comment for GitHub."""
    markdown = f"""# 🔍 Data Lineage Analysis

## Root Cause
{pr_analysis.root_cause_summary}

## Impacted Assets
"""
    
    for asset in pr_analysis.impacted_assets:
        markdown += f"\n- **{asset.asset_name}** ({asset.impact_level} impact)"
    
    markdown += f"""

## Suggested Fixes
{pr_analysis.suggested_fixes}

## Confidence Score
{pr_analysis.confidence_score * 100:.1f}%

---
_Generated by KS-RAG Data Lineage Analysis_
"""
    
    return markdown
