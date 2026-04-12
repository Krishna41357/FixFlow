"""
Investigation Routes
Manages root cause analysis investigations and results.
"""

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from typing import List

from models.investigations import InvestigationResponse, InvestigationListItem
from controllers import investigation_controller, connection_controller
from routes.auth import get_current_user
from models.users import TokenData

router = APIRouter(prefix="/investigations", tags=["investigations"])


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_investigation(
    connection_id: str,
    event_id: str,
    failure_message: str,
    current_user: TokenData = Depends(get_current_user),
    background_tasks: BackgroundTasks = BackgroundTasks()
) -> dict:
    """
    Create a new investigation for a failure event.
    Starts the investigation pipeline asynchronously.
    
    **Query Parameters:**
    - `user_id`: Target user ID (must match current user)
    - `connection_id`: Connection ID to use
    - `event_id`: Event ID triggering investigation
    - `failure_message`: Description of the failure
    
    **Response:**
    ```json
    {
        "investigation_id": "507f1f77bcf86cd799439011",
        "status": "PENDING",
        "created_at": "2024-01-15T10:30:00Z"
    }
    ```
    
    **Note:** Investigation runs asynchronously. Poll /investigations/{id} for status.
    """
    # Verify connection exists
    connection = connection_controller.get_connection_by_id(
        connection_id=connection_id,
        user_id=current_user.user_id
    )
    # connection can be None for testing without OpenMetadata
    
    # Create investigation
    investigation_id = investigation_controller.create_investigation(
        user_id=current_user.user_id,
        connection_id=connection_id,
        event_id=event_id,
        failure_message=failure_message
    )
    
    if not investigation_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create investigation"
        )
    
    # Run investigation in background
    background_tasks.add_task(
        investigation_controller.run_investigation,
        investigation_id=investigation_id,
        user_id=current_user.user_id,
        connection_id=connection_id,
        openmetadata_url=connection.openmetadata_host if connection else "http://localhost:8585",
        openmetadata_token=connection.openmetadata_token if connection else ""
    )
    
    return {
        "investigation_id": investigation_id,
        "status": "PENDING",
        "message": "Investigation started. Check status for updates."
    }


@router.get("/{investigation_id}", response_model=InvestigationResponse)
async def get_investigation(
    investigation_id: str,
    current_user: TokenData = Depends(get_current_user)
) -> InvestigationResponse:
    """
    Get investigation details and results.
    
    **Path Parameters:**
    - `investigation_id`: Investigation ID
    
    **Response:**
    ```json
    {
        "id": "507f1f77bcf86cd799439011",
        "status": "COMPLETED",
        "failure_message": "dbt model failed: ...",
        "root_cause": {
            "root_cause": "Schema column was dropped",
            "responsible_asset": "source_table",
            "suggested_fix": "ALTER TABLE ...",
            "impact_summary": "Affects 3 downstream models",
            "confidence_score": 0.92
        },
        "created_at": "2024-01-15T10:30:00Z",
        "completed_at": "2024-01-15T10:35:00Z",
        "processing_time_ms": 300000
    }
    ```
    """
    investigation = investigation_controller.get_investigation(
        investigation_id=investigation_id,
        user_id=current_user.user_id
    )
    
    if not investigation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Investigation not found"
        )
    
    return investigation


@router.get("", response_model=List[InvestigationListItem])
async def list_investigations(
    current_user: TokenData = Depends(get_current_user),
    limit: int = 20,
    skip: int = 0
) -> List[InvestigationListItem]:
    """
    List recent investigations for current user.
    Lightweight version suitable for sidebar.
    
    **Query Parameters:**
    - `limit`: Max results (default: 20, max: 100)
    - `skip`: Pagination offset (default: 0)
    
    **Response:**
    ```json
    [
        {
            "id": "507f1f77bcf86cd799439011",
            "status": "COMPLETED",
            "failure_message": "dbt model failed: ...",
            "created_at": "2024-01-15T10:30:00Z",
            "completed_at": "2024-01-15T10:35:00Z"
        }
    ]
    ```
    """
    if limit > 100:
        limit = 100
    
    return investigation_controller.list_investigations(
        user_id=current_user.user_id,
        limit=limit
    )


@router.get("/{investigation_id}/status", response_model=dict)
async def get_investigation_status(
    investigation_id: str,
    current_user: TokenData = Depends(get_current_user)
) -> dict:
    """
    Get investigation status without full details.
    Fast endpoint for polling updates.
    
    **Path Parameters:**
    - `investigation_id`: Investigation ID
    
    **Response:**
    ```json
    {
        "investigation_id": "507f1f77bcf86cd799439011",
        "status": "AI_ANALYSIS",
        "progress": 75,
        "message": "Analyzing with AI model..."
    }
    ```
    
    **Status Values:**
    - `PENDING` - Waiting to start
    - `LINEAGE_TRAVERSAL` - Mapping data lineage
    - `CONTEXT_BUILDING` - Preparing AI context
    - `AI_ANALYSIS` - Running AI analysis
    - `COMPLETED` - Analysis complete
    - `FAILED` - Analysis failed
    """
    investigation = investigation_controller.get_investigation(
        investigation_id=investigation_id,
        user_id=current_user.user_id
    )
    
    if not investigation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Investigation not found"
        )
    
    # Calculate progress based on status
    status_progress = {
        "PENDING": 10,
        "LINEAGE_TRAVERSAL": 30,
        "CONTEXT_BUILDING": 50,
        "AI_ANALYSIS": 75,
        "COMPLETED": 100,
        "FAILED": 0
    }
    
    progress = status_progress.get(investigation.status, 0)
    
    return {
        "investigation_id": investigation_id,
        "status": investigation.status,
        "progress": progress,
        "processing_time_ms": investigation.processing_time_ms,
        "completed_at": investigation.completed_at
    }
