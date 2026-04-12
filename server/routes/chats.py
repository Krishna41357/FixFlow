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
    """Create a new chat session."""
    session_id = chat_controller.create_session(
        user_id=current_user.user_id,
        title=title
    )
    if not session_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to create session")
    return {"session_id": session_id, "user_id": current_user.user_id, "title": title, "message_count": 0}


@router.get("", response_model=List[ChatSessionListItem])
async def list_sessions(
    current_user: TokenData = Depends(get_current_user),
    limit: int = 20,
    skip: int = 0
) -> List[ChatSessionListItem]:
    """List chat sessions for current user."""
    if limit > 100:
        limit = 100
    return chat_controller.list_sessions(user_id=current_user.user_id, limit=limit, skip=skip)


@router.get("/debug/token-info")
async def debug_token_info(current_user=Depends(get_current_user)):
    """Debug endpoint to check token contents."""
    return {
        "email": current_user.email,
        "user_id": current_user.user_id,
        "user_id_type": type(current_user.user_id).__name__ if current_user.user_id else "None",
        "user_id_value": str(current_user.user_id) if current_user.user_id else "NULL"
    }


@router.get("/{session_id}", response_model=ChatSessionResponse)
async def get_session(
    session_id: str,
    current_user: TokenData = Depends(get_current_user)
) -> ChatSessionResponse:
    """Get full chat session with message history."""
    session = chat_controller.get_session(session_id=session_id, user_id=current_user.user_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


@router.post("/{session_id}/query", response_model=ChatQueryResponse)
async def send_query(
    session_id: str,
    query: ChatQueryRequest,
    current_user: TokenData = Depends(get_current_user)
) -> ChatQueryResponse:
    """Send a message in a chat session."""
    session = chat_controller.get_session(session_id=session_id, user_id=current_user.user_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    investigation_result = None
    if session.investigation_id:
        investigation = investigation_controller.get_investigation(
            investigation_id=session.investigation_id,
            user_id=current_user.user_id
        )
        if investigation and investigation.root_cause:
            investigation_result = investigation.root_cause.model_dump()

    response = chat_controller.handle_query(
        session_id=session_id,
        user_id=current_user.user_id,
        query=query,
        investigation_result=investigation_result
    )
    if not response:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to process query")
    return response


@router.put("/{session_id}/title", response_model=dict)
async def update_session_title(
    session_id: str,
    title: str,
    current_user: TokenData = Depends(get_current_user)
) -> dict:
    """Update session title."""
    success = chat_controller.update_session_title(
        session_id=session_id, user_id=current_user.user_id, title=title
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return {"session_id": session_id, "title": title}


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    current_user: TokenData = Depends(get_current_user)
) -> None:
    """Delete a chat session."""
    success = chat_controller.delete_session(session_id=session_id, user_id=current_user.user_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")