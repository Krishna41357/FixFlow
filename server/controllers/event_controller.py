import os
import hmac
import hashlib
import json
from typing import List, Optional
from datetime import datetime, timezone
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv

from models.events import (
    DbtWebhookPayload, GitHubPRPayload, ManualQueryPayload,
    FailureEventCreate, FailureEventInDB
)

load_dotenv()

# MongoDB setup
mongo_uri = os.getenv("MONGO_URI")
if not mongo_uri:
    raise RuntimeError("MONGO_URI not set in environment")

client = MongoClient(mongo_uri)
db = client["rag_database"]
events_collection = db["events"]

# Webhook secrets
DBT_WEBHOOK_SECRET = os.getenv("DBT_WEBHOOK_SECRET", "")
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")


def _verify_dbt_signature(payload: str, signature: str) -> bool:
    """Verify dbt webhook signature."""
    if not DBT_WEBHOOK_SECRET:
        print("WARNING: DBT_WEBHOOK_SECRET not set")
        return True  # Allow if secret not configured
    
    expected_signature = hmac.new(
        DBT_WEBHOOK_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(signature, expected_signature)


def _verify_github_signature(signature: str, payload: bytes) -> bool:
    """Verify GitHub webhook signature (X-Hub-Signature-256)."""
    if not GITHUB_WEBHOOK_SECRET:
        print("WARNING: GITHUB_WEBHOOK_SECRET not set")
        return True  # Allow if secret not configured
    
    expected_signature = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(signature, expected_signature)


def handle_dbt_webhook(
    connection_id: str,
    user_id: str,
    payload: DbtWebhookPayload,
    signature: Optional[str] = None
) -> Optional[str]:
    """
    Validates DbtWebhookPayload signature, extracts failing node_id,
    creates FailureEvent, queues it. Returns event_id.
    """
    if not connection_id or not user_id:
        print("ERROR handle_dbt_webhook: Missing connection_id or user_id")
        return None
    
    user_id = str(user_id)
    connection_id = str(connection_id)
    
    # Verify signature if provided
    if signature:
        payload_json = json.dumps(payload.dict())
        if not _verify_dbt_signature(payload_json, signature):
            print("ERROR handle_dbt_webhook: Invalid signature")
            return None
    
    try:
        # Extract failing node_id from payload
        failing_node_id = payload.data.node_id if payload.data else None
        
        # Create failure event
        event = create_failure_event(
            user_id=user_id,
            connection_id=connection_id,
            event_type="dbt_run_failure",
            source_id=failing_node_id,
            failure_message=f"dbt model failed: {failing_node_id}",
            metadata={
                "dbt_run_id": payload.data.run_id if payload.data else None,
                "node_id": failing_node_id,
                "error_message": payload.data.error_message if payload.data else None
            }
        )
        
        return event
    except Exception as e:
        print(f"ERROR handle_dbt_webhook: {e}")
        return None


def handle_github_pr(
    connection_id: str,
    user_id: str,
    payload: GitHubPRPayload,
    signature: Optional[str] = None
) -> Optional[str]:
    """
    Validates GitHub App signature, parses changed files,
    creates FailureEvent with pr_number + pr_url. Returns event_id.
    """
    if not connection_id or not user_id:
        print("ERROR handle_github_pr: Missing connection_id or user_id")
        return None
    
    user_id = str(user_id)
    connection_id = str(connection_id)
    
    # Verify signature if provided
    if signature:
        payload_json = json.dumps(payload.dict())
        if not _verify_github_signature(signature, payload_json.encode()):
            print("ERROR handle_github_pr: Invalid signature")
            return None
    
    try:
        # Extract PR details
        pr_number = payload.pull_request.number if payload.pull_request else None
        pr_url = payload.pull_request.html_url if payload.pull_request else None
        
        # Create failure event
        event = create_failure_event(
            user_id=user_id,
            connection_id=connection_id,
            event_type="github_pr",
            source_id=pr_number,
            failure_message=f"GitHub PR opened: {pr_url}",
            metadata={
                "pr_number": pr_number,
                "pr_url": pr_url,
                "pr_title": payload.pull_request.title if payload.pull_request else None,
                "changed_files": []  # Parse from payload.pull_request.changed_files
            }
        )
        
        return event
    except Exception as e:
        print(f"ERROR handle_github_pr: {e}")
        return None


def handle_manual_query(
    user_id: str,
    payload: ManualQueryPayload
) -> Optional[str]:
    """
    Receives ManualQueryPayload from chat UI,
    creates FailureEvent, immediately starts investigation.
    Returns event_id.
    """
    if not user_id or not payload.asset_name:
        print("ERROR handle_manual_query: Missing user_id or asset_fqn")
        return None
    
    user_id = str(user_id)
    
    try:
        # Create failure event
        event = create_failure_event(
            user_id=user_id,
            connection_id=payload.connection_id,
            event_type="manual_query",
            source_id=payload.asset_name,
            failure_message=payload.question,
            metadata={
                "asset_name": payload.asset_name,
                "query": payload.question
            }
        )
        
        return event
    except Exception as e:
        print(f"ERROR handle_manual_query: {e}")
        return None


def create_failure_event(
    user_id: str,
    connection_id: str,
    event_type: str,
    source_id: str,
    failure_message: str,
    metadata: dict = None
) -> Optional[str]:
    """Internal. Inserts FailureEventInDB, returns event_id used to create the Investigation."""
    try:
        event_doc = {
            "user_id": str(user_id),
            "connection_id": str(connection_id),
            "event_type": event_type,
            "source_id": source_id,
            "failure_message": failure_message,
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc),
            "processed": False,
            "investigation_id": None
        }
        
        result = events_collection.insert_one(event_doc)
        return str(result.inserted_id)
    except Exception as e:
        print(f"ERROR create_failure_event: {e}")
        return None


def get_events_for_user(user_id: str, limit: int = 20) -> List[dict]:
    """Lists recent events — shown in the investigation history sidebar."""
    if not user_id:
        return []
    
    user_id = str(user_id)
    
    try:
        events = events_collection.find(
            {"user_id": user_id}
        ).sort("created_at", -1).limit(limit)
        
        result = []
        for event in events:
            result.append({
                "id": str(event["_id"]),
                "user_id": user_id,
                "event_type": event.get("event_type"),
                "source_id": event.get("source_id"),
                "failure_message": event.get("failure_message"),
                "created_at": event.get("created_at"),
                "processed": event.get("processed", False),
                "investigation_id": event.get("investigation_id")
            })
        
        return result
    except Exception as e:
        print(f"ERROR get_events_for_user: {e}")
        return []


def mark_event_processed(event_id: str, investigation_id: str) -> bool:
    """Mark an event as processed with associated investigation_id."""
    try:
        result = events_collection.update_one(
            {"_id": ObjectId(event_id)},
            {
                "$set": {
                    "processed": True,
                    "investigation_id": investigation_id,
                    "processed_at": datetime.now(timezone.utc)
                }
            }
        )
        return result.modified_count > 0
    except Exception as e:
        print(f"ERROR mark_event_processed: {e}")
        return False
