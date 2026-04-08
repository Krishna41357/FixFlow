import os
import json
import requests
from typing import List, Optional
from datetime import datetime, timezone
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv

from models.investigations import (
    InvestigationCreate, InvestigationInDB, InvestigationResponse,
    InvestigationListItem, RootCause, InvestigationStatus
)
from models.lineage import LineageSubgraph
from controllers import lineage_controller, event_controller

load_dotenv()

# MongoDB setup
mongo_uri = os.getenv("MONGO_URI")
if not mongo_uri:
    raise RuntimeError("MONGO_URI not set in environment")

client = MongoClient(mongo_uri)
db = client["rag_database"]
investigations_collection = db["investigations"]

# AI setup
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "claude-3-sonnet-20240229")


def create_investigation(
    user_id: str,
    connection_id: str,
    event_id: str,
    failure_message: str
) -> Optional[str]:
    """
    Creates InvestigationInDB with status=PENDING.
    Called right after a FailureEvent is created.
    Returns investigation_id.
    """
    if not user_id or not connection_id or not event_id:
        print("ERROR create_investigation: Missing required fields")
        return None
    
    try:
        user_id = str(user_id)
        connection_id = str(connection_id)
        event_id = str(event_id)
        
        investigation_doc = {
            "user_id": user_id,
            "connection_id": connection_id,
            "event_id": event_id,
            "status": InvestigationStatus.PENDING,
            "failure_message": failure_message,
            "lineage_subgraph": None,
            "root_cause": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "completed_at": None,
            "processing_time_ms": 0
        }
        
        result = investigations_collection.insert_one(investigation_doc)
        investigation_id = str(result.inserted_id)
        
        # Mark event as processed
        event_controller.mark_event_processed(event_id, investigation_id)
        
        print(f"DEBUG create_investigation: Created investigation {investigation_id}")
        return investigation_id
    except Exception as e:
        print(f"ERROR create_investigation: {e}")
        return None


def run_investigation(
    investigation_id: str,
    user_id: str,
    connection_id: str,
    openmetadata_url: str,
    openmetadata_token: str
) -> bool:
    """
    Orchestrates the full pipeline:
    traverse lineage → build context → call AI → store RootCause.
    Updates status at each step.
    Returns True if successful.
    """
    try:
        start_time = datetime.now(timezone.utc)
        
        # Get investigation details
        investigation = investigations_collection.find_one({"_id": ObjectId(investigation_id)})
        if not investigation:
            print(f"ERROR run_investigation: Investigation {investigation_id} not found")
            return False
        
        # Step 1: Traverse lineage
        print(f"DEBUG run_investigation: Step 1 - Traversing lineage")
        update_investigation_status(investigation_id, InvestigationStatus.LINEAGE_TRAVERSAL)
        
        # Extract asset from failure message or metadata
        # TODO: Extract asset_fqn from failure context
        asset_fqn = investigation.get("failure_message", "").split(":")[0].strip()
        
        nodes = lineage_controller.traverse_upstream(
            openmetadata_url,
            openmetadata_token,
            asset_fqn,
            max_depth=3
        )
        
        if not nodes:
            print(f"ERROR run_investigation: No nodes found in lineage")
            update_investigation_status(investigation_id, InvestigationStatus.FAILED)
            return False
        
        # Detect break points
        nodes = lineage_controller.detect_break_point(nodes)
        
        # Step 2: Build context
        print(f"DEBUG run_investigation: Step 2 - Building AI context")
        update_investigation_status(investigation_id, InvestigationStatus.CONTEXT_BUILDING)
        
        lineage_subgraph = LineageSubgraph(
            nodes=nodes,
            edges=[],
            total_nodes=len(nodes),
            break_point_node=next((n.id for n in nodes if n.is_break_point), None)
        )
        
        ai_context = build_ai_context(lineage_subgraph, investigation.get("failure_message", ""))
        
        # Store lineage subgraph
        investigations_collection.update_one(
            {"_id": ObjectId(investigation_id)},
            {"$set": {"lineage_subgraph": json.loads(lineage_subgraph.json())}}
        )
        
        # Step 3: Call AI layer
        print(f"DEBUG run_investigation: Step 3 - Calling AI layer")
        update_investigation_status(investigation_id, InvestigationStatus.AI_ANALYSIS)
        
        root_cause = call_ai_layer(ai_context)
        
        if not root_cause:
            print(f"ERROR run_investigation: AI layer failed")
            update_investigation_status(investigation_id, InvestigationStatus.FAILED)
            return False
        
        # Step 4: Store result
        print(f"DEBUG run_investigation: Step 4 - Storing result")
        update_investigation_status(investigation_id, InvestigationStatus.COMPLETED)
        
        processing_time_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
        
        investigations_collection.update_one(
            {"_id": ObjectId(investigation_id)},
            {
                "$set": {
                    "root_cause": json.loads(root_cause.json()),
                    "completed_at": datetime.now(timezone.utc),
                    "processing_time_ms": processing_time_ms
                }
            }
        )
        
        print(f"DEBUG run_investigation: Investigation {investigation_id} completed in {processing_time_ms}ms")
        return True
    except Exception as e:
        print(f"ERROR run_investigation: {e}")
        update_investigation_status(investigation_id, InvestigationStatus.FAILED)
        return False


def build_ai_context(lineage_subgraph: LineageSubgraph, failure_message: str) -> str:
    """
    Takes LineageSubgraph + failure message → formats the structured prompt for the LLM.
    """
    nodes_info = "\n".join([
        f"- {node.name} ({node.type}): {node.fqn}"
        for node in lineage_subgraph.nodes
    ])
    
    context = f"""You are a data lineage expert analyzing a data pipeline failure.

FAILURE MESSAGE:
{failure_message}

DATA LINEAGE (upstream flow, top to bottom):
{nodes_info}

BREAK POINT NODE:
{lineage_subgraph.break_point_node or "Not identified yet"}

Please analyze this lineage and failure, then provide:
1. Root cause of the failure
2. Which asset in the pipeline is responsible
3. Suggested fix with SQL or configuration change
4. Impact on downstream assets

Return your analysis as JSON with these fields:
- root_cause: string
- responsible_asset: string
- suggested_fix: string
- impact_summary: string
"""
    
    return context


def call_ai_layer(ai_context: str, max_retries: int = 3) -> Optional[RootCause]:
    """
    Sends prompt to Claude/OpenAI.
    Parses JSON response into RootCause.
    Handles retries.
    """
    for attempt in range(max_retries):
        try:
            # Determine which API to use
            if AI_MODEL.startswith("gpt"):
                response = _call_openai(ai_context)
            else:
                response = _call_claude(ai_context)
            
            if response:
                # Parse JSON response
                root_cause = RootCause(
                    root_cause=response.get("root_cause", ""),
                    responsible_asset=response.get("responsible_asset", ""),
                    suggested_fix=response.get("suggested_fix", ""),
                    impact_summary=response.get("impact_summary", ""),
                    confidence_score=response.get("confidence_score", 0.5)
                )
                return root_cause
        except Exception as e:
            print(f"ERROR call_ai_layer attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                continue
    
    print(f"ERROR call_ai_layer: Failed after {max_retries} attempts")
    return None


def _call_openai(prompt: str) -> Optional[dict]:
    """Call OpenAI API."""
    try:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": "gpt-4-turbo",
            "messages": [
                {"role": "system", "content": "You are a data pipeline expert. Always respond with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3
        }
        
        response = requests.post(url, json=data, headers=headers, timeout=60)
        
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            return json.loads(content)
        else:
            print(f"ERROR _call_openai: Status {response.status_code}")
            return None
    except Exception as e:
        print(f"ERROR _call_openai: {e}")
        return None


def _call_claude(prompt: str) -> Optional[dict]:
    """Call Claude API via Anthropic."""
    try:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        
        data = {
            "model": AI_MODEL,
            "max_tokens": 2048,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }
        
        response = requests.post(url, json=data, headers=headers, timeout=60)
        
        if response.status_code == 200:
            result = response.json()
            content = result["content"][0]["text"]
            return json.loads(content)
        else:
            print(f"ERROR _call_claude: Status {response.status_code}")
            return None
    except Exception as e:
        print(f"ERROR _call_claude: {e}")
        return None


def get_investigation(investigation_id: str, user_id: str) -> Optional[InvestigationResponse]:
    """Fetches InvestigationResponse by id. Used by chat UI and PR bot."""
    try:
        investigation = investigations_collection.find_one({
            "_id": ObjectId(investigation_id),
            "user_id": str(user_id)
        })
        
        if not investigation:
            return None
        
        return InvestigationResponse(
            id=str(investigation["_id"]),
            user_id=str(investigation["user_id"]),
            connection_id=str(investigation["connection_id"]),
            event_id=str(investigation["event_id"]),
            status=investigation.get("status", InvestigationStatus.PENDING),
            failure_message=investigation.get("failure_message", ""),
            root_cause=investigation.get("root_cause"),
            created_at=investigation.get("created_at"),
            completed_at=investigation.get("completed_at"),
            processing_time_ms=investigation.get("processing_time_ms", 0)
        )
    except Exception as e:
        print(f"ERROR get_investigation: {e}")
        return None


def list_investigations(user_id: str, limit: int = 20) -> List[InvestigationListItem]:
    """Returns InvestigationListItem list for sidebar — no heavy subgraph payload."""
    try:
        investigations = investigations_collection.find(
            {"user_id": str(user_id)}
        ).sort("created_at", -1).limit(limit)
        
        result = []
        for inv in investigations:
            result.append(InvestigationListItem(
                id=str(inv["_id"]),
                user_id=str(inv["user_id"]),
                status=inv.get("status", InvestigationStatus.PENDING),
                failure_message=inv.get("failure_message", "")[:100],
                created_at=inv.get("created_at"),
                completed_at=inv.get("completed_at")
            ))
        
        return result
    except Exception as e:
        print(f"ERROR list_investigations: {e}")
        return []


def update_investigation_status(investigation_id: str, status: str) -> bool:
    """Internal. Updates status field as pipeline progresses."""
    try:
        result = investigations_collection.update_one(
            {"_id": ObjectId(investigation_id)},
            {
                "$set": {
                    "status": status,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        return result.modified_count > 0
    except Exception as e:
        print(f"ERROR update_investigation_status: {e}")
        return False
