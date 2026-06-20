"""
GitHub Routes — PR Webhooks + OAuth App Registration

Route organisation:
  ── PR webhook ───────────────────────────────────────────────────────────────
  POST /webhook                    — receives GitHub PR events, starts investigation

  ── GitHub OAuth + App Registration ──────────────────────────────────────────
  GET  /oauth/start                — initiates OAuth flow
  GET  /oauth/callback             — handles OAuth callback
  POST /oauth/select-installation  — user picks which GitHub App installation to use
  POST /oauth/configure-webhook    — registers webhook on GitHub repo
  GET  /oauth/status               — returns full registration state
  GET  /webhook/verify             — checks webhook is still active on GitHub
  POST /webhook/cleanup            — deletes webhook from GitHub and clears local state

Changes from previous version:
  - run_investigation_and_update_pr replaced by run_pr_investigation
    (multi-asset, merged lineage, new AI schema, new comment renderer)
  - Webhook handler now calls filter_relevant_files + derive_fqns to build
    asset_fqn_map before handing off to background task
  - render_placeholder_comment used for initial comment (replaces inline f-string)
  - Push events on default branch trigger incremental graph refresh
  - All OAuth routes, helpers, and auth logic are UNCHANGED
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

GITHUB_CLIENT_ID     = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
GITHUB_REDIRECT_URI  = os.getenv("GITHUB_REDIRECT_URI", "http://localhost:8000/api/v1/github/oauth/callback")
FRONTEND_SUCCESS_URL = os.getenv("FRONTEND_SUCCESS_URL", "http://localhost:3000/github/setup")
FRONTEND_ERROR_URL   = os.getenv("FRONTEND_ERROR_URL",   "http://localhost:3000/error")
API_BASE_URL         = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")

GITHUB_AUTHORIZE_URL     = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL         = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL          = "https://api.github.com/user"
GITHUB_INSTALLATIONS_URL = "https://api.github.com/user/installations"
GITHUB_REPOS_URL         = "https://api.github.com/user/installations/{installation_id}/repositories"


# ═════════════════════════════════════════════════════════════════════════════
# BACKGROUND TASK — push-triggered graph refresh
# ═════════════════════════════════════════════════════════════════════════════

async def _run_push_graph_update(
    connection_id: str,
    user_id: str,
    gh_token: str,
    repo_owner: str,
    repo_name: str,
    changed_paths: list,
):
    """
    Incremental graph refresh triggered by a direct push to the default branch.
    Re-indexes only the changed files, leaving the rest of the graph intact.
    Falls back to a full scan if no base graph exists yet.
    """
    print(
        f"DEBUG _run_push_graph_update: Refreshing graph for "
        f"{len(changed_paths)} file(s) in {repo_owner}/{repo_name}"
    )
    try:
        from controllers import repo_parser_controller
        repo_parser_controller.update_graph_nodes(
            github_token=gh_token,
            repo_owner=repo_owner,
            repo_name=repo_name,
            connection_id=connection_id,
            changed_file_paths=changed_paths,
        )
    except Exception as e:
        print(f"ERROR _run_push_graph_update: Failed to update graph: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# PR WEBHOOK
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/webhook", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
async def github_pr_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(None),
    x_github_event: str = Header(None),
    connection_id: str = None,
    user_id: str = None
) -> dict:
    # ── Gate 1: required query params ────────────────────────────────────────
    if not connection_id or not user_id:
        raise HTTPException(status_code=400, detail="connection_id and user_id are required")

    raw_body = await request.body()

    # ── Gate 2: signature header present ─────────────────────────────────────
    if not x_hub_signature_256:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")

    # ── Gate 3: signature valid ───────────────────────────────────────────────
    if not github_controller.verify_github_signature(x_hub_signature_256, raw_body):
        raise HTTPException(status_code=401, detail="Invalid GitHub signature")

    # ── Gate 4: parse JSON ────────────────────────────────────────────────────
    try:
        body = json.loads(raw_body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid JSON: {str(e)}")

    # ── Early extraction: shared by BOTH push and PR branches ────────────────
    repo_owner = body.get("repository", {}).get("owner", {}).get("login", "")
    if not repo_owner:
        repo_owner = body.get("repository", {}).get("owner", {}).get("name", "")
    repo_name = body.get("repository", {}).get("name", "")

    connection = connection_controller.get_connection_by_id(connection_id, user_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    trusted_user_id = connection.user_id
    installation_id = str(getattr(connection, "github_installation_id", "demo") or "demo")
    gh_token = github_controller.get_installation_token(installation_id)
    if not gh_token:
        raise HTTPException(status_code=401, detail="Failed to get GitHub App token")

    # ── Gate 5: event type filter BEFORE Pydantic parse ──────────────────────
    action = body.get("action", "")

    # ── 5a: Push to default branch → incremental graph refresh ───────────────
    if x_github_event == "push":
        ref = body.get("ref", "")
        default_branch = body.get("repository", {}).get("default_branch", "main")
        if ref not in (f"refs/heads/{default_branch}", "refs/heads/main", "refs/heads/master"):
            return {"message": f"Ignoring push to non-default branch: {ref}"}

        changed_paths: set = set()
        for commit in body.get("commits", []):
            changed_paths.update(commit.get("added",    []))
            changed_paths.update(commit.get("modified", []))
            changed_paths.update(commit.get("removed",  []))

        _DBT_DIRS_PUSH = ("models/", "seeds/", "snapshots/", "analyses/", "macros/", "migrations/")
        relevant_paths = [
            p for p in changed_paths
            if p.endswith(".sql") or (
                p.endswith((".yml", ".yaml"))
                and any(p.startswith(d) for d in _DBT_DIRS_PUSH)
            )
        ]

        if not relevant_paths:
            return {"message": "Push received — no data-relevant files changed, graph unchanged"}

        background_tasks.add_task(
            _run_push_graph_update,
            connection_id=connection_id,
            user_id=user_id,
            gh_token=gh_token,
            repo_owner=repo_owner,
            repo_name=repo_name,
            changed_paths=relevant_paths,
        )

        print(
            f"DEBUG push_handler: Queued graph update for {len(relevant_paths)} "
            f"data-relevant file(s) changed by direct push to {ref}"
        )
        return {
            "event":          "push",
            "ref":            ref,
            "relevant_files": len(relevant_paths),
            "total_files":    len(changed_paths),
            "message":        f"Graph refresh queued for {len(relevant_paths)} data file(s) changed by direct push."
        }

    # ── 5b: Only pull_request opened/synchronize continues to PR analysis ────
    if x_github_event != "pull_request" or action not in ("opened", "synchronize"):
        return {"message": f"Ignoring event: {x_github_event}/{action}"}

    # ── Gate 6: parse into PRWebhookEvent (pull_request events only) ─────────
    try:
        payload = PRWebhookEvent(**body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid payload: {str(e)}")

    # ── Dict access: repo_owner/repo_name overwritten from Pydantic ──────────
    # (more reliable than raw dict extraction done above for the push branch)
    pr_number  = payload.pull_request["number"]
    pr_url     = payload.pull_request["html_url"]
    repo_owner = payload.repository["owner"]["login"]
    repo_name  = payload.repository["name"]

    # ── Step 1: Fetch all changed files ───────────────────────────────────────
    all_changed = github_controller.parse_pr_diff(
        github_token=gh_token,
        repo_owner=repo_owner,
        repo_name=repo_name,
        pr_number=pr_number
    )

    # ── Step 2: Filter to data-relevant files only ────────────────────────────
    relevant_files = github_controller.filter_relevant_files(all_changed)

    if not relevant_files:
        return {
            "pr_number": pr_number,
            "analyzed":  False,
            "message":   "No data-relevant files changed (.sql or dbt .yml)"
        }

    # ── Step 3: Derive FQNs + strip patches for all relevant files ───────────
    raw_fqn_map = github_controller.derive_fqns(relevant_files)

    filename_to_asset = {asset.filename: asset for asset in relevant_files}

    asset_fqn_map = {}
    for key, (fqn, approximate) in raw_fqn_map.items():
        base_filename = key.split("::")[0]
        asset = filename_to_asset.get(base_filename)
        stripped_patch = github_controller.strip_context_lines(
            asset.patch if asset else None
        )
        asset_fqn_map[key] = (fqn, approximate, stripped_patch)

    # ── Step 4: Create investigation document ─────────────────────────────────
    all_fqns    = [fqn for (fqn, _, _) in asset_fqn_map.values()]
    primary_fqn = all_fqns[0] if all_fqns else "unknown"

    investigation_id = investigation_controller.create_investigation(
        user_id=trusted_user_id,
        connection_id=connection_id,
        event_id=f"github-pr-{pr_number}",
        failure_message=(
            f"GitHub PR #{pr_number} ({pr_url}): "
            f"Schema changes detected in {len(relevant_files)} file(s): "
            f"{', '.join(a.filename for a in relevant_files)}"
        ),
        asset_fqn=primary_fqn,
        event_type="github_pr"
    )
    if not investigation_id:
        raise HTTPException(status_code=500, detail="Failed to create investigation")

    # ── Step 5: Post placeholder comment immediately ──────────────────────────
    placeholder = github_controller.render_placeholder_comment(
        relevant_files=relevant_files,
        investigation_id=investigation_id
    )

    comment_id = github_controller.post_pr_comment(
        github_token=gh_token,
        repo_owner=repo_owner,
        repo_name=repo_name,
        pr_number=pr_number,
        comment_body=placeholder
    ) or "0"

    # ── Step 6: Queue background investigation ────────────────────────────────
    background_tasks.add_task(
        investigation_controller.run_pr_investigation,
        investigation_id=investigation_id,
        user_id=trusted_user_id,
        connection_id=connection_id,
        asset_fqn_map=asset_fqn_map,
        pr_number=pr_number,
        gh_token=gh_token,
        repo_owner=repo_owner,
        repo_name=repo_name,
        comment_id=comment_id,
        pr_head_ref=payload.pull_request["head"]["ref"],
    )

    return {
        "pr_number":        pr_number,
        "analyzed":         True,
        "investigation_id": investigation_id,
        "relevant_files":   len(relevant_files),
        "total_files":      len(all_changed),
        "asset_fqns":       all_fqns,
        "comment_id":       comment_id,
        "message":          f"Analysis started for {len(relevant_files)} data file(s). Comment posted to PR."
    }


# ═════════════════════════════════════════════════════════════════════════════
# OAUTH HELPERS (unchanged)
# ═════════════════════════════════════════════════════════════════════════════

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
    data  = resp.json()
    token = data.get("access_token")
    if not token:
        raise HTTPException(400, f"Token exchange failed: {data.get('error_description', data)}")
    return token


async def _gh_get(url: str, token: str):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url,
            headers={
                "Authorization":        f"Bearer {token}",
                "Accept":               "application/vnd.github+json",
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
    print(f"DEBUG _fetch_installations: raw response = {data}")
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
    print(f"DEBUG _fetch_installations: found {len(result)} installations")
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
    if not github_repo or "/" not in github_repo:
        raise HTTPException(400, "Connection has no valid github_repo (expected 'owner/repo')")
    owner, repo = github_repo.split("/", 1)
    return owner, repo


# ═════════════════════════════════════════════════════════════════════════════
# GITHUB OAUTH + APP REGISTRATION ROUTES (unchanged)
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
        f"&scope=read:user,read:org,repo"
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

    repo_owner, repo_name = _split_repo(raw.get("github_repo"))

    full_webhook_url = github_controller.build_webhook_url(
        connection_id=body.connection_id,
        user_id=current_user.user_id,
        api_base_url=API_BASE_URL
    )

    installation_token  = github_controller.get_installation_token(body.installation_id)
    registration_result = None
    registration_error  = None

    if installation_token:
        registration_result = github_controller.register_github_webhook(
            github_token=installation_token,
            repo_owner=repo_owner,
            repo_name=repo_name,
            webhook_url=full_webhook_url,
            webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET")
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
            "status":     "success",
            "message":    "Webhook automatically registered with GitHub! PR events will now trigger analysis.",
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
                "events":        ["pull_request", "push"],
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
            "deleted":       False,
            "message":       "No GitHub registration found, nothing to clean up"
        }

    repo_owner, repo_name = _split_repo(raw.get("github_repo"))

    selected_inst = next(
        (i for i in reg.installations if i.installation_id == reg.selected_installation_id),
        None
    )
    if not selected_inst or not selected_inst.webhook_id:
        return {
            "connection_id": connection_id,
            "deleted":       False,
            "message":       "No webhook_id stored, nothing to clean up"
        }

    installation_token = github_controller.get_installation_token(reg.selected_installation_id)
    if not installation_token:
        return {
            "connection_id": connection_id,
            "deleted":       False,
            "message":       "Could not get GitHub App token for cleanup"
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
        "deleted":       success,
        "message":       "Webhook deleted from GitHub" if success else "Failed to delete webhook from GitHub"
    }