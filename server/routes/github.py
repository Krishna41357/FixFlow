"""
GitHub PR Routes - Fixed
"""

import json
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Header, Request
from typing import Optional

from models.github import PRWebhookEvent
from controllers import github_controller, investigation_controller, connection_controller
from routes.auth import get_current_user
from models.users import TokenData

# helper functions
def run_investigation_and_update_pr(
    investigation_id, user_id, connection_id,
    openmetadata_url, openmetadata_token,
    gh_token, repo_owner, repo_name, pr_number, comment_id
):
    investigation_controller.run_investigation(
        investigation_id=investigation_id,
        user_id=user_id,
        connection_id=connection_id,
        openmetadata_url=openmetadata_url,
        openmetadata_token=openmetadata_token
    )
    # Fetch completed investigation
    inv = investigation_controller.get_investigation(investigation_id, user_id)
    if not inv:
        return
    root_cause = inv.root_cause or {}
    summary = getattr(root_cause, "one_line_summary", None) or "Analysis complete"
    explanation = getattr(root_cause, "detailed_explanation", None) or ""
    confidence = getattr(root_cause, "confidence", 0) or 0
    fixes = getattr(root_cause, "suggested_fixes", []) or []
    affected = getattr(root_cause, "affected_assets", []) or []

    fix_text = "\n".join(f"- {getattr(f, 'description', '')}" for f in fixes) if fixes else "No fixes suggested"
    affected_text = "\n".join(f"- `{getattr(a, 'fqn', '')}` ({getattr(a, 'severity', '')})" for a in affected) if affected else "None detected"
    updated_comment = f"""## 🔍 Pipeline Autopsy - Analysis Complete

### Root Cause
{summary}

{explanation}

### Affected Assets
{affected_text}

### Suggested Fixes
{fix_text}

### Confidence: {confidence*100:.0f}%

---
*Investigation `{investigation_id}` completed by Pipeline Autopsy*"""

    github_controller.update_pr_comment(
        github_token=gh_token,
        repo_owner=repo_owner,
        repo_name=repo_name,
        comment_id=comment_id,
        comment_body=updated_comment
    )

router = APIRouter(prefix="/github", tags=["github"])


@router.post("/webhook", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
async def github_pr_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(None),
    x_github_event: str = Header(None),
    connection_id: str = None,
    user_id: str = None
) -> dict:
    if not connection_id or not user_id:
        raise HTTPException(status_code=400, detail="connection_id and user_id are required")

    raw_body = await request.body()

    if not x_hub_signature_256:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")

    if not github_controller.verify_github_signature(x_hub_signature_256, raw_body):
        raise HTTPException(status_code=401, detail="Invalid GitHub signature")

    try:
        body = json.loads(raw_body)
        payload = PRWebhookEvent(**body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid payload: {str(e)}")

    if x_github_event != "pull_request" or payload.action not in ("opened", "synchronize"):
        return {"message": f"Ignoring event: {x_github_event}/{payload.action}"}

    connection = connection_controller.get_connection_by_id(
        connection_id=connection_id,
        user_id=user_id
    )
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    installation_id = (
        getattr(connection, "github_installation_id", None)
        or (str(payload.installation["id"]) if payload.installation else None)
    )
    if not installation_id:
        raise HTTPException(status_code=400, detail="No GitHub installation ID found.")

    gh_token = github_controller.get_installation_token(installation_id)
    if not gh_token:
        raise HTTPException(status_code=401, detail="Failed to get GitHub App token.")

    pr_number = payload.pull_request.number
    pr_url = payload.pull_request.html_url
    repo_owner = payload.repository.owner.login
    repo_name = payload.repository.name

    changed_files = github_controller.parse_pr_diff(
        github_token=gh_token,
        repo_owner=repo_owner,
        repo_name=repo_name,
        pr_number=pr_number
    )

    if not changed_files:
        return {"pr_number": pr_number, "analyzed": False, "message": "No .sql or .yml files changed"}

    # Convert filename to dot-notation FQN for OpenMetadata lookup
    primary_file = changed_files[0].filename
    primary_asset = primary_file.replace("/", ".").rstrip(".sql").rstrip(".yml").rstrip(".yaml")

    investigation_id = investigation_controller.create_investigation(
        user_id=user_id,
        connection_id=connection_id,
        event_id=f"github-{pr_number}",
        failure_message=f"GitHub PR #{pr_number} ({pr_url}): Schema change detected in {primary_file}",
        asset_fqn=primary_asset
    )
    if not investigation_id:
        raise HTTPException(status_code=500, detail="Failed to create investigation")

    # Post initial comment FIRST so comment_id exists
    initial_comment = (
        f"## Pipeline Autopsy - analysis started\n\n"
        f"Detected **{len(changed_files)} data file(s)** changed:\n"
        + "\n".join(f"- `{f.filename}` ({f.status}, +{f.additions}/-{f.deletions})" for f in changed_files)
        + f"\n\nRunning lineage impact analysis... (investigation `{investigation_id}`)"
    )

    comment_id = github_controller.post_pr_comment(
        github_token=gh_token,
        repo_owner=repo_owner,
        repo_name=repo_name,
        pr_number=pr_number,
        comment_body=initial_comment
    ) or "0"

    # Now start background task with comment_id available
    background_tasks.add_task(
        run_investigation_and_update_pr,
        investigation_id=investigation_id,
        user_id=user_id,
        connection_id=connection_id,
        openmetadata_url=connection.openmetadata_host,
        openmetadata_token=connection.openmetadata_token,
        gh_token=gh_token,
        repo_owner=repo_owner,
        repo_name=repo_name,
        pr_number=pr_number,
        comment_id=comment_id
    )

    return {
        "pr_number": pr_number,
        "analyzed": True,
        "investigation_id": investigation_id,
        "changed_files": len(changed_files),
        "comment_id": comment_id,
        "message": "Analysis started. Comment posted to PR."
    }


@router.post("/authorize", response_model=dict)
async def github_authorize(
    connection_id: str,
    installation_id: str,
    current_user: TokenData = Depends(get_current_user)
) -> dict:
    success = connection_controller.set_github_installation_id(
        connection_id=connection_id,
        user_id=current_user.user_id,
        installation_id=installation_id
    )
    if not success:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"connection_id": connection_id, "github_installation_id": installation_id, "message": "GitHub App authorized successfully"}


@router.get("/pr-analysis/{pr_number}", response_model=dict)
async def get_pr_analysis(
    pr_number: int,
    connection_id: str,
    current_user: TokenData = Depends(get_current_user)
) -> dict:
    return {"pr_number": pr_number, "message": "PR analysis feature coming soon"}
