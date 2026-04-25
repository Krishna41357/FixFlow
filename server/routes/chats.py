"""
Chat Routes
Manages chat sessions and conversational queries.
"""

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from typing import List

from models.chat import (
    ChatQueryRequest, ChatQueryResponse, ChatSessionResponse,
    ChatSessionListItem
)
from controllers import chat_controller, investigation_controller, connection_controller, event_controller
from routes.auth import get_current_user
from models.users import TokenData
from controllers import connection_controller

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
    background_tasks: BackgroundTasks,
    current_user: TokenData = Depends(get_current_user)
) -> ChatQueryResponse:
    """
    Send a message in a chat session.
    If it's a new investigation (not a follow-up), creates an event + investigation
    and runs the pipeline in the background, then links investigation_id to the session.
    """
    session = chat_controller.get_session(session_id=session_id, user_id=current_user.user_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # --- Check if follow-up with existing investigation ---
    investigation_result = None
    existing_investigation_id = getattr(session, 'investigation_id', None)

    if existing_investigation_id:
        investigation = investigation_controller.get_investigation(
            investigation_id=existing_investigation_id,
            user_id=current_user.user_id
        )
        if investigation and investigation.root_cause:
            investigation_result = investigation.root_cause.model_dump()

    # --- If no existing investigation, start a new one ---
    new_investigation_id = None
    if not existing_investigation_id:
        # Get the user's active connection
        connections = connection_controller.get_user_connections(current_user.user_id)
        connection_response = connections[0] if connections else None
        connection = None
        if connection_response:
          connection = connection_controller.get_connection_by_id(
            connection_id=connection_response.id,
            user_id=current_user.user_id
        )

        if connection:
            # Create a manual event
            from models.events import ManualQueryPayload
            payload = ManualQueryPayload(
                asset_name=getattr(query, 'asset_name', query.message),
                question=query.message,
                connection_id=connection.id
            )
            event_id = event_controller.handle_manual_query(
                user_id=current_user.user_id,
                payload=payload
            )

            if event_id:
                # Create investigation record (returns immediately)
                new_investigation_id = investigation_controller.create_investigation(
                    user_id=current_user.user_id,
                    connection_id=connection.id,
                    event_id=event_id,
                    failure_message=query.message,
                    asset_fqn=getattr(query, 'asset_fqn', None)
                )

                if new_investigation_id:
                    # Link investigation to session immediately so frontend can poll
                    chat_controller.append_message(
                        session_id=session_id,
                        user_id=current_user.user_id,
                        role="system",
                        content="investigation_linked",
                        investigation_id=new_investigation_id
                    )

                    # Run the heavy pipeline in the background
                    background_tasks.add_task(
                        investigation_controller.run_investigation,
                        investigation_id=new_investigation_id,
                        user_id=current_user.user_id,
                        connection_id=connection.id,
                        openmetadata_url=connection.openmetadata_host,
                        openmetadata_token=connection.openmetadata_token
                    )

    # --- Build response via chat controller ---
    response = chat_controller.handle_query(
        session_id=session_id,
        user_id=current_user.user_id,
        query=query,
        investigation_result=investigation_result
    )

    if not response:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to process query")

    # Override investigation_id in response so frontend gets it immediately
    if new_investigation_id:
        response.investigation_id = new_investigation_id

    return response


@router.get("/{session_id}/investigation-status", response_model=dict)
async def get_session_investigation_status(
    session_id: str,
    current_user: TokenData = Depends(get_current_user)
) -> dict:
    """
    Polls the investigation status linked to this chat session.
    Frontend can call this every 2s to know when analysis is done.
    """
    session = chat_controller.get_session(session_id=session_id, user_id=current_user.user_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    investigation_id = getattr(session, 'investigation_id', None)
    if not investigation_id:
        return {"status": "NO_INVESTIGATION", "investigation_id": None, "progress": 0}

    investigation = investigation_controller.get_investigation(
        investigation_id=investigation_id,
        user_id=current_user.user_id
    )
    if not investigation:
        return {"status": "NOT_FOUND", "investigation_id": investigation_id, "progress": 0}

    status_progress = {
        "PENDING": 10,
        "LINEAGE_TRAVERSAL": 30,
        "CONTEXT_BUILDING": 50,
        "AI_ANALYSIS": 75,
        "COMPLETED": 100,
        "FAILED": 0
    }

    return {
        "investigation_id": investigation_id,
        "status": investigation.status,
        "progress": status_progress.get(investigation.status, 0),
        "root_cause": investigation.root_cause.model_dump() if investigation.root_cause else None,
        "processing_time_ms": investigation.processing_time_ms
    }


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