"""
investigation_controller.py — fixed for bugs #3 and #4.

Bug #3: call_ai_layer was passing raw dicts as affected_assets to RootCause.
        Pydantic v2 does not auto-coerce dicts into nested models.
        Fix: explicitly construct AffectedAsset instances.

Bug #4: get_investigation was returning raw Mongo dicts for root_cause and
        lineage_subgraph instead of proper Pydantic model instances.
        Fix: explicitly construct RootCause / LineageSubgraph from the raw dicts.
"""

import os
import json
import requests
from typing import List, Optional
from datetime import datetime, timezone
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv

from models.investigations import (
    InvestigationInDB, InvestigationResponse,
    InvestigationListItem, RootCause, SuggestedFix
)
from models.events import AffectedAsset          # FIX #3: needed for explicit construction
from models.base import InvestigationStatus
from models.lineage import LineageSubgraph
from controllers import lineage_controller, event_controller

load_dotenv()

mongo_uri = os.getenv("MONGO_URI")
if not mongo_uri:
    raise RuntimeError("MONGO_URI not set in environment")

client = MongoClient(mongo_uri)
db = client["rag_database"]
investigations_collection = db["investigations"]

OPENAI_API_KEY        = os.getenv("OPENAI_API_KEY", "")
CLAUDE_API_KEY        = os.getenv("CLAUDE_API_KEY", "")
AI_MODEL              = os.getenv("AI_MODEL", "claude-sonnet-4-20250514")


def create_investigation(
    user_id: str,
    connection_id: str,
    event_id: str,
    failure_message: str,
    asset_fqn: Optional[str] = None
) -> Optional[str]:
    if not user_id or not connection_id or not event_id:
        print("ERROR create_investigation: Missing required fields")
        return None

    try:
        investigation_doc = {
            "user_id":           str(user_id),
            "connection_id":     str(connection_id),
            "event_id":          str(event_id),
            "status":            InvestigationStatus.PENDING,
            "failure_message":   failure_message,
            "failing_asset_fqn": asset_fqn or failure_message.split(":")[0].strip(),
            "event_type":        "manual",
            "lineage_subgraph":  None,
            "root_cause":        None,
            "created_at":        datetime.now(timezone.utc).isoformat(),
            "updated_at":        datetime.now(timezone.utc).isoformat(),
            "completed_at":      None,
            "processing_time_ms": None
        }

        result = investigations_collection.insert_one(investigation_doc)
        investigation_id = str(result.inserted_id)

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
    try:
        start_time = datetime.now(timezone.utc)

        investigation = investigations_collection.find_one({"_id": ObjectId(investigation_id)})
        if not investigation:
            print(f"ERROR run_investigation: Investigation {investigation_id} not found")
            return False

        print(f"DEBUG run_investigation: Step 1 - Traversing lineage")
        update_investigation_status(investigation_id, InvestigationStatus.LINEAGE_TRAVERSAL)

        asset_fqn = investigation.get("failing_asset_fqn", "")

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

        nodes = lineage_controller.detect_break_point(nodes)

        print(f"DEBUG run_investigation: Step 2 - Building AI context")
        update_investigation_status(investigation_id, InvestigationStatus.CONTEXT_BUILDING)

        lineage_subgraph = LineageSubgraph(
            failing_asset_fqn=asset_fqn,
            nodes=nodes,
            edges=[],
            traversal_depth=len(nodes)
        )

        ai_context = build_ai_context(lineage_subgraph, investigation.get("failure_message", ""))

        investigations_collection.update_one(
            {"_id": ObjectId(investigation_id)},
            {"$set": {"lineage_subgraph": lineage_subgraph.model_dump()}}
        )

        print(f"DEBUG run_investigation: Step 3 - Calling AI layer")
        update_investigation_status(investigation_id, InvestigationStatus.AI_ANALYSIS)

        root_cause = call_ai_layer(ai_context)

        if not root_cause:
            print(f"ERROR run_investigation: AI layer failed")
            update_investigation_status(investigation_id, InvestigationStatus.FAILED)
            return False

        print(f"DEBUG run_investigation: Step 4 - Storing result")
        processing_time_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

        investigations_collection.update_one(
            {"_id": ObjectId(investigation_id)},
            {
                "$set": {
                    "status":            InvestigationStatus.COMPLETED,
                    "root_cause":        root_cause.model_dump(),
                    "completed_at":      datetime.now(timezone.utc).isoformat(),
                    "processing_time_ms": processing_time_ms,
                    "updated_at":        datetime.now(timezone.utc).isoformat()
                }
            }
        )

        print(f"DEBUG run_investigation: Completed in {processing_time_ms}ms")
        return True
    except Exception as e:
        print(f"ERROR run_investigation: {e}")
        update_investigation_status(investigation_id, InvestigationStatus.FAILED)
        return False


def build_ai_context(lineage_subgraph: LineageSubgraph, failure_message: str) -> str:
    nodes_info = "\n".join([
        f"- {node.display_name} ({node.asset_type}): {node.fqn}"
        + (" ← BREAK POINT" if node.is_break_point else "")
        for node in lineage_subgraph.nodes
    ])
    return f"""You are a data lineage expert analyzing a data pipeline failure.

FAILURE MESSAGE:
{failure_message}

DATA LINEAGE (upstream flow):
{nodes_info}

BREAK POINT NODE:
{lineage_subgraph.break_point_node or "Not identified yet"}

Analyze this failure and return ONLY valid JSON with these EXACT fields, no variations:
{{
    "one_line_summary": "Single sentence summary of the root cause",
    "detailed_explanation": "Full explanation of what changed and why it cascaded",
    "break_point_fqn": "FQN of the asset where the change originated",
    "break_point_change": "Human-readable description of the exact change",
    "affected_assets": [
        {{
            "fqn": "fully.qualified.name.of.asset",
            "asset_type": "table",
            "display_name": "asset_name",
            "severity": "critical",
            "owner_email": null
        }}
    ],
    "suggested_fixes": [
        {{
            "description": "What to do",
            "fix_type": "rename_column",
            "target_asset_fqn": "fqn.of.target",
            "code_snippet": "SQL snippet if applicable"
        }}
    ],
    "owner_to_contact": null,
    "confidence": 0.85
}}

IMPORTANT: Use exactly these field names. severity must be one of: critical, high, medium, low."""


def call_ai_layer(ai_context: str, max_retries: int = 3) -> Optional[RootCause]:
    GROQ_API_KEY          = os.getenv("GROQ_API_KEY", "")
    DEFAULT_LLM_PROVIDER  = os.getenv("DEFAULT_LLM_PROVIDER", "groq")

    for attempt in range(max_retries):
        try:
            if DEFAULT_LLM_PROVIDER == "groq" or AI_MODEL.startswith("llama"):
                response = _call_groq(ai_context, GROQ_API_KEY)
            elif AI_MODEL.startswith("gpt"):
                response = _call_openai(ai_context)
            else:
                response = _call_claude(ai_context)

            if response:
                # FIX #3: explicitly construct AffectedAsset instances from dicts.
                # Pydantic v2 does NOT auto-coerce plain dicts into nested models.
                affected_assets: List[AffectedAsset] = []
                for a in response.get("affected_assets", []):
                    if isinstance(a, dict):
                        try:
                            affected_assets.append(AffectedAsset(**a))
                        except Exception as ae:
                            print(f"WARNING call_ai_layer: skipping malformed affected_asset: {ae}")
                    else:
                        affected_assets.append(a)

                suggested_fixes: List[SuggestedFix] = []
                for f in response.get("suggested_fixes", []):
                    try:
                        suggested_fixes.append(SuggestedFix(
                            description=f.get("description", ""),
                            fix_type=f.get("fix_type", "update_ref"),
                            target_asset_fqn=f.get("target_asset_fqn"),
                            code_snippet=f.get("code_snippet")
                        ))
                    except Exception as fe:
                        print(f"WARNING call_ai_layer: skipping malformed fix: {fe}")

                root_cause = RootCause(
                    one_line_summary=response.get("one_line_summary", "Root cause analysis completed"),
                    detailed_explanation=response.get("detailed_explanation", ""),
                    break_point_fqn=response.get("break_point_fqn", "unknown"),
                    break_point_change=response.get("break_point_change", ""),
                    affected_assets=affected_assets,      # now List[AffectedAsset]
                    suggested_fixes=suggested_fixes,
                    owner_to_contact=response.get("owner_to_contact"),
                    confidence=float(response.get("confidence", 0.5))
                )
                return root_cause

        except Exception as e:
            print(f"ERROR call_ai_layer attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                continue

    print(f"ERROR call_ai_layer: Failed after {max_retries} attempts")
    return None


def _call_groq(prompt: str, groq_api_key: str) -> Optional[dict]:
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {groq_api_key}",
            "Content-Type":  "application/json"
        }
        data = {
            "model": AI_MODEL,
            "messages": [
                {"role": "system", "content": "You are a data pipeline expert. Always respond with valid JSON only, no markdown."},
                {"role": "user",   "content": prompt}
            ],
            "temperature": 0.3
        }
        response = requests.post(url, json=data, headers=headers, timeout=60)
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"]
            content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(content)
        else:
            print(f"ERROR _call_groq: Status {response.status_code} — {response.text}")
            return None
    except Exception as e:
        print(f"ERROR _call_groq: {e}")
        return None


def _call_openai(prompt: str) -> Optional[dict]:
    try:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type":  "application/json"
        }
        data = {
            "model": "gpt-4-turbo",
            "messages": [
                {"role": "system", "content": "You are a data pipeline expert. Always respond with valid JSON only, no markdown."},
                {"role": "user",   "content": prompt}
            ],
            "temperature": 0.3
        }
        response = requests.post(url, json=data, headers=headers, timeout=60)
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"]
            content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(content)
        else:
            print(f"ERROR _call_openai: Status {response.status_code} — {response.text}")
            return None
    except Exception as e:
        print(f"ERROR _call_openai: {e}")
        return None


def _call_claude(prompt: str) -> Optional[dict]:
    try:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key":         CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json"
        }
        data = {
            "model":      AI_MODEL,
            "max_tokens": 2048,
            "messages":   [{"role": "user", "content": prompt}]
        }
        response = requests.post(url, json=data, headers=headers, timeout=60)
        if response.status_code == 200:
            content = response.json()["content"][0]["text"]
            content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(content)
        else:
            print(f"ERROR _call_claude: Status {response.status_code} — {response.text}")
            return None
    except Exception as e:
        print(f"ERROR _call_claude: {e}")
        return None


def get_investigation(investigation_id: str, user_id: str) -> Optional[InvestigationResponse]:
    try:
        investigation = investigations_collection.find_one({
            "_id":     ObjectId(investigation_id),
            "user_id": str(user_id)
        })

        if not investigation:
            return None

        # FIX #4: explicitly construct model instances from raw Mongo dicts.
        # Without this, root_cause is a plain dict and getattr() returns None
        # in the PR bot, causing empty comments.
        raw_rc = investigation.get("root_cause")
        raw_lg = investigation.get("lineage_subgraph")

        root_cause_obj: Optional[RootCause] = None
        if raw_rc:
            try:
                # Re-hydrate nested AffectedAsset list too
                if "affected_assets" in raw_rc:
                    raw_rc["affected_assets"] = [
                        AffectedAsset(**a) if isinstance(a, dict) else a
                        for a in raw_rc["affected_assets"]
                    ]
                root_cause_obj = RootCause(**raw_rc)
            except Exception as e:
                print(f"WARNING get_investigation: could not parse root_cause: {e}")

        lineage_obj: Optional[LineageSubgraph] = None
        if raw_lg:
            try:
                lineage_obj = LineageSubgraph(**raw_lg)
            except Exception as e:
                print(f"WARNING get_investigation: could not parse lineage_subgraph: {e}")

        return InvestigationResponse(
            id=str(investigation["_id"]),
            event_id=str(investigation.get("event_id", "")),
            failing_asset_fqn=investigation.get("failing_asset_fqn", ""),
            failure_message=investigation.get("failure_message", ""),
            event_type=investigation.get("event_type", "manual"),
            status=investigation.get("status", InvestigationStatus.PENDING),
            root_cause=root_cause_obj,          # FIX #4
            lineage_subgraph=lineage_obj,        # FIX #4
            pr_number=investigation.get("pr_number"),
            pr_url=investigation.get("pr_url"),
            created_at=str(investigation.get("created_at", "")),
            completed_at=investigation.get("completed_at"),
            processing_time_ms=investigation.get("processing_time_ms")
        )
    except Exception as e:
        print(f"ERROR get_investigation: {e}")
        return None


def list_investigations(user_id: str, limit: int = 20) -> List[InvestigationListItem]:
    try:
        investigations = investigations_collection.find(
            {"user_id": str(user_id)}
        ).sort("created_at", -1).limit(limit)

        result = []
        for inv in investigations:
            root_cause     = inv.get("root_cause")
            summary        = root_cause.get("one_line_summary") if root_cause else None
            affected_assets = root_cause.get("affected_assets", []) if root_cause else []
            has_critical   = any(
                a.get("severity") == "critical" for a in affected_assets
            )

            result.append(InvestigationListItem(
                id=str(inv["_id"]),
                failing_asset_fqn=inv.get("failing_asset_fqn", ""),
                event_type=inv.get("event_type", "manual"),
                status=inv.get("status", InvestigationStatus.PENDING),
                summary=summary,
                affected_count=len(affected_assets),
                has_critical_impact=has_critical,
                created_at=str(inv.get("created_at", "")),
                processing_time_ms=inv.get("processing_time_ms")
            ))

        return result
    except Exception as e:
        print(f"ERROR list_investigations: {e}")
        return []


def update_investigation_status(investigation_id: str, status: InvestigationStatus) -> bool:
    try:
        result = investigations_collection.update_one(
            {"_id": ObjectId(investigation_id)},
            {
                "$set": {
                    "status":     status,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
            }
        )
        return result.modified_count > 0
    except Exception as e:
        print(f"ERROR update_investigation_status: {e}")
        return False