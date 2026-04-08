"""
base.py — shared types used across all Pipeline Autopsy schemas.
"""

from datetime import datetime, timezone
from typing import Optional, Any
from enum import Enum
from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """Always return timezone-aware UTC datetime. Replaces deprecated utcnow()."""
    return datetime.now(timezone.utc)


class PyObjectId(str):
    """
    MongoDB ObjectId ↔ Pydantic v2 compatible type.
    Accepts ObjectId or valid ObjectId string. Always serialises as str.
    """

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any):
        from pydantic_core import core_schema

        def validate(value: Any) -> str:
            if isinstance(value, ObjectId):
                return str(value)
            if isinstance(value, str) and ObjectId.is_valid(value):
                return value
            raise ValueError(f"Invalid ObjectId: {value!r}")

        return core_schema.with_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(str),
        )


class MongoBase(BaseModel):
    """
    Base for all documents stored in MongoDB.
    Handles _id ↔ id aliasing and datetime serialisation.
    """

    id: Optional[PyObjectId] = Field(default=None, alias="_id")

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat(),
        },
    )


# ── Shared enums ──────────────────────────────────────────────────────────────

class EventType(str, Enum):
    """The three ways a failure investigation can be triggered."""
    DBT_WEBHOOK   = "dbt_webhook"
    GITHUB_PR     = "github_pr"
    MANUAL_QUERY  = "manual_query"


class InvestigationStatus(str, Enum):
    """Lifecycle of a diagnosis session."""
    PENDING    = "pending"     # received, not yet processed
    RUNNING    = "running"     # lineage traversal in progress
    COMPLETED  = "completed"   # root cause found, AI response ready
    FAILED     = "failed"      # traversal or AI call errored out


class SeverityLevel(str, Enum):
    """How badly an asset is affected downstream."""
    CRITICAL = "critical"   # production dashboard or SLA asset broken
    HIGH     = "high"       # major model broken
    MEDIUM   = "medium"     # non-critical model broken
    LOW      = "low"        # test-only asset


class AssetType(str, Enum):
    """OpenMetadata entity types we traverse."""
    TABLE     = "table"
    VIEW      = "view"
    DASHBOARD = "dashboard"
    PIPELINE  = "pipeline"
    TOPIC     = "topic"