"""
investigation_controller.py — Investigation lifecycle for Pipeline Autopsy.

Function organisation:
  ── Shared utilities ──────────────────────────────────────────────────────────
  create_investigation          — creates an Investigation document in MongoDB
  update_investigation_status   — updates status field
  get_investigation             — fetches + deserialises a full InvestigationResponse
  list_investigations           — compact list for sidebar

  ── Manual investigation flow (unchanged) ────────────────────────────────────
  run_investigation             — single-asset, used by chat UI
  build_ai_context              — prompt builder for single-asset flow
  call_ai_layer                 — LLM call returning RootCause
  _call_groq / _call_openai / _call_claude  — provider adapters

  ── PR bot investigation flow ─────────────────────────────────────────────────
  merge_lineage_subgraphs       — merges N subgraphs, deduplicates nodes by FQN,
                                  tracks which upstream asset each node came from
  build_downstream_context      — orchestrates Layer 2 + Layer 3 fetching
                                  concurrently via ThreadPoolExecutor.
                                  Layer 2: schema of each changed asset (OpenMetadata)
                                  Layer 3: SQL of each downstream consumer (GitHub repo)
  build_pr_ai_context           — prompt builder for multi-asset PR flow, accepts
                                  downstream_context with Layer 2 + Layer 3 data
  call_pr_ai_layer              — LLM call returning PRRootCause
  run_pr_investigation          — entry point called by the PR webhook background task

Reuse policy:
  - _call_groq / _call_openai / _call_claude are shared between both flows
  - create_investigation / update_investigation_status / get_investigation
    are shared between both flows
  - RootCause (manual flow) and PRRootCause (PR flow) are separate models —
    no cross-contamination
"""

import os
import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Dict, Tuple
from datetime import datetime, timezone
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv

from models.investigations import (
    InvestigationInDB, InvestigationResponse,
    InvestigationListItem, RootCause, SuggestedFix
)
from models.github import (
    PRRootCause, ChangedAssetSummary, DownstreamImpact,
    AssetCause, ErrorLocation, CauseFix, ChangedAsset
)
from models.events import AffectedAsset
from models.base import InvestigationStatus, SeverityLevel
from models.lineage import LineageSubgraph, LineageNode, LineageEdge
from controllers import lineage_controller, event_controller

load_dotenv()

mongo_uri = os.getenv("MONGO_URI")
if not mongo_uri:
    raise RuntimeError("MONGO_URI not set in environment")

client = MongoClient(mongo_uri)
db = client["rag_database"]
investigations_collection = db["investigations"]

OPENAI_API_KEY       = os.getenv("OPENAI_API_KEY", "")
CLAUDE_API_KEY       = os.getenv("CLAUDE_API_KEY", "")
AI_MODEL             = os.getenv("AI_MODEL", "claude-sonnet-4-20250514")
DEFAULT_LLM_PROVIDER = os.getenv("DEFAULT_LLM_PROVIDER", "groq")
USE_OPENMETADATA = os.getenv("USE_OPENMETADATA", "false").lower() == "true"

# Maximum downstream nodes to fetch SQL for — protects against token overflow.
# 20 nodes × 150 lines × ~4 chars = ~12k tokens max for the SQL section.
MAX_DOWNSTREAM_SQL_FETCHES = 20


# ── Shared utilities ──────────────────────────────────────────────────────────

def create_investigation(
    user_id: str,
    connection_id: str,
    event_id: str,
    failure_message: str,
    asset_fqn: Optional[str] = None,
    event_type: str = "manual"
) -> Optional[str]:
    if not user_id or not connection_id or not event_id:
        print("ERROR create_investigation: Missing required fields")
        return None

    try:
        investigation_doc = {
            "user_id":            str(user_id),
            "connection_id":      str(connection_id),
            "event_id":           str(event_id),
            "status":             InvestigationStatus.PENDING,
            "failure_message":    failure_message,
            "failing_asset_fqn":  asset_fqn or failure_message.split(":")[0].strip(),
            "event_type":         event_type,
            "lineage_subgraph":   None,
            "root_cause":         None,
            "pr_root_cause":      None,
            "created_at":         datetime.now(timezone.utc).isoformat(),
            "updated_at":         datetime.now(timezone.utc).isoformat(),
            "completed_at":       None,
            "processing_time_ms": None
        }

        result = investigations_collection.insert_one(investigation_doc)
        investigation_id = str(result.inserted_id)

        event_controller.mark_event_processed(event_id, investigation_id)

        print(f"DEBUG create_investigation: Created {event_type} investigation {investigation_id}")
        return investigation_id

    except Exception as e:
        print(f"ERROR create_investigation: {e}")
        return None


def _deserialise_pr_root_cause(raw: dict) -> Optional["PRRootCause"]:
    """
    Explicitly reconstructs a PRRootCause from a raw MongoDB dict.
    Called by get_investigation — mirrors the explicit construction pattern
    used in _parse_pr_ai_response to stay consistent and avoid auto-coerce.
    Imported lazily to avoid circular import (github.py ← investigations.py).
    """
    from models.github import PRRootCause, ChangedAssetSummary, DownstreamImpact, AssetCause, ErrorLocation, CauseFix

    changed_assets: List[ChangedAssetSummary] = []
    for ca in raw.get("changed_assets", []):
        try:
            changed_assets.append(ChangedAssetSummary(**ca))
        except Exception as e:
            print(f"WARNING _deserialise_pr_root_cause: skipping malformed changed_asset: {e}")

    downstream_impacts: List[DownstreamImpact] = []
    for di in raw.get("downstream_impacts", []):
        try:
            causes: List[AssetCause] = []
            for cause in di.get("causes", []):
                try:
                    loc = cause.get("error_location", {})
                    fix = cause.get("fix", {})
                    causes.append(AssetCause(
                        source_asset_fqn=cause["source_asset_fqn"],
                        error_type=cause["error_type"],
                        error_description=cause["error_description"],
                        error_location=ErrorLocation(
                            file=loc["file"],
                            clause=loc["clause"],
                            approximate_line=loc.get("approximate_line")
                        ),
                        fix=CauseFix(
                            description=fix["description"],
                            fix_type=fix["fix_type"],
                            target_file=fix["target_file"],
                            code_snippet=fix.get("code_snippet")
                        )
                    ))
                except Exception as e:
                    print(f"WARNING _deserialise_pr_root_cause: skipping malformed cause: {e}")

            downstream_impacts.append(DownstreamImpact(
                fqn=di["fqn"],
                display_name=di.get("display_name", di["fqn"]),
                severity=SeverityLevel(di["severity"]),
                causes=causes
            ))
        except Exception as e:
            print(f"WARNING _deserialise_pr_root_cause: skipping malformed downstream_impact: {e}")

    return PRRootCause(
        pr_summary=raw["pr_summary"],
        overall_severity=SeverityLevel(raw["overall_severity"]),
        safe_to_merge=bool(raw["safe_to_merge"]),
        confidence=float(raw["confidence"]),
        changed_assets=changed_assets,
        downstream_impacts=downstream_impacts
    )


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


def get_investigation(investigation_id: str, user_id: str) -> Optional[InvestigationResponse]:
    try:
        investigation = investigations_collection.find_one({
            "_id":     ObjectId(investigation_id),
            "user_id": str(user_id)
        })

        if not investigation:
            return None

        raw_rc  = investigation.get("root_cause")
        raw_prc = investigation.get("pr_root_cause")
        raw_lg  = investigation.get("lineage_subgraph")

        # ── Deserialise manual root_cause ─────────────────────────────────────
        root_cause_obj: Optional[RootCause] = None
        if raw_rc:
            try:
                if "affected_assets" in raw_rc:
                    raw_rc["affected_assets"] = [
                        AffectedAsset(**a) if isinstance(a, dict) else a
                        for a in raw_rc["affected_assets"]
                    ]
                root_cause_obj = RootCause(**raw_rc)
            except Exception as e:
                print(f"WARNING get_investigation: could not parse root_cause: {e}")

        # ── Deserialise PR root cause ─────────────────────────────────────────
        pr_root_cause_obj: Optional[PRRootCause] = None
        if raw_prc:
            try:
                pr_root_cause_obj = _deserialise_pr_root_cause(raw_prc)
            except Exception as e:
                print(f"WARNING get_investigation: could not parse pr_root_cause: {e}")

        # ── Deserialise lineage subgraph ──────────────────────────────────────
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
            root_cause=root_cause_obj,
            pr_root_cause=pr_root_cause_obj,
            lineage_subgraph=lineage_obj,
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
            root_cause      = inv.get("root_cause")
            summary         = root_cause.get("one_line_summary") if root_cause else None
            affected_assets = root_cause.get("affected_assets", []) if root_cause else []
            has_critical    = any(a.get("severity") == "critical" for a in affected_assets)

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


# ── Manual investigation flow (unchanged) ─────────────────────────────────────

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
            openmetadata_url, openmetadata_token, asset_fqn, max_depth=3
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
                    "status":             InvestigationStatus.COMPLETED,
                    "root_cause":         root_cause.model_dump(),
                    "completed_at":       datetime.now(timezone.utc).isoformat(),
                    "processing_time_ms": processing_time_ms,
                    "updated_at":         datetime.now(timezone.utc).isoformat()
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
    """Prompt builder for single-asset manual investigation flow."""
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
    """
    Calls the configured LLM and parses the response into a RootCause.
    Used by the manual investigation flow only.
    """
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

    for attempt in range(max_retries):
        try:
            if DEFAULT_LLM_PROVIDER == "groq" or AI_MODEL.startswith("llama"):
                response = _call_groq(ai_context, GROQ_API_KEY)
            elif AI_MODEL.startswith("gpt"):
                response = _call_openai(ai_context)
            else:
                response = _call_claude(ai_context)

            if response:
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

                return RootCause(
                    one_line_summary=response.get("one_line_summary", "Root cause analysis completed"),
                    detailed_explanation=response.get("detailed_explanation", ""),
                    break_point_fqn=response.get("break_point_fqn", "unknown"),
                    break_point_change=response.get("break_point_change", ""),
                    affected_assets=affected_assets,
                    suggested_fixes=suggested_fixes,
                    owner_to_contact=response.get("owner_to_contact"),
                    confidence=float(response.get("confidence", 0.5))
                )

        except Exception as e:
            print(f"ERROR call_ai_layer attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                continue

    print(f"ERROR call_ai_layer: Failed after {max_retries} attempts")
    return None


# ── Shared LLM provider adapters ──────────────────────────────────────────────
# Used by both call_ai_layer (manual) and call_pr_ai_layer (PR bot)

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
                {
                    "role": "system",
                    "content": "You are a data pipeline expert. Always respond with valid JSON only. No markdown, no backticks, no explanation outside the JSON object."
                },
                {"role": "user", "content": prompt}
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
                {
                    "role": "system",
                    "content": "You are a data pipeline expert. Always respond with valid JSON only. No markdown, no backticks, no explanation outside the JSON object."
                },
                {"role": "user", "content": prompt}
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
            "system":     "You are a data pipeline expert. Always respond with valid JSON only. No markdown, no backticks, no explanation outside the JSON object.",
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


# ── PR bot investigation flow ──────────────────────────────────────────────────

def merge_lineage_subgraphs(
    subgraphs: List[Tuple[str, LineageSubgraph]]
) -> LineageSubgraph:
    """
    Merges multiple per-asset lineage subgraphs into one unified subgraph.

    Behaviour:
      - Nodes deduplicated by FQN — first occurrence wins for base fields.
      - Each node gains raw_metadata["source_assets"] tracking which PR-changed
        asset it was reached from.
      - is_downstream flag is preserved: if ANY subgraph marks the node as a
        downstream consumer, it stays marked as downstream in the merged graph.
      - Severity escalates to highest across subgraphs for the same node.
      - Edges deduplicated by (from_fqn, to_fqn) pair.
    """
    seen_nodes: Dict[str, LineageNode] = {}
    seen_edges: set = set()
    merged_edges: List[LineageEdge] = []
    max_depth = 0

    for source_fqn, subgraph in subgraphs:
        max_depth = max(max_depth, subgraph.traversal_depth)

        for node in subgraph.nodes:
            if node.fqn not in seen_nodes:
                node.raw_metadata["source_assets"] = [source_fqn]
                seen_nodes[node.fqn] = node
            else:
                existing = seen_nodes[node.fqn]
                sources = existing.raw_metadata.get("source_assets", [])
                if source_fqn not in sources:
                    sources.append(source_fqn)
                existing.raw_metadata["source_assets"] = sources

                # Preserve is_downstream: once a node is tagged as a consumer
                # in any subgraph, keep it tagged
                if node.is_downstream:
                    existing.is_downstream = True
                    existing.depth_from_failure = -1

                # Escalate severity
                if node.severity and existing.severity:
                    severity_rank = {
                        SeverityLevel.LOW: 0, SeverityLevel.MEDIUM: 1,
                        SeverityLevel.HIGH: 2, SeverityLevel.CRITICAL: 3
                    }
                    if severity_rank.get(node.severity, 0) > severity_rank.get(existing.severity, 0):
                        existing.severity = node.severity

        for edge in subgraph.edges:
            edge_key = (edge.from_fqn, edge.to_fqn)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                merged_edges.append(edge)

    all_source_fqns = [source_fqn for source_fqn, _ in subgraphs]

    merged = LineageSubgraph(
        failing_asset_fqn=", ".join(all_source_fqns),
        nodes=list(seen_nodes.values()),
        edges=merged_edges,
        traversal_depth=max_depth
    )

    downstream_count = sum(1 for n in merged.nodes if n.is_downstream)
    print(
        f"DEBUG merge_lineage_subgraphs: "
        f"{len(merged.nodes)} unique nodes ({downstream_count} downstream consumers), "
        f"{len(merged.edges)} unique edges from {len(subgraphs)} subgraphs"
    )
    return merged


# ── build_downstream_context ───────────────────────────────────────────────────

def build_downstream_context(
    openmetadata_url: str,
    openmetadata_token: str,
    github_token: str,
    repo_owner: str,
    repo_name: str,
    asset_fqn_map: Dict[str, Tuple[str, bool, str]],
    merged_subgraph: LineageSubgraph
) -> dict:
    """
    Orchestrates concurrent fetching of Layer 2 and Layer 3 context.

    Layer 2 — Current schema of each CHANGED asset (from OpenMetadata).
      For every FQN in asset_fqn_map, fetches the current column list so the
      AI knows the exact contract the PR is potentially breaking.

    Layer 3 — SQL of each DOWNSTREAM CONSUMER node (from GitHub repo).
      For every node in merged_subgraph where is_downstream=True, searches
      the repo for its SQL file and fetches the content so the AI can check
      which columns it references.

      Only nodes with is_downstream=True are fetched — these are the actual
      consumers of the changed asset (tagged by traverse_upstream from
      downstreamEdges). Upstream ancestors are excluded.

      Capped at MAX_DOWNSTREAM_SQL_FETCHES (20) to prevent token overflow.

    All fetches run concurrently via ThreadPoolExecutor — no sequential waiting.

    Returns:
      {
        "changed_asset_schemas": { fqn: [{"name": "col", "dataType": "INT"}, ...] },
        "downstream_sqls":       { fqn: "SELECT ..." | None }
      }

    None values in downstream_sqls mean the SQL file could not be located
    in the repo (external asset, BI tool, different repo, etc.) — the AI
    will reason from lineage context only for those.
    """
    from controllers.github_controller import search_file_in_repo, fetch_file_content

    # Layer 2: changed asset FQNs (deduplicated)
    changed_fqns = list({fqn for (fqn, _, _) in asset_fqn_map.values()})

    # Layer 3: downstream consumer FQNs only — is_downstream=True nodes
    # These are the assets that actually CONSUME the changed asset.
    # Capped to avoid token overflow.
    all_downstream_fqns = [
        node.fqn
        for node in merged_subgraph.nodes
        if node.is_downstream
    ]

    if len(all_downstream_fqns) > MAX_DOWNSTREAM_SQL_FETCHES:
        print(
            f"WARNING build_downstream_context: {len(all_downstream_fqns)} downstream consumers — "
            f"capping at {MAX_DOWNSTREAM_SQL_FETCHES} to avoid token overflow"
        )
        downstream_fqns = all_downstream_fqns[:MAX_DOWNSTREAM_SQL_FETCHES]
    else:
        downstream_fqns = all_downstream_fqns

    changed_asset_schemas: Dict[str, List[dict]] = {}
    downstream_sqls: Dict[str, Optional[str]] = {}

    print(
        f"DEBUG build_downstream_context: "
        f"Fetching schemas for {len(changed_fqns)} changed assets, "
        f"SQL for {len(downstream_fqns)} downstream consumers"
    )

    # ── Concurrent fetch ───────────────────────────────────────────────────────
    def _fetch_schema(fqn: str) -> Tuple[str, List[dict]]:
        schema = lineage_controller.fetch_asset_schema(
            openmetadata_url, openmetadata_token, fqn
        )
        return fqn, schema

    def _fetch_downstream_sql(fqn: str) -> Tuple[str, Optional[str]]:
        file_path = search_file_in_repo(github_token, repo_owner, repo_name, fqn)
        if not file_path:
            print(f"DEBUG build_downstream_context: SQL not found in repo for {fqn}")
            return fqn, None
        content = fetch_file_content(github_token, repo_owner, repo_name, file_path)
        return fqn, content

    with ThreadPoolExecutor(max_workers=10) as executor:
        schema_futures = {
            executor.submit(_fetch_schema, fqn): ("schema", fqn)
            for fqn in changed_fqns
        }
        sql_futures = {
            executor.submit(_fetch_downstream_sql, fqn): ("sql", fqn)
            for fqn in downstream_fqns
        }
        all_futures = {**schema_futures, **sql_futures}

        for future in as_completed(all_futures):
            task_type, fqn = all_futures[future]
            try:
                result_fqn, result_value = future.result()
                if task_type == "schema":
                    changed_asset_schemas[result_fqn] = result_value
                else:
                    downstream_sqls[result_fqn] = result_value
            except Exception as e:
                print(f"ERROR build_downstream_context: Task failed for {fqn} ({task_type}): {e}")
                if task_type == "schema":
                    changed_asset_schemas[fqn] = []
                else:
                    downstream_sqls[fqn] = None

    # Ensure every FQN has an entry even if its future errored out
    for fqn in changed_fqns:
        changed_asset_schemas.setdefault(fqn, [])
    for fqn in downstream_fqns:
        downstream_sqls.setdefault(fqn, None)

    fetched_schemas = sum(1 for v in changed_asset_schemas.values() if v)
    fetched_sqls    = sum(1 for v in downstream_sqls.values() if v)
    print(
        f"DEBUG build_downstream_context: "
        f"Got {fetched_schemas}/{len(changed_fqns)} schemas, "
        f"{fetched_sqls}/{len(downstream_fqns)} downstream SQLs"
    )

    return {
        "changed_asset_schemas": changed_asset_schemas,
        "downstream_sqls":       downstream_sqls,
    }


# ── build_pr_ai_context ────────────────────────────────────────────────────────

def build_pr_ai_context(
    asset_fqn_map: Dict[str, Tuple[str, bool, str]],
    merged_subgraph: LineageSubgraph,
    pr_number: int,
    downstream_context: Optional[dict] = None
) -> str:
    """
    Builds the AI prompt for multi-asset PR analysis.

    Args:
        asset_fqn_map:       Dict mapping filename → (fqn, fqn_approximate, stripped_patch)
        merged_subgraph:     Unified lineage subgraph across all changed assets
        pr_number:           GitHub PR number (for context)
        downstream_context:  Optional dict from build_downstream_context:
                               - "changed_asset_schemas": {fqn: [{name, dataType}, ...]}
                               - "downstream_sqls":       {fqn: sql_string | None}
                             When None, prompt falls back to lineage-only format.

    Three-section prompt when downstream_context is provided:
      1. CHANGED ASSETS   — diff + current schema (Layer 1 + 2)
      2. DOWNSTREAM SQL   — SQL fetched from repo (Layer 3)
      3. LINEAGE GRAPH    — merged node list with source tracking
    """
    changed_asset_schemas = (downstream_context or {}).get("changed_asset_schemas", {})
    downstream_sqls       = (downstream_context or {}).get("downstream_sqls", {})

    # ── Section 1: Changed assets with diff + current schema ─────────────────
    changed_section_parts = []
    for i, (filename, (fqn, approximate, stripped_patch)) in enumerate(asset_fqn_map.items(), 1):
        approx_note = " (FQN is approximate — derived from path, not patch)" if approximate else ""

        schema_cols = changed_asset_schemas.get(fqn, [])
        if schema_cols:
            schema_lines = "\n".join(
                f"    - {col['name']:<30} {col['dataType']}"
                for col in schema_cols
            )
            schema_block = f"   Current schema (what downstream consumers depend on):\n{schema_lines}"
        else:
            schema_block = "   Current schema: not available in OpenMetadata"

        changed_section_parts.append(
            f"{i}. FQN: {fqn}{approx_note}\n"
            f"   File: {filename}\n"
            f"{schema_block}\n"
            f"   What changed (diff — additions/removals only):\n"
            f"{stripped_patch or '   (no patch available)'}"
        )
    changed_section = "\n\n".join(changed_section_parts)

    # ── Section 2: Downstream consumer SQL ───────────────────────────────────
    if downstream_sqls:
        downstream_parts = []
        for idx, (fqn, sql_content) in enumerate(downstream_sqls.items(), 1):
            if sql_content:
                sql_lines = sql_content.splitlines()
                if len(sql_lines) > 150:
                    sql_display = (
                        "\n".join(sql_lines[:150])
                        + f"\n... ({len(sql_lines) - 150} more lines truncated)"
                    )
                else:
                    sql_display = sql_content
                downstream_parts.append(
                    f"{idx}. FQN: {fqn}\n"
                    f"   SQL (fetched from repo):\n"
                    f"   ```sql\n{sql_display}\n   ```"
                )
            else:
                downstream_parts.append(
                    f"{idx}. FQN: {fqn}\n"
                    f"   SQL: NOT FOUND IN REPO — asset may be in a different repository, "
                    f"a BI tool, or an external system. Reason from lineage context only."
                )
        downstream_section = "\n\n".join(downstream_parts)
    else:
        downstream_section = "No downstream consumers identified in lineage."

    # ── Section 3: Merged lineage graph with source tracking ─────────────────
    lineage_parts = []
    for node in merged_subgraph.nodes:
        sources     = node.raw_metadata.get("source_assets", [])
        source_note = f" [reachable from: {', '.join(sources)}]" if sources else ""
        break_note  = " ← BREAK POINT" if node.is_break_point else ""
        down_note   = " ← DOWNSTREAM CONSUMER" if node.is_downstream else ""
        lineage_parts.append(
            f"- {node.display_name} ({node.asset_type.value}) | FQN: {node.fqn}"
            f"{source_note}{break_note}{down_note}"
        )
    lineage_section = "\n".join(lineage_parts) if lineage_parts else "No lineage data available"

    severity_values = " | ".join(s.value for s in SeverityLevel)

    return f"""You are a data lineage expert analyzing a GitHub PR (#{pr_number}) that changes data assets.
Your job: identify exactly what will break downstream and provide precise, actionable fixes.

Use the downstream SQL provided below to CHECK whether each downstream consumer actually
references the columns that changed. Do not guess — read the SQL and verify.

═══════════════════════════════════════
CHANGED ASSETS IN THIS PR ({len(asset_fqn_map)} files)
═══════════════════════════════════════
{changed_section}

═══════════════════════════════════════
DOWNSTREAM CONSUMERS — SQL FROM REPO
═══════════════════════════════════════
These assets CONSUME the changed assets above.
For each one, the actual SQL has been fetched from the repository.
Check each SQL carefully: does it reference any column that was renamed, dropped, or type-changed?

{downstream_section}

═══════════════════════════════════════
LINEAGE GRAPH (for reference)
═══════════════════════════════════════
{lineage_section}

═══════════════════════════════════════
RESPONSE SCHEMA — FOLLOW EXACTLY
═══════════════════════════════════════
Return ONLY a valid JSON object. No markdown. No backticks. No explanation outside the JSON.
Every field listed below is required. Use null for optional fields you cannot determine.

{{
  "pr_summary": "One sentence: what changed and how many downstream assets are affected",
  "overall_severity": "{severity_values}",
  "safe_to_merge": false,
  "confidence": 0.85,

  "changed_assets": [
    {{
      "fqn": "exact FQN from the CHANGED ASSETS section above",
      "filename": "exact filename from the CHANGED ASSETS section above",
      "change_type": "column_added | column_dropped | column_type_changed | source_renamed | model_renamed | schema_change | sql_logic_change",
      "change_description": "One sentence describing exactly what changed",
      "patch_evidence": "The specific +/- lines that show the change (copy from diff above)",
      "fqn_approximate": false
    }}
  ],

  "downstream_impacts": [
    {{
      "fqn": "fully.qualified.name of the broken downstream asset",
      "display_name": "human readable name",
      "severity": "{severity_values}",
      "causes": [
        {{
          "source_asset_fqn": "FQN of the PR-changed asset that caused THIS specific break",
          "error_type": "missing_column | type_mismatch | renamed_column | dropped_source | ref_not_found",
          "error_description": "Exactly what is broken — name the specific column from the SQL above",
          "error_location": {{
            "file": "relative/path/to/file/that/needs/fixing.sql",
            "clause": "SELECT | JOIN | WHERE | FROM | source | ref",
            "approximate_line": null
          }},
          "fix": {{
            "description": "Concrete action to resolve this specific error",
            "fix_type": "update_sql_ref | add_cast | rename_column | revert_change | update_source_yaml | contact_owner",
            "target_file": "relative/path/to/file/to/edit.sql",
            "code_snippet": "Ready-to-apply SQL or YAML. null if not applicable."
          }}
        }}
      ]
    }}
  ]
}}

RULES:
- Only flag a downstream asset as broken if its SQL ACTUALLY references a column that changed.
  If the SQL does not reference any changed column, do NOT include it in downstream_impacts.
- downstream_impacts must be deduplicated by FQN — one entry per broken asset
- Each cause must reference a specific column name from the SQL and the changed asset schema
- patch_evidence must be copied verbatim from the diff lines above
- If no downstream assets reference any changed columns, return downstream_impacts as an
  empty array and safe_to_merge as true
- severity must be one of exactly: {severity_values}"""


def _parse_pr_ai_response(response: dict) -> Optional[PRRootCause]:
    """
    Parses the raw AI response dict into a PRRootCause model.
    Validates required top-level keys first, then constructs nested models
    explicitly. Skips malformed entries with warnings rather than crashing.
    """
    required_keys = {"pr_summary", "overall_severity", "safe_to_merge", "confidence", "changed_assets", "downstream_impacts"}
    missing = required_keys - set(response.keys())
    if missing:
        print(f"ERROR _parse_pr_ai_response: Missing required keys: {missing}")
        return None

    changed_assets: List[ChangedAssetSummary] = []
    for i, ca in enumerate(response.get("changed_assets", [])):
        try:
            changed_assets.append(ChangedAssetSummary(
                fqn=ca["fqn"],
                filename=ca["filename"],
                change_type=ca["change_type"],
                change_description=ca["change_description"],
                patch_evidence=ca.get("patch_evidence", ""),
                fqn_approximate=bool(ca.get("fqn_approximate", False))
            ))
        except Exception as e:
            print(f"WARNING _parse_pr_ai_response: Skipping malformed changed_asset[{i}]: {e}")

    downstream_impacts: List[DownstreamImpact] = []
    for i, di in enumerate(response.get("downstream_impacts", [])):
        try:
            causes: List[AssetCause] = []
            for j, cause in enumerate(di.get("causes", [])):
                try:
                    loc = cause.get("error_location", {})
                    fix = cause.get("fix", {})
                    causes.append(AssetCause(
                        source_asset_fqn=cause["source_asset_fqn"],
                        error_type=cause["error_type"],
                        error_description=cause["error_description"],
                        error_location=ErrorLocation(
                            file=loc["file"],
                            clause=loc["clause"],
                            approximate_line=loc.get("approximate_line")
                        ),
                        fix=CauseFix(
                            description=fix["description"],
                            fix_type=fix["fix_type"],
                            target_file=fix["target_file"],
                            code_snippet=fix.get("code_snippet")
                        )
                    ))
                except Exception as e:
                    print(f"WARNING _parse_pr_ai_response: Skipping malformed cause[{i}][{j}]: {e}")

            downstream_impacts.append(DownstreamImpact(
                fqn=di["fqn"],
                display_name=di.get("display_name", di["fqn"]),
                severity=SeverityLevel(di["severity"]),
                causes=causes
            ))
        except Exception as e:
            print(f"WARNING _parse_pr_ai_response: Skipping malformed downstream_impact[{i}]: {e}")

    try:
        return PRRootCause(
            pr_summary=response["pr_summary"],
            overall_severity=SeverityLevel(response["overall_severity"]),
            safe_to_merge=bool(response["safe_to_merge"]),
            confidence=float(response["confidence"]),
            changed_assets=changed_assets,
            downstream_impacts=downstream_impacts
        )
    except Exception as e:
        print(f"ERROR _parse_pr_ai_response: Failed to construct PRRootCause: {e}")
        return None


def call_pr_ai_layer(ai_context: str, max_retries: int = 3) -> Optional[PRRootCause]:
    """
    Calls the configured LLM and parses the response into a PRRootCause.
    Used by the PR bot investigation flow only.
    """
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

    for attempt in range(max_retries):
        try:
            if DEFAULT_LLM_PROVIDER == "groq" or AI_MODEL.startswith("llama"):
                response = _call_groq(ai_context, GROQ_API_KEY)
            elif AI_MODEL.startswith("gpt"):
                response = _call_openai(ai_context)
            else:
                response = _call_claude(ai_context)

            if response:
                pr_root_cause = _parse_pr_ai_response(response)
                if pr_root_cause:
                    return pr_root_cause
                else:
                    print(f"WARNING call_pr_ai_layer: Parse failed on attempt {attempt + 1}, retrying")

        except Exception as e:
            print(f"ERROR call_pr_ai_layer attempt {attempt + 1}: {e}")

        if attempt < max_retries - 1:
            continue

    print(f"ERROR call_pr_ai_layer: Failed after {max_retries} attempts")
    return None


# ── run_pr_investigation ───────────────────────────────────────────────────────

def run_pr_investigation(
    investigation_id: str,
    user_id: str,
    connection_id: str,
    asset_fqn_map: Dict[str, Tuple[str, bool, str]],
    pr_number: int,
    gh_token: str,
    repo_owner: str,
    repo_name: str,
    comment_id: str,
    openmetadata_url: Optional[str] = None,
    openmetadata_token: Optional[str] = None,
) -> bool:
    """
    Full PR investigation pipeline. Called as a background task by the webhook handler.

    Steps:
        1.  Traverse lineage for each FQN (upstream + downstream in one call)
        2.  Merge all subgraphs
        2b. Build downstream context — Layer 2 (schemas) + Layer 3 (SQL) concurrently
        3.  Build PR-specific AI prompt (enriched with schema + downstream SQL)
        4.  Call AI → PRRootCause
        5.  Store result on investigation document
        6.  Update PR comment with full analysis
    """
    from controllers.github_controller import render_pr_comment, update_pr_comment

    start_time = datetime.now(timezone.utc)

    try:
        # ── Step 1: Lineage traversal per asset ───────────────────────────────
        print(f"DEBUG run_pr_investigation: Step 1 - Traversing lineage for {len(asset_fqn_map)} assets")
        update_investigation_status(investigation_id, InvestigationStatus.LINEAGE_TRAVERSAL)
 
        subgraphs: List[Tuple[str, LineageSubgraph]] = []
        graph = None  # repo graph — used by both Step 1 and Step 2b in Option B
 
        if USE_OPENMETADATA:
            # ── OPTION A: OpenMetadata (original — preserved exactly) ─────────
            print(f"DEBUG run_pr_investigation: Step 1 [OPTION A - OpenMetadata]")
            for filename, (fqn, approximate, stripped_patch) in asset_fqn_map.items():
                try:
                    nodes = lineage_controller.traverse_upstream(
                        openmetadata_url, openmetadata_token, fqn, max_depth=3
                    )
 
                    if not nodes:
                        print(f"WARNING run_pr_investigation: No lineage found for {fqn} ({filename})")
                        continue
 
                    nodes = lineage_controller.detect_break_point(nodes)
 
                    subgraph = LineageSubgraph(
                        failing_asset_fqn=fqn,
                        nodes=nodes,
                        edges=[],
                        traversal_depth=len(nodes)
                    )
                    subgraphs.append((fqn, subgraph))
 
                    downstream_count = sum(1 for n in nodes if n.is_downstream)
                    print(
                        f"DEBUG run_pr_investigation: {fqn} — "
                        f"{len(nodes)} nodes ({downstream_count} downstream consumers)"
                    )
 
                except Exception as e:
                    print(f"WARNING run_pr_investigation: Lineage traversal failed for {fqn}: {e}")
                    continue
 
        else:
            # ── OPTION B: Repo parser (new — zero OpenMetadata calls) ─────────
            print(f"DEBUG run_pr_investigation: Step 1 [OPTION B - repo graph]")
            from controllers.repo_parser_controller import (
                get_repo_graph,
                build_subgraph_from_graph,
            )
 
            repo_full_name = f"{repo_owner}/{repo_name}"
            graph = get_repo_graph(
                connection_id=connection_id,
                repo_full_name=repo_full_name
                )
            if not graph:
                print(
                    f"DEBUG run_pr_investigation: No cached graph for {repo_full_name} "
                    f"— triggering on-demand scan now"
                )
                from controllers.repo_parser_controller import scan_repo
                graph = scan_repo(
                    github_token=gh_token,
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                    connection_id=connection_id,
                    user_id=user_id,
                )

            if not graph or not graph.nodes:
                print(f"WARNING run_pr_investigation: Graph empty after scan — patch-only analysis")
            else:
                for filename, (fqn, approximate, stripped_patch) in asset_fqn_map.items():
                                try:
                                    subgraph = build_subgraph_from_graph(graph, fqn)
            
                                    if subgraph and subgraph.nodes:
                                        subgraphs.append((fqn, subgraph))
                                        downstream_count = sum(1 for n in subgraph.nodes if n.is_downstream)
                                        print(
                                            f"DEBUG run_pr_investigation: {fqn} — "
                                            f"{len(subgraph.nodes)} nodes ({downstream_count} downstream consumers)"
                                        )
                                    else:
                                        print(
                                            f"WARNING run_pr_investigation: "
                                            f"{fqn} not found in repo graph — skipping"
                                        )
            
                                except Exception as e:
                                    print(f"WARNING run_pr_investigation: Graph traversal failed for {fqn}: {e}")
                                    continue

        # ── Step 2: Merge subgraphs ────────────────────────────────────────────
        if subgraphs:
            print(f"DEBUG run_pr_investigation: Step 2 - Merging {len(subgraphs)} subgraphs")
            merged_subgraph = merge_lineage_subgraphs(subgraphs)
        else:
            print(f"WARNING run_pr_investigation: No lineage found for any asset — running patch-only analysis")
            all_fqns = [fqn for _, (fqn, _, _) in asset_fqn_map.items()]
            merged_subgraph = LineageSubgraph(
                failing_asset_fqn=", ".join(all_fqns),
                nodes=[],
                edges=[],
                traversal_depth=0
            )

        update_investigation_status(investigation_id, InvestigationStatus.CONTEXT_BUILDING)

        investigations_collection.update_one(
            {"_id": ObjectId(investigation_id)},
            {"$set": {"lineage_subgraph": merged_subgraph.model_dump()}}
        )

        # ── Step 2b: Build downstream context ────────────────────────────────
        print(f"DEBUG run_pr_investigation: Step 2b - Building downstream context")
 
        if USE_OPENMETADATA:
            # ── OPTION A: OpenMetadata (original — preserved exactly) ─────────
            downstream_context = build_downstream_context(
                openmetadata_url=openmetadata_url,
                openmetadata_token=openmetadata_token,
                github_token=gh_token,
                repo_owner=repo_owner,
                repo_name=repo_name,
                asset_fqn_map=asset_fqn_map,
                merged_subgraph=merged_subgraph
            )
 
        else:
            # ── OPTION B: Build context from repo graph directly ──────────────
            from controllers.repo_parser_controller import get_downstream
 
            downstream_context = {
                "changed_asset_schemas": {},
                "downstream_sqls":       {},
            }
 
            if graph:
                for filename, (fqn, approximate, stripped_patch) in asset_fqn_map.items():
 
                    # Layer 2: column schema of changed asset (from graph)
                    node = graph.nodes.get(fqn)
                    if not node:
                        # Try suffix match
                        for candidate_fqn, candidate_node in graph.nodes.items():
                            if candidate_fqn.endswith(f".{fqn}") or candidate_fqn == fqn:
                                node = candidate_node
                                break
 
                    if node and node.columns:
                        downstream_context["changed_asset_schemas"][fqn] = [
                            {"name": col, "dataType": "UNKNOWN"}
                            for col in node.columns
                        ]
                    else:
                        downstream_context["changed_asset_schemas"][fqn] = []
 
                    # Layer 3: SQL of downstream consumers (from graph — no GitHub search needed)
                    downstream_nodes = get_downstream(graph, fqn, depth=3)
                    for downstream_node in downstream_nodes:
                        if downstream_node.sql:
                            downstream_context["downstream_sqls"][downstream_node.fqn] = (
                                downstream_node.sql
                            )
                        else:
                            downstream_context["downstream_sqls"][downstream_node.fqn] = None
 
                fetched_schemas = sum(1 for v in downstream_context["changed_asset_schemas"].values() if v)
                fetched_sqls    = sum(1 for v in downstream_context["downstream_sqls"].values() if v)
                print(
                    f"DEBUG run_pr_investigation: Step 2b [OPTION B] "
                    f"Got {fetched_schemas}/{len(asset_fqn_map)} schemas, "
                    f"{fetched_sqls}/{len(downstream_context['downstream_sqls'])} downstream SQLs "
                    f"from repo graph"
                )
            else:
                print(
                    f"DEBUG run_pr_investigation: Step 2b [OPTION B] "
                    f"No graph available — AI will reason from patch diff only"
                )

        # ── Step 3: Build PR AI prompt ────────────────────────────────────────
        print(f"DEBUG run_pr_investigation: Step 3 - Building PR AI context")
        ai_context = build_pr_ai_context(
            asset_fqn_map=asset_fqn_map,
            merged_subgraph=merged_subgraph,
            pr_number=pr_number,
            downstream_context=downstream_context
        )
        estimated_tokens = len(ai_context) // 4
        print(f"DEBUG run_pr_investigation: Estimated prompt tokens: ~{estimated_tokens}")

        # ── Step 4: AI analysis ───────────────────────────────────────────────
        print(f"DEBUG run_pr_investigation: Step 4 - Calling PR AI layer")
        update_investigation_status(investigation_id, InvestigationStatus.AI_ANALYSIS)

        pr_root_cause = call_pr_ai_layer(ai_context)

        if not pr_root_cause:
            print(f"ERROR run_pr_investigation: PR AI layer failed")
            update_investigation_status(investigation_id, InvestigationStatus.FAILED)
            return False

        # ── Step 5: Store result ──────────────────────────────────────────────
        print(f"DEBUG run_pr_investigation: Step 5 - Storing result")
        processing_time_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

        investigations_collection.update_one(
            {"_id": ObjectId(investigation_id)},
            {
                "$set": {
                    "status":             InvestigationStatus.COMPLETED,
                    "pr_root_cause":      pr_root_cause.model_dump(),
                    "completed_at":       datetime.now(timezone.utc).isoformat(),
                    "processing_time_ms": processing_time_ms,
                    "updated_at":         datetime.now(timezone.utc).isoformat()
                }
            }
        )

        # ── Step 6: Update PR comment ─────────────────────────────────────────
        print(f"DEBUG run_pr_investigation: Step 6 - Updating PR comment {comment_id}")
        comment_body = render_pr_comment(pr_root_cause, investigation_id)

        update_pr_comment(
            github_token=gh_token,
            repo_owner=repo_owner,
            repo_name=repo_name,
            comment_id=comment_id,
            comment_body=comment_body
        )

        print(f"DEBUG run_pr_investigation: Completed in {processing_time_ms}ms")
        return True

    except Exception as e:
        print(f"ERROR run_pr_investigation: {e}")
        update_investigation_status(investigation_id, InvestigationStatus.FAILED)
        return False