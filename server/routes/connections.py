"""
Connection Routes
Manages GitHub and workspace credentials per user.
OpenMetadata fields are kept for backward compatibility but are optional.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List

from models.users import ConnectionCreate, ConnectionResponse
from controllers import connection_controller
from routes.auth import get_current_user
from models.users import TokenData

router = APIRouter(prefix="/connections", tags=["connections"])


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_connection(
    connection_data: ConnectionCreate,
    current_user: TokenData = Depends(get_current_user)
) -> dict:
    """
    Create a new workspace connection (GitHub repo + optional OpenMetadata).
    
    **Request:**
    ```json
    {
        "workspace_name": "Production",
        "openmetadata_url": "https://metadata.company.com",
        "openmetadata_token": "your-token",
        "github_repo": "owner/repo-name"
    }
    ```
    
    **Response:**
    ```json
    {
        "id": "507f1f77bcf86cd799439011",
        "user_id": "507f1f77bcf86cd799439010",
        "workspace_name": "Production",
        "openmetadata_url": "https://metadata.company.com",
        "github_repo": "owner/repo-name",
        "created_at": "2024-01-15T10:30:00Z"
    }
    ```
    
    **Errors:**
    - 400: Invalid credentials
    - 409: Connection already exists
    """
    connection = connection_controller.create_connection(
        user_id=current_user.user_id,
        connection_data=connection_data
    )
    
    if not connection:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create connection. Check credentials."
        )
    
    return connection.model_dump()


@router.get("", response_model=List[ConnectionResponse])
async def list_connections(
    current_user: TokenData = Depends(get_current_user)
) -> List[ConnectionResponse]:
    """
    List all active connections for current user.
    Tokens are masked for security.
    
    **Response:**
    ```json
    [
        {
            "id": "507f1f77bcf86cd799439011",
            "workspace_name": "Production",
            "openmetadata_url": "https://metadata.company.com",
            "github_repo": "owner/repo-name",
            "token_masked": "***4d5e",
            "created_at": "2024-01-15T10:30:00Z"
        }
    ]
    ```
    """
    return connection_controller.get_user_connections(
        user_id=current_user.user_id
    )


@router.get("/{connection_id}", response_model=dict)
async def get_connection(
    connection_id: str,
    current_user: TokenData = Depends(get_current_user)
) -> dict:
    """
    Get a specific connection by ID.
    Only accessible to the connection owner.
    
    **Path Parameters:**
    - `connection_id`: Connection ID
    
    **Response:**
    ```json
    {
        "id": "507f1f77bcf86cd799439011",
        "workspace_name": "Production",
        "openmetadata_url": "https://metadata.company.com",
        "github_repo": "owner/repo-name",
        "created_at": "2024-01-15T10:30:00Z"
    }
    ```
    """
    connection = connection_controller.get_connection_by_id(
        connection_id=connection_id,
        user_id=current_user.user_id
    )
    
    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connection not found"
        )
    
    return connection.model_dump()


@router.post("/{connection_id}/verify", response_model=dict, status_code=status.HTTP_200_OK)
async def verify_connection(
    connection_id: str,
    current_user: TokenData = Depends(get_current_user)
) -> dict:
    """
    Verify that the connection credentials are still valid.
    
    **Path Parameters:**
    - `connection_id`: Connection ID
    
    **Response:**
    ```json
    {
        "connection_id": "507f1f77bcf86cd799439011",
        "is_valid": true,
        "message": "Connection OK"
    }
    ```
    """
    connection = connection_controller.get_connection_by_id(
        connection_id=connection_id,
        user_id=current_user.user_id
    )
    
    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connection not found"
        )
    
    is_valid = connection_controller.verify_openmetadata_connection(
        url=connection.openmetadata_host,
        token=connection.openmetadata_token
    )
    
    return {
        "connection_id": connection_id,
        "is_valid": is_valid,
        "message": "Connection OK" if is_valid else "Connection failed"
    }


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: str,
    current_user: TokenData = Depends(get_current_user)
) -> None:
    """
    Delete a connection.
    Related investigations will be marked as orphaned.
    GitHub webhooks (if registered) will be cleaned up.
    
    **Path Parameters:**
    - `connection_id`: Connection ID
    """
    # Try to cleanup GitHub webhook before deleting connection
    # This is best-effort; we still delete the connection even if cleanup fails
    try:
        from controllers import github_controller, connection_controller
        raw = connection_controller.get_connection_raw(connection_id, current_user.user_id)
        if raw and isinstance(raw, dict) and raw.get("github_registration"):
            # Extract installation info and attempt cleanup
            reg_data = raw.get("github_registration", {})
            if reg_data.get("selected_installation_id"):
                installation_id = reg_data["selected_installation_id"]
                installation_token = github_controller.get_installation_token(installation_id)
                if installation_token:
                    github_controller.delete_github_webhook(installation_token)
    except Exception as e:
        # Log but don't fail the connection deletion
        print(f"WARNING: Failed to cleanup GitHub webhook: {e}")

    deleted = connection_controller.delete_connection(
        connection_id=connection_id,
        user_id=current_user.user_id
    )
    
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connection not found"
        )


@router.post("/{connection_id}/github-installation/{installation_id}", status_code=status.HTTP_200_OK)
async def set_github_installation(
    connection_id: str,
    installation_id: str,
    current_user: TokenData = Depends(get_current_user)
) -> dict:
    """
    Store GitHub App installation ID after user authorizes the app.
    Called from GitHub App OAuth callback.
    
    **Path Parameters:**
    - `connection_id`: Connection ID
    - `installation_id`: GitHub App installation ID from webhook
    
    **Response:**
    ```json
    {
        "connection_id": "507f1f77bcf86cd799439011",
        "github_installation_id": "12345678"
    }
    ```
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
        "github_installation_id": installation_id
    }
