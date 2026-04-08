"""
models/__init__.py
Pipeline Autopsy — all schemas in one import.

Usage:
    from schemas import FailureEventCreate, InvestigationResponse, PRAnalysis
"""

from .base import (
    PyObjectId,
    MongoBase,
    EventType,
    InvestigationStatus,
    SeverityLevel,
    AssetType,
    utc_now,
)

from .users import (
    ConnectionCreate,
    ConnectionResponse,
    ConnectionInDB,
    UserCreate,
    UserLogin,
    UserInDB,
    UserResponse,
    Token,
    TokenData,
)

from .events import (
    DbtTestFailure,
    DbtWebhookPayload,
    GitHubPRPayload,
    ManualQueryPayload,
    AffectedAsset,
    FailureEventCreate,
    FailureEventInDB,
    FailureEventResponse,
)

from .lineage import (
    ColumnDiff,
    SchemaDiff,
    LineageNode,
    LineageEdge,
    LineageSubgraph,
)

from .investigations import (
    SuggestedFix,
    RootCause,
    InvestigationInDB,
    InvestigationResponse,
    InvestigationListItem,
)

from .github import (
    ChangedAsset,
    PRWebhookEvent,
    ImpactedAsset,
    PRAnalysis,
    PRAnalysisInDB,
)

from .chat import (
    ChatMessage,
    ChatSessionInDB,
    ChatSessionResponse,
    ChatSessionListItem,
    ChatQueryRequest,
    ChatQueryResponse,
)

__all__ = [
    # base
    "PyObjectId", "MongoBase", "EventType", "InvestigationStatus",
    "SeverityLevel", "AssetType", "utc_now",
    # users
    "ConnectionCreate", "ConnectionResponse", "ConnectionInDB",
    "UserCreate", "UserLogin", "UserInDB", "UserResponse",
    "Token", "TokenData",
    # events
    "DbtTestFailure", "DbtWebhookPayload", "GitHubPRPayload",
    "ManualQueryPayload", "AffectedAsset",
    "FailureEventCreate", "FailureEventInDB", "FailureEventResponse",
    # lineage
    "ColumnDiff", "SchemaDiff", "LineageNode", "LineageEdge", "LineageSubgraph",
    # investigations
    "SuggestedFix", "RootCause",
    "InvestigationInDB", "InvestigationResponse", "InvestigationListItem",
    # github
    "ChangedAsset", "PRWebhookEvent", "ImpactedAsset", "PRAnalysis", "PRAnalysisInDB",
    # chat
    "ChatMessage", "ChatSessionInDB", "ChatSessionResponse",
    "ChatSessionListItem", "ChatQueryRequest", "ChatQueryResponse",
]