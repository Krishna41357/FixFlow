"""
Event Routes
Handles webhook intake from dbt, GitHub, and manual queries.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Header
from typing import List

from models.events import DbtWebhookPayload, GitHubPRPayload, ManualQueryPayload
from controllers import event_controller, connection_controller
from routes.auth import get_current_user
from models.users import TokenData

router = APIRouter(prefix="/events", tags=["events"])


@router.post("/dbt-webhook", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
async def dbt_webhook(
    payload: DbtWebhookPayload,
    x_dbt_signature: str = Header(None),
    connection_id: str = None,
    user_id: str = None
) -> dict:
    """
    Receive dbt Cloud webhook notification.
    Called when dbt run completes (success or failure).
    
    **Headers:**
    - `X-dBt-Signature`: HMAC-SHA256 signature (optional, recommended)
    
    **Query Parameters:**
    - `connection_id`: Connection ID to use
    - `user_id`: User ID (if webhook comes from external system)
    
    **Request Body:**
    ```json
    {
        "data": {
            "run_id": "abc123",
            "node_id": "model.proj.table_name",
            "error_message": "Schema mismatch..."
        }
    }
    ```
    
    **Response:**
    ```json
    {
        "event_id": "507f1f77bcf86cd799439011",
        "status": "accepted",
        "message": "Event queued for processing"
    }
    ```
    
    **Note:** Signature validation is optional. Store webhook secret in environment.
    """
    if not connection_id or not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="connection_id and user_id are required"
        )
    
    # Handle webhook
    event_id = event_controller.handle_dbt_webhook(
        connection_id=connection_id,
        user_id=user_id,
        payload=payload,
        signature=x_dbt_signature
    )
    
    if not event_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to process dbt webhook"
        )
    
    return {
        "event_id": event_id,
        "status": "accepted",
        "message": "Event queued for processing"
    }


@router.post("/github-webhook", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    payload: GitHubPRPayload,
    x_hub_signature_256: str = Header(None),
    connection_id: str = None,
    user_id: str = None
) -> dict:
    """
    Receive GitHub App webhook notification.
    Called when pull request is created or updated.
    
    **Headers:**
    - `X-Hub-Signature-256`: HMAC-SHA256 signature (required for security)
    
    **Query Parameters:**
    - `connection_id`: Connection ID to use
    - `user_id`: User ID
    
    **Request Body:**
    GitHub's standard pull_request event payload
    
    **Response:**
    ```json
    {
        "event_id": "507f1f77bcf86cd799439011",
        "status": "accepted",
        "message": "PR webhook queued for processing"
    }
    ```
    
    **Validation:**
    - GitHub signature must be valid (checked against GITHUB_WEBHOOK_SECRET env var)
    """
    if not connection_id or not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="connection_id and user_id are required"
        )
    
    # Handle webhook
    event_id = event_controller.handle_github_pr(
        connection_id=connection_id,
        user_id=user_id,
        payload=payload,
        signature=x_hub_signature_256
    )
    
    if not event_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to process GitHub webhook"
        )
    
    return {
        "event_id": event_id,
        "status": "accepted",
        "message": "PR webhook queued for processing"
    }


@router.post("/manual-query", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
async def manual_query(
    payload: ManualQueryPayload,
    current_user: TokenData = Depends(get_current_user)
) -> dict:
    """
    Manually trigger an investigation from chat interface.
    User asks about a specific asset that's failing.
    
    **Request Body:**
    ```json
    {
        "connection_id": "507f1f77bcf86cd799439011",
        "asset_fqn": "snowflake.prod.orders",
        "failure_query": "Why is the orders table returning NULL values?"
    }
    ```
    
    **Response:**
    ```json
    {
        "event_id": "507f1f77bcf86cd799439012",
        "status": "accepted",
        "message": "Investigation started"
    }
    ```
    
    **Note:** Investigation runs asynchronously. Caller should poll /investigations for status.
    """
    # Handle manual query
    event_id = event_controller.handle_manual_query(
        user_id=current_user.user_id,
        payload=payload
    )
    
    if not event_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create investigation query"
        )
    
    return {
        "event_id": event_id,
        "status": "accepted",
        "message": "Investigation started"
    }


@router.get("", response_model=List[dict])
async def list_events(
    current_user: TokenData = Depends(get_current_user),
    limit: int = 20
) -> List[dict]:
    """
    List recent events for current user.
    Shows webhook intake history and manual queries.
    
    **Query Parameters:**
    - `limit`: Max results (default: 20, max: 100)
    
    **Response:**
    ```json
    [
        {
            "id": "507f1f77bcf86cd799439011",
            "event_type": "dbt_run_failure",
            "source_id": "model.proj.orders",
            "failure_message": "dbt model failed: ...",
            "created_at": "2024-01-15T10:30:00Z",
            "processed": false,
            "investigation_id": null
        }
    ]
    ```
    
    **Event Types:**
    - `dbt_run_failure` - Failure from dbt Cloud
    - `github_pr` - PR created/updated
    - `manual_query` - Manual investigation from chat
    """
    if limit > 100:
        limit = 100
    
    return event_controller.get_events_for_user(
        user_id=current_user.user_id,
        limit=limit
    )
