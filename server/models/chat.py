"""
chat.py — Chat session schemas for Pipeline Autopsy's manual query UI.

Unlike KS-RAG, chat here is NOT the primary interface — it's one of
three trigger surfaces. A ChatSession wraps a manual investigation:
the user types an asset name, we run the full pipeline, and return
the structured diagnosis in a conversational format.

We keep message history so users can ask follow-up questions
about the same investigation (e.g. "who owns that asset?",
"show me the SQL that caused this").
"""

from typing import Optional, List, Literal
from pydantic import BaseModel, Field, computed_field

from .base import MongoBase, utc_now
from .investigations import InvestigationResponse


# ── Messages ──────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    """A single turn in the chat session."""
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=5000)
    # For assistant messages — links back to the investigation
    investigation_id: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: utc_now().isoformat())


# ── Session ───────────────────────────────────────────────────────────────────

class ChatSessionInDB(MongoBase):
    """
    Chat session document stored in MongoDB.
    One session = one investigation triggered via the chat UI.
    Follow-up questions are additional turns in the same session.
    """
    user_id: str
    connection_id: str
    title: str = Field(..., max_length=200)
    messages: List[ChatMessage] = Field(default_factory=list)
    # The investigation this session is anchored to
    investigation_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())


class ChatSessionResponse(BaseModel):
    id: str
    title: str
    messages: List[ChatMessage]
    investigation: Optional[InvestigationResponse] = None
    investigation_id: Optional[str] = None    # ← add this
    created_at: str
    updated_at: str

    @computed_field
    @property
    def message_count(self) -> int:
        return len(self.messages)


class ChatSessionListItem(BaseModel):
    """Compact session for sidebar list — no messages, no investigation."""
    id: str
    title: str
    message_count: int
    last_message_preview: Optional[str] = Field(
        None, max_length=100,
        description="First 100 chars of the last message"
    )
    created_at: str
    updated_at: str


# ── Request / response for the chat endpoint ──────────────────────────────────

class ChatQueryRequest(BaseModel):
    """What the frontend sends when the user submits a message."""
    message: str = Field(..., min_length=1, max_length=500)
    connection_id: str
    # If continuing an existing session, provide its ID
    session_id: Optional[str] = None


class ChatQueryResponse(BaseModel):
    session_id: str
    message: str
    is_followup: bool = False
    investigation_id: Optional[str] = None