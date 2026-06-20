"""
investigation_controller.py — Investigation lifecycle for Pipeline Autopsy (PR Bot Only).

OPENMETADATA FULLY REMOVED — Using repo_parser (zero OpenMetadata dependencies)

Function organisation:
  ── Shared utilities ──────────────────────────────────────────────────────────
  create_investigation          — creates an Investigation document in MongoDB
  update_investigation_status   — updates status field
  get_investigation             — fetches + deserialises a full InvestigationResponse
  list_investigations           — compact list for sidebar

  ── PR bot investigation flow (repo_parser only) ──────────────────────────────
  merge_lineage_subgraphs       — merges N subgraphs, deduplicates nodes by FQN
  build_pr_ai_context           — prompt builder for multi-asset PR flow
  call_pr_ai_layer              — LLM call returning PRRootCause
  run_pr_investigation          — entry point called by PR webhook background task

Lineage Source: GitHub repo_parser (zero OpenMetadata dependencies).
  - Layer 2: column schema of changed asset (from repo graph)
  - Layer 3: SQL of each downstream consumer (from repo graph)

Shared LLM providers: _call_groq / _call_openai / _call_claude

CHANGES (contract validation):
  - build_pr_ai_context now accepts violations: List[ContractViolation]
    and injects a structured violation block into the AI prompt
  - run_pr_investigation adds Step 2b: fetches new file content from GitHub,
    calls validate_contracts, and hard-overrides AI verdict on critical/high
  - run_investigation mirrors the same Step 2b logic
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
    InvestigationListItem
)
from models.github import (
    PRRootCause, ChangedAssetSummary, DownstreamImpact,
    AssetCause, ErrorLocation, CauseFix
)
from models.base import InvestigationStatus, SeverityLevel
from models.lineage import LineageSubgraph, LineageNode, LineageEdge
from controllers import event_controller

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
GROQ_API_KEY         = os.getenv("GROQ_API_KEY", "")

# Maximum downstream nodes to include in prompt — protects against token overflow.
MAX_DOWNSTREAM_SQL_FETCHES = 20


# ── Shared utilities ──────────────────────────────────────────────────────────

def create_investigation(
    user_id: str,
    connection_id: str,
    event_id: str,
    failure_message: str,
    asset_fqn: Optional[str] = None,
    event_type: str = "github_pr"
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
    """Reconstruct PRRootCause from MongoDB dict."""
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

        raw_prc = investigation.get("pr_root_cause")
        raw_lg  = investigation.get("lineage_subgraph")

        # ── Deserialise PR root cause
        pr_root_cause_obj: Optional[PRRootCause] = None
        if raw_prc:
            try:
                pr_root_cause_obj = _deserialise_pr_root_cause(raw_prc)
            except Exception as e:
                print(f"WARNING get_investigation: could not parse pr_root_cause: {e}")

        # ── Deserialise lineage subgraph
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
            event_type=investigation.get("event_type", "github_pr"),
            status=investigation.get("status", InvestigationStatus.PENDING),
            root_cause=None,
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
            pr_root_cause = inv.get("pr_root_cause")
            summary = pr_root_cause.get("pr_summary") if pr_root_cause else None
            downstream_impacts = pr_root_cause.get("downstream_impacts", []) if pr_root_cause else []
            has_critical = any(di.get("severity") == "critical" for di in downstream_impacts)

            result.append(InvestigationListItem(
                id=str(inv["_id"]),
                failing_asset_fqn=inv.get("failing_asset_fqn", ""),
                event_type=inv.get("event_type", "github_pr"),
                status=inv.get("status", InvestigationStatus.PENDING),
                summary=summary,
                affected_count=len(downstream_impacts),
                has_critical_impact=has_critical,
                created_at=str(inv.get("created_at", "")),
                processing_time_ms=inv.get("processing_time_ms")
            ))

        return result

    except Exception as e:
        print(f"ERROR list_investigations: {e}")
        return []


# ── Shared LLM provider adapters ──────────────────────────────────────────────

def _call_groq(prompt: str) -> Optional[dict]:
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type":  "application/json"
        }
        data = {
            "model": AI_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a data pipeline expert. Always respond with valid JSON only. No markdown, no backticks."
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
                    "content": "You are a data pipeline expert. Always respond with valid JSON only. No markdown, no backticks."
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
            "system":     "You are a data pipeline expert. Always respond with valid JSON only. No markdown, no backticks.",
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


# ── Contract violation helpers ────────────────────────────────────────────────

def _build_violation_block(violations) -> str:
    if not violations:
        return (
            "═══════════════════════════════════════\n"
            "STATIC CONTRACT ANALYSIS\n"
            "═══════════════════════════════════════\n"
            "No violations detected by static analysis.\n"
        )

    lines = [
        "═══════════════════════════════════════",
        "STATIC CONTRACT ANALYSIS  ← TRUST THIS OVER YOUR OWN READING",
        "═══════════════════════════════════════",
        f"Found {len(violations)} violation(s). These are CONFIRMED breakages, not guesses.",
        "",
    ]

    for i, v in enumerate(violations, 1):
        col_note = f" | column: {v.column}" if v.column else ""
        lines.append(
            f"{i}. [{v.severity.upper()}] {v.violation_type}"
            f" | {v.changed_fqn} → {v.affected_fqn}{col_note}"
        )
        lines.append(f"   Detail : {v.detail}")
        lines.append(f"   Fix    : {v.fix_hint}")
        lines.append(f"   File   : {v.file_path}")
        lines.append("")

    return "\n".join(lines)


def _apply_violation_override(
    pr_root_cause: PRRootCause,
    violations,
) -> PRRootCause:
    if not violations:
        return pr_root_cause

    critical_viols = [v for v in violations if v.severity == "critical"]
    high_viols     = [v for v in violations if v.severity == "high"]

    if not critical_viols and not high_viols:
        medium_count = len([v for v in violations if v.severity == "medium"])
        pr_root_cause.pr_summary = (
            f"[Static analysis: {medium_count} medium risk(s) detected] "
            + pr_root_cause.pr_summary
        )
        return pr_root_cause

    if critical_viols:
        banner = (
            f"[Static analysis: {len(critical_viols)} CRITICAL violation(s)"
            + (f" + {len(high_viols)} high" if high_viols else "")
            + " — merge blocked] "
        )
        pr_root_cause.safe_to_merge    = False
        pr_root_cause.overall_severity = SeverityLevel.CRITICAL
        pr_root_cause.confidence       = 0.99
    else:
        banner = f"[Static analysis: {len(high_viols)} HIGH violation(s) — merge blocked] "
        pr_root_cause.safe_to_merge = False
        if pr_root_cause.overall_severity not in (SeverityLevel.CRITICAL,):
            pr_root_cause.overall_severity = SeverityLevel.HIGH
        pr_root_cause.confidence = 0.90

    pr_root_cause.pr_summary = banner + pr_root_cause.pr_summary

    existing_affected_fqns = {di.fqn for di in pr_root_cause.downstream_impacts}

    for v in (critical_viols + high_viols):
        if v.affected_fqn in existing_affected_fqns:
            continue

        pr_root_cause.downstream_impacts.append(
            DownstreamImpact(
                fqn=v.affected_fqn,
                display_name=v.affected_fqn.split(".")[-1],
                severity=SeverityLevel(v.severity),
                causes=[\
                    AssetCause(
                        source_asset_fqn=v.changed_fqn,
                        error_type=v.violation_type,
                        error_description=v.detail,
                        error_location=ErrorLocation(
                            file=v.file_path,
                            clause="STATIC_ANALYSIS",
                            approximate_line=None,
                        ),
                        fix=CauseFix(
                            description=v.fix_hint,
                            fix_type="update_sql",
                            target_file=v.file_path,
                            code_snippet=None,
                        ),
                    )
                ],
            )
        )
        existing_affected_fqns.add(v.affected_fqn)

    print(
        f"DEBUG _apply_violation_override: "
        f"Overrode verdict -> safe_to_merge={pr_root_cause.safe_to_merge}, "
        f"severity={pr_root_cause.overall_severity}, "
        f"confidence={pr_root_cause.confidence}"
    )
    return pr_root_cause


def _collect_new_column_map(
    graph,
    asset_fqn_map: Dict[str, Tuple[str, bool, str]],
    gh_token: str,
    repo_owner: str,
    repo_name: str,
    pr_head_ref: str = "",
) -> Tuple[List[str], Dict[str, List[str]], Dict[str, Dict[str, str]], Dict[str, Dict], Dict[str, set], Dict[str, str]]:
    """
    Fetches the new column list, types, defaults, and NOT NULL constraints
    for every migration file changed in this PR.

    Returns:
      (changed_fqns, new_column_map, new_type_map, new_default_map,
       new_not_null_map, patch_map)
    """
    from controllers.repo_parser_controller import (
        _fetch_file_content_at_ref,
        _extract_migration_columns,
        NODE_TYPE_MIGRATION,
    )
    from validators.type_change import extract_column_types, extract_types_from_patch
    from validators.default_change import extract_column_defaults, extract_not_null_columns

    changed_fqns:    List[str]            = []
    new_column_map:  Dict[str, List[str]] = {}
    new_type_map:    Dict[str, Dict[str, str]] = {}
    new_default_map: Dict[str, Dict]      = {}
    new_not_null_map: Dict[str, set]      = {}
    patch_map:       Dict[str, str]       = {}

    for filename, (fqn, approximate, stripped_patch) in asset_fqn_map.items():
        changed_fqns.append(fqn)

        if not graph:
            continue

        # ── Resolve node in graph ─────────────────────────────────────────────
        resolved_fqn = fqn
        node = graph.nodes.get(fqn)
        if not node:
            for candidate_fqn, candidate_node in graph.nodes.items():
                if candidate_fqn.endswith(f".{fqn}") or candidate_fqn == fqn:
                    node = candidate_node
                    resolved_fqn = candidate_fqn
                    break

        if not node or node.node_type != NODE_TYPE_MIGRATION:
            print(f"DEBUG _collect_new_column_map: {fqn} is not a migration node — skipping column fetch")
            continue

        # Store patch for ALTER TABLE validator
        if stripped_patch:
            patch_map[resolved_fqn] = stripped_patch

        print(
            f"DEBUG _collect_new_column_map: fetching {node.file_path} "
            f"at ref='{pr_head_ref}' for {resolved_fqn}"
        )

        # ── Strategy 1: fetch from PR branch ─────────────────────────────────
        new_sql = _fetch_file_content_at_ref(
            gh_token,
            repo_owner,
            repo_name,
            node.file_path,
            ref=pr_head_ref,
        )

        if new_sql is not None:
            cols = _extract_migration_columns(new_sql)
            new_column_map[resolved_fqn] = cols
            new_type_map[resolved_fqn] = extract_column_types(new_sql)
            new_default_map[resolved_fqn] = extract_column_defaults(new_sql)
            new_not_null_map[resolved_fqn] = extract_not_null_columns(new_sql)
            print(
                f"DEBUG _collect_new_column_map: {resolved_fqn} -> "
                f"{len(cols)} columns from branch fetch: {cols}"
            )
            continue

        print(
            f"WARNING _collect_new_column_map: branch fetch returned None for "
            f"{node.file_path}@{pr_head_ref} — trying patch fallback"
        )

        # ── Strategy 2: extract added/removed columns from the diff patch ────
        if stripped_patch:
            patch_cols   = _extract_columns_from_patch(stripped_patch)
            removed_cols = _extract_removed_columns_from_patch(stripped_patch)
            if patch_cols or removed_cols:
                # Merge: start from stored columns, subtract removals, add additions
                stored_cols = list(node.columns) if node.columns else []
                merged = [c for c in stored_cols if c not in removed_cols]
                for c in patch_cols:
                    if c not in merged:
                        merged.append(c)
                new_column_map[resolved_fqn] = merged

                # Merge types from patch with stored types
                stored_types = dict(node.raw_metadata.get("column_types", {}))
                for rc in removed_cols:
                    stored_types.pop(rc, None)
                patch_types = extract_types_from_patch(stripped_patch)
                stored_types.update(patch_types)
                new_type_map[resolved_fqn] = stored_types

                # Defaults: use stored minus removed
                stored_defaults = dict(node.raw_metadata.get("column_defaults", {}))
                for rc in removed_cols:
                    stored_defaults.pop(rc, None)
                new_default_map[resolved_fqn] = stored_defaults

                print(
                    f"DEBUG _collect_new_column_map: {resolved_fqn} → "
                    f"{len(merged)} columns from patch fallback "
                    f"(added={patch_cols}, removed={removed_cols}): {merged}"
                )
                continue

        # ── Strategy 3: fall back to stored graph columns ─────────────────────
        stored_cols = list(node.columns) if node.columns else []
        new_column_map[resolved_fqn] = stored_cols
        new_type_map[resolved_fqn] = dict(node.raw_metadata.get("column_types", {}))
        new_default_map[resolved_fqn] = dict(node.raw_metadata.get("column_defaults", {}))
        print(
            f"WARNING _collect_new_column_map: {resolved_fqn} -> "
            f"using stored graph columns as fallback ({len(stored_cols)} cols). "
            f"Contract validation may be imprecise."
        )

    return (changed_fqns, new_column_map, new_type_map,
            new_default_map, new_not_null_map, patch_map)


def _extract_columns_from_patch(patch: str) -> List[str]:
    """
    Extracts column names from lines ADDED in a diff patch (+lines).

    Handles both CREATE TABLE body lines and ALTER TABLE ADD COLUMN lines.
    Returns lowercased column names.
    """
    import re
    columns: List[str] = []
    seen: set = set()

    skip = re.compile(
        r'^\s*(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|CONSTRAINT|INDEX|KEY|CREATE|ALTER|\))',
        re.IGNORECASE
    )

    for line in patch.splitlines():
        if not line.startswith("+"):
            continue
        content = line[1:].strip().rstrip(",")
        if not content or skip.match(content):
            continue

        # ALTER TABLE ADD COLUMN col_name TYPE
        alter_match = re.match(
            r'ALTER\s+TABLE\s+\w+\s+ADD\s+(?:COLUMN\s+)?(\w+)\s+\w+',
            content,
            re.IGNORECASE
        )
        if alter_match:
            col = alter_match.group(1).lower()
            if col not in seen:
                seen.add(col)
                columns.append(col)
            continue

        # CREATE TABLE body line: col_name TYPE ...
        col_match = re.match(r'^(\w+)\s+\w+', content)
        if col_match:
            col = col_match.group(1).lower()
            if col not in seen:
                seen.add(col)
                columns.append(col)

    return columns


def _extract_removed_columns_from_patch(patch: str) -> List[str]:
    """
    Extracts column names from lines REMOVED in a diff patch (-lines).
    Used to subtract dropped columns from the stored baseline.
    """
    import re
    columns: List[str] = []
    seen: set = set()

    skip = re.compile(
        r'^\s*(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|CONSTRAINT|INDEX|KEY|CREATE|ALTER|\))',
        re.IGNORECASE
    )

    for line in patch.splitlines():
        if not line.startswith("-"):
            continue
        content = line[1:].strip().rstrip(",")
        if not content or skip.match(content):
            continue

        alter_match = re.match(
            r'ALTER\s+TABLE\s+\w+\s+DROP\s+(?:COLUMN\s+)?(\w+)',
            content,
            re.IGNORECASE
        )
        if alter_match:
            col = alter_match.group(1).lower()
            if col not in seen:
                seen.add(col)
                columns.append(col)
            continue

        col_match = re.match(r'^(\w+)\s+\w+', content)
        if col_match:
            col = col_match.group(1).lower()
            if col not in seen:
                seen.add(col)
                columns.append(col)

    return columns


# ── PR bot investigation flow ──────────────────────────────────────────────────

def merge_lineage_subgraphs(
    subgraphs: List[Tuple[str, LineageSubgraph]]
) -> LineageSubgraph:
    """Merges multiple per-asset lineage subgraphs into one unified subgraph."""
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
                if node.is_downstream:
                    existing.is_downstream = True

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
        f"{len(merged.nodes)} unique nodes ({downstream_count} downstream), "
        f"{len(merged.edges)} edges from {len(subgraphs)} subgraphs"
    )
    return merged


def build_pr_ai_context(
    asset_fqn_map: Dict[str, Tuple[str, bool, str]],
    merged_subgraph: LineageSubgraph,
    pr_number: int,
    downstream_context: Optional[dict] = None,
    violations=None,
) -> str:
    changed_asset_schemas = (downstream_context or {}).get("changed_asset_schemas", {})
    downstream_sqls       = (downstream_context or {}).get("downstream_sqls", {})

    # Section 1: Changed assets with diff + current schema
    changed_section_parts = []
    for i, (filename, (fqn, approximate, stripped_patch)) in enumerate(asset_fqn_map.items(), 1):
        approx_note = " (FQN approximate)" if approximate else ""

        schema_cols = changed_asset_schemas.get(fqn, [])
        if schema_cols:
            schema_lines = "\n".join(
                f"    - {col['name']:<30} {col['dataType']}"
                for col in schema_cols
            )
            schema_block = f"   Schema (from repo_parser):\n{schema_lines}"
        else:
            schema_block = "   Schema: not available"

        changed_section_parts.append(
            f"{i}. FQN: {fqn}{approx_note}\n"
            f"   File: {filename}\n"
            f"{schema_block}\n"
            f"   Changes:\n"
            f"{stripped_patch or '   (no patch available)'}"
        )
    changed_section = "\n\n".join(changed_section_parts)

    # Section 2: Downstream consumer SQL
    if downstream_sqls:
        downstream_parts = []
        for idx, (fqn, sql_content) in enumerate(downstream_sqls.items(), 1):
            if sql_content:
                sql_lines = sql_content.splitlines()
                if len(sql_lines) > 150:
                    sql_display = (
                        "\n".join(sql_lines[:150])
                        + f"\n... ({len(sql_lines) - 150} more lines)"
                    )
                else:
                    sql_display = sql_content
                downstream_parts.append(
                    f"{idx}. FQN: {fqn}\n"
                    f"   SQL:\n"
                    f"   ```sql\n{sql_display}\n   ```"
                )
            else:
                downstream_parts.append(f"{idx}. FQN: {fqn}\n   SQL: NOT FOUND IN REPO")
        downstream_section = "\n\n".join(downstream_parts)
    else:
        downstream_section = "No downstream consumers found."

    # Section 3: Lineage graph
    lineage_parts = []
    for node in merged_subgraph.nodes:
        sources = node.raw_metadata.get("source_assets", [])
        source_note = f" [from: {', '.join(sources)}]" if sources else ""
        break_note  = " ← BREAK" if node.is_break_point else ""
        down_note   = " ← DOWNSTREAM" if node.is_downstream else ""
        lineage_parts.append(
            f"- {node.display_name} ({node.asset_type.value}): {node.fqn}"
            f"{source_note}{break_note}{down_note}"
        )
    lineage_section = "\n".join(lineage_parts) if lineage_parts else "(no lineage)"

    # Section 4: Static contract violations
    violation_section = _build_violation_block(violations)

    severity_values = " | ".join(s.value for s in SeverityLevel)

    if violations:
        critical_count = sum(1 for v in violations if v.severity == "critical")
        high_count     = sum(1 for v in violations if v.severity == "high")
        violation_instruction = (
            f"\nIMPORTANT: Static analysis already confirmed {len(violations)} violation(s) "
            f"({critical_count} critical, {high_count} high). "
            f"You MUST reflect these in downstream_impacts. "
            f"safe_to_merge MUST be false if any critical or high violations are listed above."
        )
    else:
        violation_instruction = ""

    return f"""You are a data lineage expert analyzing GitHub PR #{pr_number}.
Check the downstream SQL carefully: does each consumer reference changed columns?

═══════════════════════════════════════
CHANGED ASSETS ({len(asset_fqn_map)} files)
═══════════════════════════════════════
{changed_section}

═══════════════════════════════════════
DOWNSTREAM SQL (fetched from repo_parser)
═══════════════════════════════════════
{downstream_section}

═══════════════════════════════════════
LINEAGE
═══════════════════════════════════════
{lineage_section}

{violation_section}
{violation_instruction}

═══════════════════════════════════════
RESPONSE (JSON only, no markdown)
═══════════════════════════════════════
{{
  "pr_summary": "One sentence: what changed and impact",
  "overall_severity": "{severity_values}",
  "safe_to_merge": false,
  "confidence": 0.85,
  "changed_assets": [
    {{
      "fqn": "exact FQN",
      "filename": "exact filename",
      "change_type": "column_added | column_dropped | column_type_changed | source_renamed | other",
      "change_description": "What changed",
      "patch_evidence": "Copy diff lines",
      "fqn_approximate": false
    }}
  ],
  "downstream_impacts": [
    {{
      "fqn": "broken_downstream_fqn",
      "display_name": "name",
      "severity": "{severity_values}",
      "causes": [
        {{
          "source_asset_fqn": "changed asset FQN",
          "error_type": "missing_column | type_mismatch | renamed_column | other",
          "error_description": "What's broken",
          "error_location": {{"file": "path", "clause": "SELECT | JOIN | WHERE", "approximate_line": null}},
          "fix": {{"description": "How to fix", "fix_type": "update_sql | rename | revert | other", "target_file": "path", "code_snippet": null}}
        }}
      ]
    }}
  ]
}}

RULES:
- Only flag broken downstream if SQL ACTUALLY references changed columns
- severity must be one of: {severity_values}
- If no downstream assets break, return downstream_impacts as empty array and safe_to_merge as true"""


def _parse_pr_ai_response(response: dict) -> Optional[PRRootCause]:
    """Parses the AI response dict into a PRRootCause model."""
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
                    print(f"WARNING _parse_pr_ai_response: Skipping cause[{i}][{j}]: {e}")

            downstream_impacts.append(DownstreamImpact(
                fqn=di["fqn"],
                display_name=di.get("display_name", di["fqn"]),
                severity=SeverityLevel(di["severity"]),
                causes=causes
            ))
        except Exception as e:
            print(f"WARNING _parse_pr_ai_response: Skipping downstream_impact[{i}]: {e}")

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
    """Calls LLM and parses response into PRRootCause."""
    for attempt in range(max_retries):
        try:
            if DEFAULT_LLM_PROVIDER == "groq" or AI_MODEL.startswith("llama"):
                response = _call_groq(ai_context)
            elif AI_MODEL.startswith("gpt"):
                response = _call_openai(ai_context)
            else:
                response = _call_claude(ai_context)

            if response:
                pr_root_cause = _parse_pr_ai_response(response)
                if pr_root_cause:
                    return pr_root_cause

        except Exception as e:
            print(f"ERROR call_pr_ai_layer attempt {attempt + 1}: {e}")

    print(f"ERROR call_pr_ai_layer: Failed after {max_retries} attempts")
    return None


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
    pr_head_ref: str = "",
) -> bool:
    from controllers.github_controller import render_pr_comment, update_pr_comment

    start_time = datetime.now(timezone.utc)

    try:
        print(f"DEBUG run_pr_investigation: Step 1 - Scanning repo (pr_head_ref='{pr_head_ref}')")
        update_investigation_status(investigation_id, InvestigationStatus.LINEAGE_TRAVERSAL)

        from controllers.repo_parser_controller import (
            get_repo_graph,
            build_subgraph_from_graph,
            scan_repo,
            get_downstream,
            validate_contracts,
        )

        subgraphs: List[Tuple[str, LineageSubgraph]] = []
        repo_full_name = f"{repo_owner}/{repo_name}"

        graph = get_repo_graph(connection_id=connection_id, repo_full_name=repo_full_name)
        if not graph:
            print(f"DEBUG run_pr_investigation: Scanning repo {repo_full_name}")
            graph = scan_repo(
                github_token=gh_token,
                repo_owner=repo_owner,
                repo_name=repo_name,
                connection_id=connection_id,
                user_id=user_id,
            )

        if not graph or not graph.nodes:
            print(f"WARNING run_pr_investigation: Graph empty — patch-only analysis")
        else:
            for filename, (fqn, approximate, stripped_patch) in asset_fqn_map.items():
                # Log patch preview so we can confirm data is flowing through
                patch_preview = stripped_patch[:200] if stripped_patch else "EMPTY"
                print(f"DEBUG run_pr_investigation: patch preview for {fqn}: {patch_preview}")
                try:
                    subgraph = build_subgraph_from_graph(graph, fqn)
                    if subgraph and subgraph.nodes:
                        subgraphs.append((fqn, subgraph))
                        downstream_count = sum(1 for n in subgraph.nodes if n.is_downstream)
                        print(f"DEBUG run_pr_investigation: {fqn} — {len(subgraph.nodes)} nodes ({downstream_count} downstream)")
                except Exception as e:
                    print(f"WARNING run_pr_investigation: Graph traversal failed for {fqn}: {e}")

        # Step 2: Merge subgraphs
        if subgraphs:
            print(f"DEBUG run_pr_investigation: Step 2 - Merging {len(subgraphs)} subgraphs")
            merged_subgraph = merge_lineage_subgraphs(subgraphs)
        else:
            print(f"WARNING run_pr_investigation: No lineage — patch-only analysis")
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

        # ── Step 2b: Contract validation ─────────────────────────────────────
        print(f"DEBUG run_pr_investigation: Step 2b - Running contract validation")
        violations = []

        if graph and graph.nodes:
            try:
                (changed_fqns, new_column_map, new_type_map,
                 new_default_map, new_not_null_map, p_map) = _collect_new_column_map(
                    graph=graph,
                    asset_fqn_map=asset_fqn_map,
                    gh_token=gh_token,
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                    pr_head_ref=pr_head_ref,
                )

                # Log the column map so we can confirm what's going into validation
                for fqn, cols in new_column_map.items():
                    print(f"DEBUG run_pr_investigation: new_column_map[{fqn}] = {cols}")

                violations = validate_contracts(
                    graph, changed_fqns, new_column_map,
                    new_type_map=new_type_map,
                    new_default_map=new_default_map,
                    new_not_null_map=new_not_null_map,
                    patch_map=p_map,
                )
                print(
                    f"DEBUG run_pr_investigation: {len(violations)} contract violation(s) — "
                    f"critical={sum(1 for v in violations if v.severity == 'critical')}, "
                    f"high={sum(1 for v in violations if v.severity == 'high')}"
                )
                for v in violations:
                    print(
                        f"DEBUG run_pr_investigation: violation — "
                        f"[{v.severity}] {v.violation_type}: "
                        f"{v.changed_fqn} → {v.affected_fqn} (col={v.column})"
                    )
            except Exception as e:
                print(f"WARNING run_pr_investigation: Contract validation failed: {e}")
                violations = []

        # Step 3: Build downstream context from repo graph
        print(f"DEBUG run_pr_investigation: Step 3 - Building downstream context")

        downstream_context = {
            "changed_asset_schemas": {},
            "downstream_sqls":       {},
        }

        if graph:
            for filename, (fqn, approximate, stripped_patch) in asset_fqn_map.items():
                node = graph.nodes.get(fqn)
                if not node:
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

                downstream_nodes = get_downstream(graph, fqn, depth=3)
                for downstream_node in downstream_nodes:
                    downstream_context["downstream_sqls"][downstream_node.fqn] = downstream_node.sql

            fetched_schemas = sum(1 for v in downstream_context["changed_asset_schemas"].values() if v)
            fetched_sqls    = sum(1 for v in downstream_context["downstream_sqls"].values() if v)
            print(f"DEBUG run_pr_investigation: Got {fetched_schemas} schemas, {fetched_sqls} SQLs")

        # Step 4: Build AI prompt
        print(f"DEBUG run_pr_investigation: Step 4 - Building AI context")
        ai_context = build_pr_ai_context(
            asset_fqn_map=asset_fqn_map,
            merged_subgraph=merged_subgraph,
            pr_number=pr_number,
            downstream_context=downstream_context,
            violations=violations,
        )
        estimated_tokens = len(ai_context) // 4
        print(f"DEBUG run_pr_investigation: Estimated tokens: ~{estimated_tokens}")

        # Step 5: AI analysis
        print(f"DEBUG run_pr_investigation: Step 5 - Calling AI")
        update_investigation_status(investigation_id, InvestigationStatus.AI_ANALYSIS)

        pr_root_cause = call_pr_ai_layer(ai_context)
        if not pr_root_cause:
            print(f"ERROR run_pr_investigation: AI layer failed")
            update_investigation_status(investigation_id, InvestigationStatus.FAILED)
            return False

        # ── Step 5b: Hard-override AI verdict if static analysis found violations
        if violations:
            pr_root_cause = _apply_violation_override(pr_root_cause, violations)

        # Step 6: Store result
        print(f"DEBUG run_pr_investigation: Step 6 - Storing result")
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

        # Step 7: Update PR comment
        print(f"DEBUG run_pr_investigation: Step 7 - Updating PR comment")
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


def run_investigation(
    investigation_id: str,
    user_id: str,
    connection_id: str,
    openmetadata_url: str = "",
    openmetadata_token: str = "",
) -> bool:
    from controllers.repo_parser_controller import (
        get_repo_graph,
        build_subgraph_from_graph,
        scan_repo,
        get_downstream,
        validate_contracts,
    )

    start_time = datetime.now(timezone.utc)

    try:
        investigation = investigations_collection.find_one(
            {"_id": ObjectId(investigation_id)}
        )
        if not investigation:
            print(f"ERROR run_investigation: Investigation {investigation_id} not found")
            return False

        failure_message = investigation.get("failure_message", "")
        asset_fqn = investigation.get("failing_asset_fqn", "")

        print(f"DEBUG run_investigation: Step 1 - Getting repo graph")
        update_investigation_status(investigation_id, InvestigationStatus.LINEAGE_TRAVERSAL)

        conn_doc = None
        try:
            from controllers import connection_controller
            conn_doc = connection_controller.get_connection_by_id(connection_id, user_id)
        except Exception as e:
            print(f"WARNING run_investigation: Could not load connection: {e}")

        if not conn_doc or not conn_doc.github_repo:
            print(f"ERROR run_investigation: No GitHub repo configured for connection {connection_id}")
            update_investigation_status(investigation_id, InvestigationStatus.FAILED)
            return False

        repo_parts = conn_doc.github_repo.split("/")
        if len(repo_parts) != 2:
            print(f"ERROR run_investigation: Invalid github_repo format: {conn_doc.github_repo}")
            update_investigation_status(investigation_id, InvestigationStatus.FAILED)
            return False

        repo_owner, repo_name = repo_parts
        repo_full_name = conn_doc.github_repo

        gh_token = None
        try:
            from controllers import github_controller
            if conn_doc.github_installation_id:
                gh_token = github_controller.get_installation_token(
                    str(conn_doc.github_installation_id)
                )
        except Exception as e:
            print(f"WARNING run_investigation: Could not get installation token: {e}")

        if not gh_token:
            gh_token = os.getenv("GITHUB_TOKEN", "")

        if not gh_token:
            print(f"ERROR run_investigation: No GitHub token available")
            update_investigation_status(investigation_id, InvestigationStatus.FAILED)
            return False

        graph = get_repo_graph(connection_id=connection_id, repo_full_name=repo_full_name)
        if not graph:
            print(f"DEBUG run_investigation: Scanning repo {repo_full_name}")
            graph = scan_repo(
                github_token=gh_token,
                repo_owner=repo_owner,
                repo_name=repo_name,
                connection_id=connection_id,
                user_id=user_id,
            )

        print(f"DEBUG run_investigation: Step 2 - Building subgraph for {asset_fqn}")
        merged_subgraph = None
        subgraphs: List[Tuple[str, LineageSubgraph]] = []

        if graph and graph.nodes:
            subgraph = build_subgraph_from_graph(graph, asset_fqn)
            if subgraph and subgraph.nodes:
                subgraphs.append((asset_fqn, subgraph))

        if subgraphs:
            merged_subgraph = merge_lineage_subgraphs(subgraphs)
        else:
            merged_subgraph = LineageSubgraph(
                failing_asset_fqn=asset_fqn,
                nodes=[],
                edges=[],
                traversal_depth=0,
            )

        update_investigation_status(investigation_id, InvestigationStatus.CONTEXT_BUILDING)
        investigations_collection.update_one(
            {"_id": ObjectId(investigation_id)},
            {"$set": {"lineage_subgraph": merged_subgraph.model_dump()}}
        )

        print(f"DEBUG run_investigation: Step 2b - Running contract validation")
        violations = []

        if graph and graph.nodes:
            try:
                synthetic_fqn_map: Dict[str, Tuple[str, bool, str]] = {
                    asset_fqn: (asset_fqn, False, "")
                }
                (changed_fqns, new_column_map, new_type_map,
                 new_default_map, new_not_null_map, p_map) = _collect_new_column_map(
                    graph=graph,
                    asset_fqn_map=synthetic_fqn_map,
                    gh_token=gh_token,
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                )
                violations = validate_contracts(
                    graph, changed_fqns, new_column_map,
                    new_type_map=new_type_map,
                    new_default_map=new_default_map,
                    new_not_null_map=new_not_null_map,
                    patch_map=p_map,
                )
                print(
                    f"DEBUG run_investigation: {len(violations)} contract violation(s) — "
                    f"critical={sum(1 for v in violations if v.severity == 'critical')}, "
                    f"high={sum(1 for v in violations if v.severity == 'high')}"
                )
            except Exception as e:
                print(f"WARNING run_investigation: Contract validation failed: {e}")
                violations = []

        print(f"DEBUG run_investigation: Step 3 - Building downstream context")
        downstream_context = {
            "changed_asset_schemas": {},
            "downstream_sqls": {},
        }

        if graph:
            node = graph.nodes.get(asset_fqn)
            if not node:
                for candidate_fqn, candidate_node in graph.nodes.items():
                    if candidate_fqn.endswith(f".{asset_fqn}") or candidate_fqn == asset_fqn:
                        node = candidate_node
                        break

            if node and node.columns:
                downstream_context["changed_asset_schemas"][asset_fqn] = [
                    {"name": col, "dataType": "UNKNOWN"} for col in node.columns
                ]

            downstream_nodes = get_downstream(graph, asset_fqn, depth=3)
            for dn in downstream_nodes:
                downstream_context["downstream_sqls"][dn.fqn] = dn.sql

        print(f"DEBUG run_investigation: Step 4 - Building AI context")
        asset_fqn_map_for_prompt = {
            failure_message.split(":")[0].strip(): (asset_fqn, False, "")
        }
        ai_context = build_pr_ai_context(
            asset_fqn_map=asset_fqn_map_for_prompt,
            merged_subgraph=merged_subgraph,
            pr_number=0,
            downstream_context=downstream_context,
            violations=violations,
        )

        print(f"DEBUG run_investigation: Step 5 - Calling AI")
        update_investigation_status(investigation_id, InvestigationStatus.AI_ANALYSIS)

        pr_root_cause = call_pr_ai_layer(ai_context)
        if not pr_root_cause:
            print(f"ERROR run_investigation: AI layer failed")
            update_investigation_status(investigation_id, InvestigationStatus.FAILED)
            return False

        if violations:
            pr_root_cause = _apply_violation_override(pr_root_cause, violations)

        processing_time_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

        investigations_collection.update_one(
            {"_id": ObjectId(investigation_id)},
            {
                "$set": {
                    "status":             InvestigationStatus.COMPLETED,
                    "pr_root_cause":      pr_root_cause.model_dump(),
                    "completed_at":       datetime.now(timezone.utc).isoformat(),
                    "processing_time_ms": processing_time_ms,
                    "updated_at":         datetime.now(timezone.utc).isoformat(),
                }
            }
        )

        print(f"DEBUG run_investigation: Completed in {processing_time_ms}ms")
        return True

    except Exception as e:
        print(f"ERROR run_investigation: {e}")
        update_investigation_status(investigation_id, InvestigationStatus.FAILED)
        return False