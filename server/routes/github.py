"""
GitHub PR Routes
Handles GitHub App PR webhooks and analysis.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Header
from typing import Optional

from models.github import PRWebhookEvent
from controllers import github_controller, investigation_controller, connection_controller
from routes.auth import get_current_user
from models.users import TokenData

router = APIRouter(prefix="/github", tags=["github"])


@router.post("/webhook", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
async def github_pr_webhook(
    payload: PRWebhookEvent,
    x_hub_signature_256: str = Header(None),
    connection_id: str = None,
    user_id: str = None
) -> dict:
    """
    Receive GitHub App PR webhook.
    Automatically analyzes PRs and posts comments with impact analysis.
    
    **Headers:**
    - `X-Hub-Signature-256`: HMAC-SHA256 signature (required)
    - `X-GitHub-Event`: Event type (pull_request)
    
    **Query Parameters:**
    - `connection_id`: Connection ID to use
    - `user_id`: User ID
    
    **Request Body:**
    GitHub's standard pull_request event payload
    
    **Response:**
    ```json
    {
        "pr_number": 42,
        "analyzed": true,
        "comment_id": "123456",
        "message": "PR analysis posted"
    }
    ```
    
    **Behavior:**
    1. Validates webhook signature
    2. Parses PR diff (.sql & .yml files only)
    3. Triggers investigation if assets affected
    4. Posts analysis comment to PR
    
    **Validation:**
    - Signature must match GITHUB_WEBHOOK_SECRET
    """
    if not connection_id or not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="connection_id and user_id are required"
        )
    
    # Verify signature
    if not x_hub_signature_256:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Hub-Signature-256 header"
        )
    
    if not github_controller.verify_github_signature(x_hub_signature_256, payload.json().encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid GitHub signature"
        )
    
    # Get connection
    connection = connection_controller.get_connection_by_id(
        connection_id=connection_id,
        user_id=user_id
    )
    
    if not connection or not connection.github_installation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="GitHub connection not configured"
        )
    
    try:
        # Extract PR info
        pr_number = payload.pull_request.number
        pr_url = payload.pull_request.html_url
        repo_owner = payload.repository.owner.login
        repo_name = payload.repository.name
        
        # Get GitHub token
        gh_token = github_controller.get_installation_token(
            connection.github_installation_id
        )
        
        if not gh_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Failed to get GitHub App token"
            )
        
        # Parse PR diff
        changed_files = github_controller.parse_pr_diff(
            github_token=gh_token,
            repo_owner=repo_owner,
            repo_name=repo_name,
            pr_number=pr_number
        )
        
        if not changed_files:
            # No relevant files changed
            return {
                "pr_number": pr_number,
                "analyzed": False,
                "message": "No .sql or .yml files changed in PR"
            }
        
        # Trigger investigation (would normally be async)
        investigation_id = investigation_controller.create_investigation(
            user_id=user_id,
            connection_id=connection_id,
            event_id=f"github-{pr_number}",
            failure_message=f"GitHub PR #{pr_number}: Analyzing impact on {len(changed_files)} files"
        )
        
        if not investigation_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create investigation"
            )
        
        # Build analysis (would normally get from completed investigation)
        # For now, return basic response
        return {
            "pr_number": pr_number,
            "analyzed": True,
            "investigation_id": investigation_id,
            "changed_files": len(changed_files),
            "message": "PR analysis in progress. Check investigation for results."
        }
    except Exception as e:
        print(f"ERROR processing GitHub PR: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process PR: {str(e)}"
        )


@router.post("/authorize", response_model=dict)
async def github_authorize(
    connection_id: str,
    installation_id: str,
    current_user: TokenData = Depends(get_current_user)
) -> dict:
    """
    Called after user authorizes GitHub App installation.
    Stores the installation_id on the connection.
    
    **Query Parameters:**
    - `connection_id`: Connection ID
    - `installation_id`: GitHub App installation ID from GitHub
    
    **Response:**
    ```json
    {
        "connection_id": "507f1f77bcf86cd799439011",
        "github_installation_id": "12345678",
        "message": "GitHub App authorized"
    }
    ```
    
    **Typical Flow:**
    1. User clicks "Connect GitHub"
    2. Redirected to GitHub OAuth flow
    3. User authorizes KS-RAG app on their repo
    4. GitHub redirects back with installation_id parameter
    5. Frontend calls this endpoint to save installation_id
    """
    success = connection_controller.set_github_installation_id(
        connection_id=connection_id,
        user_id=current_user.user_id,
        installation_id=installation_id
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connection not found"
        )
    
    return {
        "connection_id": connection_id,
        "github_installation_id": installation_id,
        "message": "GitHub App authorized successfully"
    }


@router.get("/pr-analysis/{pr_number}", response_model=dict)
async def get_pr_analysis(
    pr_number: int,
    connection_id: str,
    current_user: TokenData = Depends(get_current_user)
) -> dict:
    """
    Get PR analysis results (if analysis has been posted).
    
    **Path Parameters:**
    - `pr_number`: GitHub PR number
    
    **Query Parameters:**
    - `connection_id`: Connection ID
    
    **Response:**
    ```json
    {
        "pr_number": 42,
        "root_cause": "Column was removed in upstream transform",
        "impacted_assets": ["users", "orders_summary"],
        "suggested_fixes": "ALTER TABLE ... ADD COLUMN ...",
        "confidence_score": 0.92
    }
    ```
    
    **Note:** Returns cached analysis from investigation, not real-time.
    """
    # This would fetch investigation linked to this PR
    # For now, return placeholder
    return {
        "pr_number": pr_number,
        "message": "PR analysis feature coming soon"
    }
