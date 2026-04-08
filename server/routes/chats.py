"""
Chat Routes
Manages chat sessions and conversational queries.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List

from models.chat import (
    ChatQueryRequest, ChatQueryResponse, ChatSessionResponse,
    ChatSessionListItem
)
from controllers import chat_controller, investigation_controller
from routes.auth import get_current_user
from models.users import TokenData

router = APIRouter(prefix="/chats", tags=["chat"])


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_session(
    title: str = "New Session",
    current_user: TokenData = Depends(get_current_user)
) -> dict:
    """
    Create a new chat session.
    
    **Query Parameters:**
    - `title`: Session title (optional, default: "New Session")
    
    **Response:**
    ```json
    {
        "session_id": "507f1f77bcf86cd799439011",
        "user_id": "507f1f77bcf86cd799439010",
        "title": "New Session",
        "created_at": "2024-01-15T10:30:00Z"
    }
    ```
    """
    session_id = chat_controller.create_session(
        user_id=current_user.user_id,
        title=title
    )
    
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create session"
        )
    
    return {
        "session_id": session_id,
        "user_id": current_user.user_id,
        "title": title,
        "message_count": 0
    }


@router.get("", response_model=List[ChatSessionListItem])
async def list_sessions(
    current_user: TokenData = Depends(get_current_user),
    limit: int = 20,
    skip: int = 0
) -> List[ChatSessionListItem]:
    """
    List chat sessions for current user.
    Lightweight version for sidebar display.
    
    **Query Parameters:**
    - `limit`: Max results (default: 20, max: 100)
    - `skip`: Pagination offset (default: 0)
    
    **Response:**
    ```json
    [
        {
            "id": "507f1f77bcf86cd799439011",
            "title": "Orders schema issue",
            "message_count": 5,
            "last_message": "What changed in the orders table?",
            "created_at": "2024-01-15T10:30:00Z",
            "updated_at": "2024-01-15T10:35:00Z"
        }
    ]
    ```
    """
    if limit > 100:
        limit = 100
    
    return chat_controller.list_sessions(
        user_id=current_user.user_id,
        limit=limit,
        skip=skip
    )


@router.get("/{session_id}", response_model=ChatSessionResponse)
async def get_session(
    session_id: str,
    current_user: TokenData = Depends(get_current_user)
) -> ChatSessionResponse:
    """
    Get full chat session with message history.
    
    **Path Parameters:**
    - `session_id`: Session ID
    
    **Response:**
    ```json
    {
        "id": "507f1f77bcf86cd799439011",
        "user_id": "507f1f77bcf86cd799439010",
        "title": "Orders schema issue",
        "messages": [
            {
                "role": "user",
                "content": "Why is my orders table failing?",
                "timestamp": "2024-01-15T10:30:00Z"
            },
            {
                "role": "assistant",
                "content": "Starting investigation...",
                "timestamp": "2024-01-15T10:30:05Z"
            }
        ],
        "investigation_id": "507f1f77bcf86cd799439012",
        "message_count": 2
    }
    ```
    """
    session = chat_controller.get_session(
        session_id=session_id,
        user_id=current_user.user_id
    )
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    
    return session


@router.post("/{session_id}/query", response_model=ChatQueryResponse)
async def send_query(
    session_id: str,
    query: ChatQueryRequest,
    current_user: TokenData = Depends(get_current_user)
) -> ChatQueryResponse:
    """
    Send a message in a chat session.
    
    **Path Parameters:**
    - `session_id`: Session ID
    
    **Request Body:**
    ```json
    {
        "message": "Why is my orders table failing?"
    }
    ```
    
    **Response:**
    ```json
    {
        "session_id": "507f1f77bcf86cd799439011",
        "message": "Based on the lineage analysis, the orders table is failing because...",
        "is_followup": false,
        "investigation_id": "507f1f77bcf86cd799439012"
    }
    ```
    
    **Behavior:**
    - If message is related to existing investigation: answers immediately
    - If message asks about new asset: triggers new investigation
    - All messages are appended to session history
    """
    # Verify session exists
    session = chat_controller.get_session(
        session_id=session_id,
        user_id=current_user.user_id
    )
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    
    # Get investigation if exists
    investigation_result = None
    if session.investigation_id:
        investigation = investigation_controller.get_investigation(
            investigation_id=session.investigation_id,
            user_id=current_user.user_id
        )
        if investigation and investigation.root_cause:
            investigation_result = investigation.root_cause.dict()
    
    # Handle query
    response = chat_controller.handle_query(
        session_id=session_id,
        user_id=current_user.user_id,
        query=query,
        investigation_result=investigation_result
    )
    
    if not response:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to process query"
        )
    
    return response


@router.put("/{session_id}/title", response_model=dict)
async def update_session_title(
    session_id: str,
    title: str,
    current_user: TokenData = Depends(get_current_user)
) -> dict:
    """
    Update session title.
    
    **Path Parameters:**
    - `session_id`: Session ID
    
    **Query Parameters:**
    - `title`: New title
    
    **Response:**
    ```json
    {
        "session_id": "507f1f77bcf86cd799439011",
        "title": "Orders schema issue"
    }
    ```
    """
    success = chat_controller.update_session_title(
        session_id=session_id,
        user_id=current_user.user_id,
        title=title
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    
    return {
        "session_id": session_id,
        "title": title
    }


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    current_user: TokenData = Depends(get_current_user)
) -> None:
    """
    Delete a chat session.
    
    **Path Parameters:**
    - `session_id`: Session ID
    """
    success = chat_controller.delete_session(
        session_id=session_id,
        user_id=current_user.user_id
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )


@router.patch("/{chat_id}")
async def update_chat(
    chat_id: str,
    chat_update: ChatUpdate,
    current_user=Depends(get_current_user)
):
    """
    Update chat title.
    
    Request body:
    {
        "title": "New Title"
    }
    """
    # Validate user_id exists in token
    if not current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: user_id not found. Please log in again."
        )
    
    user_id = str(current_user.user_id)  # Ensure it's a string
    
    print(f"DEBUG update_chat: chat_id={chat_id}, user_id={user_id}")
    
    # Verify chat exists and belongs to user
    chat = get_chat_by_id(chat_id=chat_id, user_id=user_id)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found or you don't have access to it"
        )
    
    success = update_chat_title(
        chat_id=chat_id,
        user_id=user_id,
        title=chat_update.title
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update chat title"
        )
    
    return {
        "message": "Chat title updated successfully",
        "title": chat_update.title
    }


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_endpoint(
    chat_id: str,
    current_user=Depends(get_current_user)
):
    """
    Delete a chat permanently.
    """
    # Validate user_id exists in token
    if not current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: user_id not found. Please log in again."
        )
    
    user_id = str(current_user.user_id)  # Ensure it's a string
    
    print(f"DEBUG delete_chat: chat_id={chat_id}, user_id={user_id}")
    
    success = delete_chat(
        chat_id=chat_id,
        user_id=user_id
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found or you don't have access to it"
        )
    
    return None


# DEBUG ENDPOINT - Remove in production
@router.get("/debug/token-info")
async def debug_token_info(current_user=Depends(get_current_user)):
    """Debug endpoint to check token contents"""
    return {
        "email": current_user.email,
        "user_id": current_user.user_id,
        "user_id_type": type(current_user.user_id).__name__ if current_user.user_id else "None",
        "user_id_is_none": current_user.user_id is None,
        "user_id_value": str(current_user.user_id) if current_user.user_id else "NULL"
    }