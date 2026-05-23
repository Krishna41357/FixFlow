import os
import hmac
import hashlib
import time
import requests
import json
from typing import List, Optional
from datetime import datetime, timezone
from dotenv import load_dotenv

from models.github import (
    PRWebhookEvent, ChangedAsset, PRAnalysis, ImpactedAsset
)

load_dotenv()

GITHUB_APP_ID          = os.getenv("GITHUB_APP_ID", "")
GITHUB_APP_PRIVATE_KEY = os.getenv("GITHUB_APP_PRIVATE_KEY", "").replace("\\n", "\n")
GITHUB_WEBHOOK_SECRET  = os.getenv("GITHUB_WEBHOOK_SECRET", "")
GITHUB_TEST_PAT        = os.getenv("GITHUB_TEST_PAT", "")


# ── Signature verification ────────────────────────────────────────────────────

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


# ── Installation token ────────────────────────────────────────────────────────

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

    # FIX #2: guard against int being passed in (model declared int, we want str)
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


# ── Webhook URL builder ───────────────────────────────────────────────────────

def build_webhook_url(connection_id: str, user_id: str, api_base_url: str) -> str:
    api_base_url = api_base_url.rstrip("/")
    return f"{api_base_url}/api/vi/github/webhook?connection_id={connection_id}&user_id={user_id}"


# ── Webhook lifecycle management ──────────────────────────────────────────────

def register_github_webhook(
    github_token: str,
    repo_owner: str,       # FIX #1 + #5: now required
    repo_name: str,        # FIX #1 + #5: now required
    webhook_url: str,
    webhook_secret: str
) -> Optional[dict]:
    """
    Registers a repo-level webhook via POST /repos/{owner}/{repo}/hooks.
    Returns dict with webhook_id on success (HTTP 201), None on failure.

    FIX: was incorrectly using PATCH /app/hook/config (global App webhook).
    """
    if not github_token or not webhook_url or not repo_owner or not repo_name:
        print("ERROR register_github_webhook: Missing required parameters")
        return None

    try:
        # FIX #1: correct endpoint — repo-level hook, not global app hook
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/hooks"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        # FIX #1: correct payload — nested "config" dict, not flat
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

        # FIX #1: GitHub returns 201 Created for new hooks, not 200
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
    """
    Updates an existing repo-level webhook via PATCH /repos/{owner}/{repo}/hooks/{id}.

    FIX: was using PATCH /app/hook/config (global App webhook).
    """
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
    """
    Deletes a repo-level webhook via DELETE /repos/{owner}/{repo}/hooks/{id}.

    FIX: was using DELETE /app/hook/config (global App webhook).
    """
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

        # 204 = deleted, 404 = already gone — both are success
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
    """
    Fetches a specific repo-level webhook via GET /repos/{owner}/{repo}/hooks/{id}.

    FIX: was using GET /app/hook/config (global App webhook).
    """
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


# ── PR diff parsing ───────────────────────────────────────────────────────────

def parse_pr_diff(
    github_token: str,
    repo_owner: str,
    repo_name: str,
    pr_number: int
) -> List[ChangedAsset]:
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

        changed_assets = []
        for file_info in response.json():
            filename = file_info.get("filename", "")
            if filename.endswith((".sql", ".yml", ".yaml")):
                changed_assets.append(ChangedAsset(
                    filename=filename,
                    status=file_info.get("status", "modified"),
                    additions=file_info.get("additions", 0),
                    deletions=file_info.get("deletions", 0),
                    changes=file_info.get("changes", 0),
                    patch=file_info.get("patch", ""),
                ))

        print(f"DEBUG parse_pr_diff: Found {len(changed_assets)} relevant changed files")
        return changed_assets

    except Exception as e:
        print(f"ERROR parse_pr_diff: {e}")
        return []


# ── PR comment posting / updating ─────────────────────────────────────────────

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