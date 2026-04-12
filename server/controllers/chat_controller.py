import os
from typing import List, Optional
from datetime import datetime, timezone
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv

from models.chat import (
    ChatSessionInDB, ChatMessage, ChatQueryRequest,
    ChatQueryResponse, ChatSessionResponse, ChatSessionListItem
)

load_dotenv()

# MongoDB setup
mongo_uri = os.getenv("MONGO_URI")
if not mongo_uri:
    raise RuntimeError("MONGO_URI not set in environment")

client = MongoClient(mongo_uri)
db = client["rag_database"]
sessions_collection = db["chat_sessions"]


def create_session(user_id: str, title: str = "New Session") -> Optional[str]:
    """
    Creates ChatSessionInDB.
    Called when user sends first message in a new conversation.
    Returns session_id.
    """
    if not user_id:
        print("ERROR create_session: user_id required")
        return None
    
    user_id = str(user_id)
    
    try:
        session_doc = {
            "user_id": user_id,
            "title": title or "New Session",
            "messages": [],
            "investigation_id": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        }
        
        result = sessions_collection.insert_one(session_doc)
        session_id = str(result.inserted_id)
        
        print(f"DEBUG create_session: Created session {session_id} for user {user_id}")
        return session_id
    except Exception as e:
        print(f"ERROR create_session: {e}")
        return None


def handle_query(
    session_id: str,
    user_id: str,
    query: ChatQueryRequest,
    investigation_result: Optional[dict] = None
) -> Optional[ChatQueryResponse]:
    """
    Main function. Receives ChatQueryRequest →
    triggers investigation OR answers follow-up →
    returns ChatQueryResponse.
    """
    if not session_id or not user_id:
        print("ERROR handle_query: Missing session_id or user_id")
        return None
    
    user_id = str(user_id)
    session_id = str(session_id)
    
    try:
        # Append user message to session
        append_message(session_id, user_id, "user", query.message)
        
        # Check if this is a follow-up question
        session = sessions_collection.find_one({
            "_id": ObjectId(session_id),
            "user_id": user_id
        })
        
        if not session:
            print(f"ERROR handle_query: Session {session_id} not found")
            return None
        
        if is_followup_question(query.message, len(session.get("messages", [])) > 1):
            # Answer follow-up from existing investigation
            response_text = answer_followup(query.message, investigation_result)
        else:
            # Trigger new investigation
            response_text = f"Starting investigation for: {query.message}"
        
        # Append assistant response
        append_message(session_id, user_id, "assistant", response_text)
        
        return ChatQueryResponse(
            session_id=session_id,
            message=response_text,
            is_followup=is_followup_question(query.message, len(session.get("messages", [])) > 2),
            investigation_id=session.get("investigation_id")
        )
    except Exception as e:
        print(f"ERROR handle_query: {e}")
        return None


def is_followup_question(message: str, has_history: bool) -> bool:
    """
    Checks if the message is asking about existing investigation data
    (owner? fix? SQL?) vs a new asset query.
    """
    if not has_history:
        return False
    
    followup_keywords = [
        "what", "who", "when", "why", "how",
        "can you", "could you", "explain", "tell me",
        "show me", "more details", "about", "this",
        "that", "it", "from the", "of the"
    ]
    
    message_lower = message.lower()
    
    for keyword in followup_keywords:
        if keyword in message_lower:
            return True
    
    return False


def answer_followup(message: str, investigation_result: Optional[dict]) -> str:
    """Answers from existing investigation.root_cause without re-traversing lineage."""
    if not investigation_result:
        return "No investigation data available. Please start a new query."
    
    root_cause = investigation_result.get("root_cause", {})
    
    # Simple heuristic-based answers
    message_lower = message.lower()
    
    if "fix" in message_lower or "solution" in message_lower:
        return root_cause.get("suggested_fix", "Unable to determine a fix.")
    
    if "cause" in message_lower or "why" in message_lower:
        return root_cause.get("root_cause", "Unable to determine root cause.")
    
    if "impact" in message_lower or "affected" in message_lower:
        return root_cause.get("impact_summary", "Impact assessment not available.")
    
    if "sql" in message_lower or "code" in message_lower:
        return root_cause.get("suggested_fix", "No SQL recommendations available.")
    
    # Default response
    summary = f"""**Root Cause:** {root_cause.get('root_cause', 'N/A')}

**Responsible Asset:** {root_cause.get('responsible_asset', 'N/A')}

**Suggested Fix:** {root_cause.get('suggested_fix', 'N/A')}

**Impact Summary:** {root_cause.get('impact_summary', 'N/A')}"""
    
    return summary


def append_message(
    session_id: str,
    user_id: str,
    role: str,
    content: str,
    investigation_id: Optional[str] = None
) -> bool:
    """Adds a ChatMessage turn to the session. Updates updated_at."""
    if not session_id or not user_id:
        print("ERROR append_message: Missing session_id or user_id")
        return False
    
    user_id = str(user_id)
    session_id = str(session_id)
    
    try:
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc)
        }
        
        update_data = {
            "$push": {"messages": message},
            "$set": {"updated_at": datetime.now(timezone.utc)}
        }
        
        if investigation_id:
            update_data["$set"]["investigation_id"] = investigation_id
        
        result = sessions_collection.update_one(
            {"_id": ObjectId(session_id), "user_id": user_id},
            update_data
        )
        
        return result.modified_count > 0
    except Exception as e:
        print(f"ERROR append_message: {e}")
        return False


def get_session(session_id: str, user_id: str) -> Optional[ChatSessionResponse]:
    """Returns full ChatSessionResponse with messages + linked investigation."""
    if not session_id or not user_id:
        return None
    
    user_id = str(user_id)
    
    try:
        session = sessions_collection.find_one({
            "_id": ObjectId(session_id),
            "user_id": user_id
        })
        
        if not session:
            print(f"ERROR get_session: Session {session_id} not found")
            return None
        
        messages = []
        for msg in session.get("messages", []):
            messages.append(ChatMessage(
                role=msg.get("role", "user"),
                content=msg.get("content", ""),
                timestamp=str(msg.get("timestamp", datetime.now(timezone.utc).isoformat()))
            ))
        
        return ChatSessionResponse(
            id=str(session["_id"]),
            title=session.get("title", "New Session"),
            messages=messages,
            investigation_id=session.get("investigation_id"),
            created_at=str(session.get("created_at", datetime.now(timezone.utc).isoformat())),
            updated_at=str(session.get("updated_at", datetime.now(timezone.utc).isoformat())),
            message_count=len(messages)
        )
    except Exception as e:
        print(f"ERROR get_session: {e}")
        return None


def list_sessions(user_id: str, skip: int = 0, limit: int = 20) -> List[ChatSessionListItem]:
    """Returns ChatSessionListItem list for sidebar. No messages payload."""
    if not user_id:
        return []
    
    user_id = str(user_id)
    
    try:
        sessions = sessions_collection.find(
            {"user_id": user_id}
        ).sort("updated_at", -1).skip(skip).limit(limit)
        
        result = []
        for session in sessions:
            messages = session.get("messages", [])
            last_message = None
            
            # Get last user message as preview
            if messages:
                user_messages = [m for m in messages if m.get("role") == "user"]
                if user_messages:
                    last_message = (user_messages[-1].get("content") or "")[:100]
            
            result.append(ChatSessionListItem(
                id=str(session["_id"]),
                title=session.get("title", "New Session"),
                message_count=len(messages),
                last_message_preview=last_message,
                created_at=str(session.get("created_at", datetime.now(timezone.utc).isoformat())),
                updated_at=str(session.get("updated_at", datetime.now(timezone.utc).isoformat()))
            ))
        
        return result
    except Exception as e:
        print(f"ERROR list_sessions: {e}")
        return []


def generate_title(first_message: str) -> str:
    """Auto-titles session from first message. Same logic as generate_chat_title()."""
    if not first_message:
        return "New Session"
    
    title = first_message.strip()[:50]
    if len(first_message) > 50:
        title += "..."
    
    return title or "New Session"


def update_session_title(session_id: str, user_id: str, title: str) -> bool:
    """Update session title."""
    if not session_id or not user_id:
        return False
    
    user_id = str(user_id)
    
    try:
        result = sessions_collection.update_one(
            {"_id": ObjectId(session_id), "user_id": user_id},
            {
                "$set": {
                    "title": title or "New Session",
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        return result.modified_count > 0
    except Exception as e:
        print(f"ERROR update_session_title: {e}")
        return False


def delete_session(session_id: str, user_id: str) -> bool:
    """Delete a session."""
    if not session_id or not user_id:
        return False
    
    user_id = str(user_id)
    
    try:
        result = sessions_collection.delete_one({
            "_id": ObjectId(session_id),
            "user_id": user_id
        })
        
        return result.deleted_count > 0
    except Exception as e:
        print(f"ERROR delete_session: {e}")
        return False
