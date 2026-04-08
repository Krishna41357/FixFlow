"""
routes/__init__.py
API endpoint layer initialization.

Routes handle:
- HTTP request/response mapping
- Input validation (Pydantic models)
- Output serialization
- HTTP status codes and error responses
- JWT authentication via dependencies
"""

from .auth import router as auth_router
from .connections import router as connections_router
from .events import router as events_router
from .investigations import router as investigations_router
from .chats import router as chats_router
from .github import router as github_router

__all__ = [
    "auth_router",
    "connections_router",
    "events_router",
    "investigations_router",
    "chats_router",
    "github_router",
]
