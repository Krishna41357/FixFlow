"""
GitHub Routes - PR Webhooks + OAuth App Registration
"""

import os
import json
import base64
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status, Header, Request
from fastapi.responses import RedirectResponse

from models.github import (
    PRWebhookEvent,
    GitHubOAuthProfile,
    GitHubInstallation,
    GitHubAppRegistration,
    GitHubWebhookConfigRequest,
    GitHubRegistrationStatusResponse,
)
from controllers import github_controller, investigation_controller, connection_controller, auth_controller
from routes.auth import get_current_user
from models.users import TokenData

router = APIRouter(prefix="/github", tags=["github"])

GITHUB_CLIENT_ID      = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET  = os.getenv("GITHUB_CLIENT_SECRET")
GITHUB_REDIRECT_URI = os.getenv("GITHUB_REDIRECT_URI", "http://localhost:8000/api/v1/github/oauth/callback")
FRONTEND_SUCCESS_URL  = os.getenv("FRONTEND_SUCCESS_URL", "http://localhost:3000/github/setup")
FRONTEND_ERROR_URL    = os.getenv("FRONTEND_ERROR_URL",   "http://localhost:3000/error")
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")

GITHUB_AUTHORIZE_URL     = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL         = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL          = "https://api.github.com/user"
GITHUB_INSTALLATIONS_URL = "https://api.github.com/user/installations"
GITHUB_REPOS_URL         = "https://api.github.com/user/installations/{installation_id}/repositories"


# ── PR webhook background task ────────────────────────────────────────────────

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
    inv = investigation_controller.get_investigation(investigation_id, user_id)
    if not inv:
        return

    root_cause  = inv.root_cause
    if root_cause is None:
        return

    summary     = root_cause.one_line_summary
    explanation = root_cause.detailed_explanation
    confidence  = root_cause.confidence
    fixes       = root_cause.suggested_fixes
    affected    = root_cause.affected_assets

    fix_text      = "\n".join(f"- {f.description}" for f in fixes)    if fixes    else "No fixes suggested"
    affected_text = "\n".join(f"- `{a.fqn}` ({a.severity})" for a in affected) if affected else "None detected"

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


# ── OAuth helpers ─────────────────────────────────────────────────────────────

def _encode_state(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()


def _decode_state(state: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(state).decode())


async def _exchange_code(code: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id":     GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code":          code,
                "redirect_uri":  GITHUB_REDIRECT_URI,
            },
        )
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise HTTPException(400, f"Token exchange failed: {data.get('error_description', data)}")
    return token


async def _gh_get(url: str, token: str):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    if resp.status_code != 200:
        raise HTTPException(502, f"GitHub API error ({url}): {resp.text}")
    return resp.json()


async def _fetch_profile(token: str) -> GitHubOAuthProfile:
    data = await _gh_get(GITHUB_USER_URL, token)
    return GitHubOAuthProfile(
        github_id=data["id"],
        github_login=data["login"],
        github_name=data.get("name"),
        github_email=data.get("email"),
        github_avatar_url=data.get("avatar_url"),
        github_html_url=data.get("html_url"),
    )


async def _fetch_installations(token: str) -> list[GitHubInstallation]:
    data = await _gh_get(GITHUB_INSTALLATIONS_URL, token)
    print(f"DEBUG _fetch_installations: raw response = {data}")  # ← add this
    result = []
    for inst in data.get("installations", []):
        account = inst.get("account", {})
        repos: list[str] = []
        try:
            repo_url  = GITHUB_REPOS_URL.format(installation_id=inst["id"])
            repo_data = await _gh_get(repo_url, token)
            repos = [r["full_name"] for r in repo_data.get("repositories", [])]
        except Exception:
            pass
        result.append(GitHubInstallation(
            installation_id=str(inst["id"]),
            account_login=account.get("login", ""),
            account_type=account.get("type", "User"),
            account_avatar_url=account.get("avatar_url"),
            app_slug=inst.get("app_slug"),
            repositories=repos,
        ))
    print(f"DEBUG _fetch_installations: found {len(result)} installations")  # ← add this
    return result


def _get_registration(connection) -> Optional[GitHubAppRegistration]:
    if isinstance(connection, dict):
        raw = connection.get("github_registration")
    else:
        raw = getattr(connection, "github_registration", None)
    if not raw:
        return None
    if isinstance(raw, dict):
        return GitHubAppRegistration(**raw)
    return raw


def _save_registration(connection_id: str, user_id: str, reg: GitHubAppRegistration):
    connection_controller.update_connection_field(
        connection_id=connection_id,
        user_id=user_id,
        field="github_registration",
        value=reg.model_dump(),
    )


def _split_repo(github_repo: Optional[str]) -> tuple[str, str]:
    """Splits 'owner/repo' into (owner, repo). Raises HTTPException if invalid."""
    if not github_repo or "/" not in github_repo:
        raise HTTPException(400, "Connection has no valid github_repo (expected 'owner/repo')")
    owner, repo = github_repo.split("/", 1)
    return owner, repo


# ═════════════════════════════════════════════════════════════════════════════
# PR WEBHOOK ROUTES
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/webhook", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
async def github_pr_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(None),
    x_github_event: str = Header(None),
    connection_id: str = None,
    user_id: str = None        # kept for routing but NOT trusted for ownership
) -> dict:
    if not connection_id or not user_id:
        raise HTTPException(status_code=400, detail="connection_id and user_id are required")

    raw_body = await request.body()

    if not x_hub_signature_256:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")

    if not github_controller.verify_github_signature(x_hub_signature_256, raw_body):
        raise HTTPException(status_code=401, detail="Invalid GitHub signature")

    try:
        body    = json.loads(raw_body)
        payload = PRWebhookEvent(**body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid payload: {str(e)}")

    if x_github_event != "pull_request" or payload.action not in ("opened", "synchronize"):
        return {"message": f"Ignoring event: {x_github_event}/{payload.action}"}

    # FIX #7: derive user_id from connection document — never trust the query param
    connection = connection_controller.get_connection_by_id(
        connection_id=connection_id,
        user_id=user_id
    )
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    trusted_user_id = connection.user_id  # derived from DB, not from request

    installation_id = str(
        getattr(connection, "github_installation_id", None)
        or (str(payload.installation["id"]) if payload.installation else None)
        or "demo"
    )

    gh_token = github_controller.get_installation_token(installation_id)
    if not gh_token:
        raise HTTPException(status_code=401, detail="Failed to get GitHub App token.")

    pr_number  = payload.pull_request.number
    pr_url     = payload.pull_request.html_url
    repo_owner = payload.repository.owner.login
    repo_name  = payload.repository.name

    changed_files = github_controller.parse_pr_diff(
        github_token=gh_token,
        repo_owner=repo_owner,
        repo_name=repo_name,
        pr_number=pr_number
    )

    if not changed_files:
        return {"pr_number": pr_number, "analyzed": False, "message": "No .sql or .yml files changed"}

    primary_file  = changed_files[0].filename
    primary_asset = primary_file.replace("/", ".").rstrip(".sql").rstrip(".yml").rstrip(".yaml")

    investigation_id = investigation_controller.create_investigation(
        user_id=trusted_user_id,
        connection_id=connection_id,
        event_id=f"github-{pr_number}",
        failure_message=f"GitHub PR #{pr_number} ({pr_url}): Schema change detected in {primary_file}",
        asset_fqn=primary_asset
    )
    if not investigation_id:
        raise HTTPException(status_code=500, detail="Failed to create investigation")

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

    background_tasks.add_task(
        run_investigation_and_update_pr,
        investigation_id=investigation_id,
        user_id=trusted_user_id,
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
        "pr_number":        pr_number,
        "analyzed":         True,
        "investigation_id": investigation_id,
        "changed_files":    len(changed_files),
        "comment_id":       comment_id,
        "message":          "Analysis started. Comment posted to PR."
    }


# ═════════════════════════════════════════════════════════════════════════════
# GITHUB OAUTH + APP REGISTRATION ROUTES
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/oauth/start")
async def github_oauth_start(
    connection_id: str = Query(...),
    current_user: TokenData = Depends(get_current_user),
):
    if not GITHUB_CLIENT_ID:
        raise HTTPException(500, "GITHUB_CLIENT_ID not configured")

    state = _encode_state({
        "connection_id": connection_id,
        "user_id":       current_user.user_id,
    })

    url = (
        f"{GITHUB_AUTHORIZE_URL}"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={GITHUB_REDIRECT_URI}"
        f"&scope=read:org,repo"
        f"&state={state}"
    )
    return RedirectResponse(url=url)


@router.get("/oauth/callback")
async def github_oauth_callback(
    code:  str = Query(...),
    state: str = Query(...),
):
    try:
        state_data    = _decode_state(state)
        connection_id = state_data["connection_id"]
        user_id       = state_data["user_id"]
    except Exception:
        return RedirectResponse(f"{FRONTEND_ERROR_URL}?reason=invalid_state")

    try:
        user_token = await _exchange_code(code)
    except HTTPException:
        return RedirectResponse(f"{FRONTEND_ERROR_URL}?reason=token_exchange_failed")

    try:
        profile       = await _fetch_profile(user_token)
        installations = await _fetch_installations(user_token)
    except HTTPException:
        return RedirectResponse(f"{FRONTEND_ERROR_URL}?reason=github_api_failed")

    # Always use the original user_id from state — never switch users mid-flow
    reg = GitHubAppRegistration(
        oauth_profile=profile,
        installations=installations,
        selected_installation_id=installations[0].installation_id if len(installations) == 1 else None,
        registered_at=datetime.now(timezone.utc).isoformat(),
    )
    _save_registration(connection_id, user_id, reg)

    if len(installations) == 1:
        connection_controller.set_github_installation_id(
            connection_id=connection_id,
            user_id=user_id,
            installation_id=installations[0].installation_id,
        )

    # Issue a fresh JWT for the original user (not the GitHub-created one)
    fresh_token = auth_controller.create_access_token(
        user_id=user_id,
        email=user_id
    )

    return RedirectResponse(
        f"{FRONTEND_SUCCESS_URL}"
        f"?access_token={fresh_token}"
        f"&connection_id={connection_id}"
        f"&github_login={profile.github_login}"
        f"&installations={len(installations)}"
        f"&step=select_installation"
    )

@router.post("/oauth/select-installation", response_model=dict)
async def select_installation(
    connection_id: str,
    installation_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    raw = connection_controller.get_connection_raw(connection_id, current_user.user_id)
    if not raw:
        raise HTTPException(404, "Connection not found")

    reg = _get_registration(raw)
    if not reg:
        raise HTTPException(400, "GitHub OAuth not completed. Call /github/oauth/start first.")

    if installation_id not in [i.installation_id for i in reg.installations]:
        raise HTTPException(400, f"installation_id {installation_id} not in your GitHub installations")

    reg.selected_installation_id = installation_id
    _save_registration(connection_id, current_user.user_id, reg)

    connection_controller.set_github_installation_id(
        connection_id=connection_id,
        user_id=current_user.user_id,
        installation_id=installation_id,
    )

    selected = next(i for i in reg.installations if i.installation_id == installation_id)
    return {
        "connection_id":   connection_id,
        "installation_id": installation_id,
        "account_login":   selected.account_login,
        "repositories":    selected.repositories,
        "next_step":       "configure_webhook",
        "message":         f"Installation '{selected.account_login}' selected. Now configure your webhook.",
    }


@router.post("/oauth/configure-webhook", response_model=dict)
async def configure_webhook(
    body: GitHubWebhookConfigRequest,
    current_user: TokenData = Depends(get_current_user),
):
    raw = connection_controller.get_connection_raw(body.connection_id, current_user.user_id)
    if not raw:
        raise HTTPException(404, "Connection not found")

    reg = _get_registration(raw)
    if not reg:
        raise HTTPException(400, "GitHub OAuth not completed. Call /github/oauth/start first.")

    if not reg.selected_installation_id:
        raise HTTPException(400, "No installation selected. Call /github/oauth/select-installation first.")

    # FIX #5: extract repo_owner / repo_name from connection and pass to controller
    repo_owner, repo_name = _split_repo(raw.get("github_repo"))

    full_webhook_url = github_controller.build_webhook_url(
        connection_id=body.connection_id,
        user_id=current_user.user_id,
        api_base_url=API_BASE_URL
    )

    installation_token = github_controller.get_installation_token(body.installation_id)
    registration_result = None
    registration_error  = None

    if installation_token:
        registration_result = github_controller.register_github_webhook(
            github_token=installation_token,
            repo_owner=repo_owner,        # FIX #5
            repo_name=repo_name,          # FIX #5
            webhook_url=full_webhook_url,
            webhook_secret=body.webhook_secret
        )
        if not registration_result:
            registration_error = "Failed to register webhook with GitHub API. See logs."

    updated = False
    for inst in reg.installations:
        if inst.installation_id == body.installation_id:
            inst.webhook_url        = full_webhook_url
            inst.webhook_secret     = body.webhook_secret
            inst.webhook_configured = bool(registration_result)
            if registration_result:
                inst.webhook_id = registration_result.get("webhook_id")
            updated = True
            break

    if not updated:
        raise HTTPException(404, f"Installation {body.installation_id} not found")

    _save_registration(body.connection_id, current_user.user_id, reg)

    response = {
        "connection_id":      body.connection_id,
        "installation_id":    body.installation_id,
        "webhook_configured": bool(registration_result),
        "webhook_url":        full_webhook_url,
    }

    if registration_result:
        response.update({
            "status":  "success",
            "message": "Webhook automatically registered with GitHub! PR events will now trigger analysis.",
            "webhook_id": registration_result.get("webhook_id"),
            "github_status": {
                "url":    registration_result.get("url"),
                "active": registration_result.get("active"),
            }
        })
    else:
        response.update({
            "status":  "partial",
            "message": registration_error or "Webhook registration requires manual GitHub App setup.",
            "manual_configuration": {
                "instructions":  "Paste these into your GitHub repo Settings → Webhooks",
                "webhook_url":   full_webhook_url,
                "webhook_secret": body.webhook_secret,
                "content_type":  "application/json",
                "events":        ["pull_request"],
                "active":        True,
            }
        })

    return response


@router.get("/oauth/status", response_model=GitHubRegistrationStatusResponse)
async def github_registration_status(
    connection_id: str = Query(...),
    current_user: TokenData = Depends(get_current_user),
):
    raw = connection_controller.get_connection_raw(connection_id, current_user.user_id)
    if not raw:
        raise HTTPException(404, "Connection not found")

    reg = _get_registration(raw)
    if not reg:
        return GitHubRegistrationStatusResponse(oauth_connected=False)

    webhook_configured = False
    webhook_url        = None

    if reg.selected_installation_id:
        for inst in reg.installations:
            if inst.installation_id == reg.selected_installation_id:
                webhook_configured = inst.webhook_configured
                webhook_url        = inst.webhook_url
                break

    return GitHubRegistrationStatusResponse(
        oauth_connected=True,
        github_login=reg.oauth_profile.github_login,
        github_avatar_url=reg.oauth_profile.github_avatar_url,
        installations=reg.installations,
        selected_installation_id=reg.selected_installation_id,
        webhook_configured=webhook_configured,
        webhook_url=webhook_url,
    )


@router.get("/webhook/verify", response_model=dict)
async def verify_webhook_status(
    connection_id: str = Query(...),
    current_user: TokenData = Depends(get_current_user),
):
    raw = connection_controller.get_connection_raw(connection_id, current_user.user_id)
    if not raw:
        raise HTTPException(404, "Connection not found")

    reg = _get_registration(raw)
    if not reg or not reg.selected_installation_id:
        raise HTTPException(400, "GitHub registration incomplete. Complete OAuth flow first.")

    repo_owner, repo_name = _split_repo(raw.get("github_repo"))

    selected_inst = next(
        (i for i in reg.installations if i.installation_id == reg.selected_installation_id),
        None
    )
    if not selected_inst or not selected_inst.webhook_id:
        raise HTTPException(400, "No webhook registered for selected installation.")

    installation_token = github_controller.get_installation_token(reg.selected_installation_id)
    if not installation_token:
        raise HTTPException(401, "Failed to get GitHub App token for verification")

    webhook_info = github_controller.verify_github_webhook(
        github_token=installation_token,
        repo_owner=repo_owner,
        repo_name=repo_name,
        webhook_id=selected_inst.webhook_id
    )

    if webhook_info:
        return {
            "connection_id":    connection_id,
            "webhook_verified": True,
            **webhook_info,
            "message": "Webhook is registered and active on GitHub"
        }
    else:
        return {
            "connection_id":    connection_id,
            "webhook_verified": False,
            "message": "Webhook not found on GitHub or error retrieving status"
        }


@router.post("/webhook/cleanup", response_model=dict)
async def cleanup_webhook(
    connection_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    raw = connection_controller.get_connection_raw(connection_id, current_user.user_id)
    if not raw:
        raise HTTPException(404, "Connection not found")

    reg = _get_registration(raw)
    if not reg or not reg.selected_installation_id:
        return {
            "connection_id": connection_id,
            "deleted": False,
            "message": "No GitHub registration found, nothing to clean up"
        }

    repo_owner, repo_name = _split_repo(raw.get("github_repo"))

    selected_inst = next(
        (i for i in reg.installations if i.installation_id == reg.selected_installation_id),
        None
    )
    if not selected_inst or not selected_inst.webhook_id:
        return {
            "connection_id": connection_id,
            "deleted": False,
            "message": "No webhook_id stored, nothing to clean up"
        }

    installation_token = github_controller.get_installation_token(reg.selected_installation_id)
    if not installation_token:
        return {
            "connection_id": connection_id,
            "deleted": False,
            "message": "Could not get GitHub App token for cleanup"
        }

    success = github_controller.delete_github_webhook(
        github_token=installation_token,
        repo_owner=repo_owner,
        repo_name=repo_name,
        webhook_id=selected_inst.webhook_id
    )

    for inst in reg.installations:
        if inst.installation_id == reg.selected_installation_id:
            inst.webhook_url        = None
            inst.webhook_secret     = None
            inst.webhook_id         = None
            inst.webhook_configured = False
            break

    _save_registration(connection_id, current_user.user_id, reg)

    return {
        "connection_id": connection_id,
        "deleted": success,
        "message": "Webhook deleted from GitHub" if success else "Failed to delete webhook from GitHub"
    }