"""
base.py - shared types used across all Pipeline Autopsy schemas.
"""

from datetime import datetime, timezone
from typing import Optional, Any
from enum import Enum
from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PyObjectId(str):
    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any):
        from pydantic_core import core_schema

        def validate(value: Any) -> str:
            if isinstance(value, ObjectId):
                return str(value)
            if isinstance(value, str) and ObjectId.is_valid(value):
                return value
            raise ValueError(f"Invalid ObjectId: {value!r}")

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(str),
        )


class MongoBase(BaseModel):
    id: Optional[PyObjectId] = Field(default=None, alias="_id")

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat(),
        },
    )


class EventType(str, Enum):
    DBT_WEBHOOK  = "dbt_webhook"
    GITHUB_PR    = "github_pr"
    MANUAL_QUERY = "manual_query"


class InvestigationStatus(str, Enum):
    PENDING           = "pending"
    LINEAGE_TRAVERSAL = "lineage_traversal"
    CONTEXT_BUILDING  = "context_building"
    AI_ANALYSIS       = "ai_analysis"
    RUNNING           = "running"
    COMPLETED         = "completed"
    FAILED            = "failed"


class SeverityLevel(str, Enum):
    CRITICAL = "critical"
    HIGH     = "high"
    MEDIUM   = "medium"
    LOW      = "low"


class AssetType(str, Enum):
    TABLE     = "table"
    VIEW      = "view"
    DASHBOARD = "dashboard"
    PIPELINE  = "pipeline"
    TOPIC     = "topic"