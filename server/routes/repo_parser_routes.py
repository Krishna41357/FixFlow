"""
repo_parser_routes.py — Repo lineage graph management routes for Pipeline Autopsy.

Route organisation:
  POST /repo-parser/scan          — trigger full repo scan, build and store graph
  GET  /repo-parser/graph         — inspect stored graph summary
  GET  /repo-parser/graph/{fqn}   — inspect a single node in detail
  POST /repo-parser/refresh       — force full rebuild ignoring TTL
  GET  /repo-parser/health        — check if graph exists and how fresh it is

All routes require authentication.
All routes look up the GitHub token and repo details from the connection document.
"""

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from controllers import connection_controller
from controllers.repo_parser_controller import (
    scan_repo,
    get_repo_graph,
    update_graph_nodes,
    _load_graph_from_mongo,
    GRAPH_CACHE_TTL_HOURS,
)
from controllers import github_controller
from routes.auth import get_current_user
from models.users import TokenData

router = APIRouter(prefix="/repo-parser", tags=["repo-parser"])


# ── Request / Response schemas ────────────────────────────────────────────────

class ScanRequest(BaseModel):
    connection_id: str


class RefreshRequest(BaseModel):
    connection_id: str


class NodeDetailResponse(BaseModel):
    fqn:           str
    file_path:     str
    columns:       list
    depends_on:    list
    referenced_by: list
    column_usage:  dict


class GraphSummaryResponse(BaseModel):
    repo_full_name:      str
    built_at:            str
    age_hours:           float
    total_nodes:         int
    total_files_scanned: int
    fqns:                list


class HealthResponse(BaseModel):
    graph_exists:  bool
    built_at:      Optional[str]  = None
    age_hours:     Optional[float] = None
    is_stale:      bool            = True
    total_nodes:   int             = 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_connection_and_token(connection_id: str, user_id: str):
    """
    Looks up the connection document and derives the GitHub token.
    Raises HTTPException if connection not found or token unavailable.
    Returns (connection, gh_token, repo_owner, repo_name).
    """
    connection = connection_controller.get_connection_by_id(
        connection_id=connection_id,
        user_id=user_id
    )
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    installation_id = str(connection.github_installation_id or "demo")
    gh_token = github_controller.get_installation_token(installation_id)
    if not gh_token:
        raise HTTPException(status_code=401, detail="Failed to get GitHub App token")

    github_repo = getattr(connection, "github_repo", None)
    if not github_repo or "/" not in github_repo:
        raise HTTPException(status_code=400, detail="Connection has no valid github_repo (expected 'owner/repo')")

    repo_owner, repo_name = github_repo.split("/", 1)
    return connection, gh_token, repo_owner, repo_name


def _age_hours(built_at_str: str) -> float:
    """Returns how many hours ago built_at was."""
    try:
        built_at = datetime.fromisoformat(built_at_str.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - built_at).total_seconds() / 3600
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Routes
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/scan", response_model=dict, status_code=status.HTTP_200_OK)
async def trigger_scan(
    body: ScanRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """
    Triggers a full repo scan for the given connection.

    Fetches all SQL and dbt YML files from the repo, builds the complete
    lineage graph, and stores it in MongoDB + Redis.

    This should be called once when a user connects their repo.
    Subsequent PRs will use the cached graph automatically.

    Returns a summary of what was found.
    """
    start = datetime.now(timezone.utc)

    connection, gh_token, repo_owner, repo_name = _get_connection_and_token(
        body.connection_id, current_user.user_id
    )

    try:
        graph = scan_repo(
            github_token=gh_token,
            repo_owner=repo_owner,
            repo_name=repo_name,
            connection_id=body.connection_id,
            user_id=current_user.user_id,
        )

        elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

        return {
            "status":            "success",
            "repo_full_name":    graph.repo_full_name,
            "total_nodes":       graph.total_nodes,
            "total_files_scanned": graph.total_files_scanned,
            "built_at":          graph.built_at,
            "time_taken_ms":     elapsed_ms,
            "fqns":              list(graph.nodes.keys()),
            "message": (
                f"Graph built successfully — {graph.total_nodes} nodes "
                f"from {graph.total_files_scanned} files in {elapsed_ms}ms"
            ),
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Scan failed: {str(e)}"
        )


@router.get("/graph", response_model=GraphSummaryResponse)
async def get_graph_summary(
    connection_id: str = Query(...),
    current_user: TokenData = Depends(get_current_user),
):
    """
    Returns a summary of the stored lineage graph for a connection.

    Shows: repo name, when it was built, how old it is, total nodes,
    and the list of all FQNs in the graph.

    Use this to verify the graph is populated before expecting PR analysis
    to work correctly.
    """
    connection, _, repo_owner, repo_name = _get_connection_and_token(
        connection_id, current_user.user_id
    )

    repo_full_name = f"{repo_owner}/{repo_name}"
    graph = get_repo_graph(connection_id=connection_id, repo_full_name=repo_full_name)

    if not graph:
        raise HTTPException(
            status_code=404,
            detail=f"No graph found for {repo_full_name}. Run POST /repo-parser/scan first."
        )

    age = _age_hours(graph.built_at)

    return GraphSummaryResponse(
        repo_full_name=graph.repo_full_name,
        built_at=graph.built_at,
        age_hours=round(age, 2),
        total_nodes=graph.total_nodes,
        total_files_scanned=graph.total_files_scanned,
        fqns=list(graph.nodes.keys()),
    )


@router.get("/graph/{fqn:path}", response_model=NodeDetailResponse)
async def get_node_detail(
    fqn: str,
    connection_id: str = Query(...),
    current_user: TokenData = Depends(get_current_user),
):
    """
    Returns full details for a single node in the lineage graph.

    Shows: file path, column definitions, upstream dependencies,
    downstream consumers, and column-level usage tracking.

    Useful for debugging lineage — verify a specific asset is correctly
    wired before opening a PR.

    FQN can be the full FQN (finance.revenue) or just the model name (revenue).
    """
    connection, _, repo_owner, repo_name = _get_connection_and_token(
        connection_id, current_user.user_id
    )

    repo_full_name = f"{repo_owner}/{repo_name}"
    graph = get_repo_graph(connection_id=connection_id, repo_full_name=repo_full_name)

    if not graph:
        raise HTTPException(
            status_code=404,
            detail=f"No graph found for {repo_full_name}. Run POST /repo-parser/scan first."
        )

    # Try exact match first, then suffix match
    node = graph.nodes.get(fqn)
    if not node:
        for candidate_fqn, candidate_node in graph.nodes.items():
            if candidate_fqn.endswith(f".{fqn}") or candidate_fqn == fqn:
                node = candidate_node
                break

    if not node:
        raise HTTPException(
            status_code=404,
            detail=f"Node '{fqn}' not found in graph. Available FQNs: {list(graph.nodes.keys())[:10]}"
        )

    # Serialize column_usage for response
    serialized_usage = {
        upstream_fqn: [
            {
                "column":          cu.column,
                "used_in_select":  cu.used_in_select,
                "used_in_where":   cu.used_in_where,
                "used_in_join":    cu.used_in_join,
            }
            for cu in usages
        ]
        for upstream_fqn, usages in node.column_usage.items()
    }

    return NodeDetailResponse(
        fqn=node.fqn,
        file_path=node.file_path,
        columns=node.columns,
        depends_on=node.depends_on,
        referenced_by=node.referenced_by,
        column_usage=serialized_usage,
    )


@router.post("/refresh", response_model=dict, status_code=status.HTTP_200_OK)
async def force_refresh(
    body: RefreshRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """
    Forces a full graph rebuild regardless of cache TTL.

    Use this when:
      - You've made significant structural changes to the repo
      - The graph seems stale or incorrect
      - You want to verify the latest state is reflected

    This is identical to /scan but always rebuilds even if the cached
    graph is still within its TTL window.
    """
    start = datetime.now(timezone.utc)

    connection, gh_token, repo_owner, repo_name = _get_connection_and_token(
        body.connection_id, current_user.user_id
    )

    try:
        graph = scan_repo(
            github_token=gh_token,
            repo_owner=repo_owner,
            repo_name=repo_name,
            connection_id=body.connection_id,
            user_id=current_user.user_id,
        )

        elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

        return {
            "status":              "refreshed",
            "repo_full_name":      graph.repo_full_name,
            "total_nodes":         graph.total_nodes,
            "total_files_scanned": graph.total_files_scanned,
            "built_at":            graph.built_at,
            "time_taken_ms":       elapsed_ms,
            "message": (
                f"Graph rebuilt — {graph.total_nodes} nodes "
                f"from {graph.total_files_scanned} files in {elapsed_ms}ms"
            ),
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Refresh failed: {str(e)}"
        )


@router.get("/health", response_model=HealthResponse)
async def graph_health(
    connection_id: str = Query(...),
    current_user: TokenData = Depends(get_current_user),
):
    """
    Returns the health status of the graph for a connection.

    Checks:
      - Does a graph exist?
      - How old is it?
      - Is it within the TTL window (not stale)?
      - How many nodes does it have?

    Use this from the frontend to show "graph ready" / "graph stale" /
    "scan required" status to the user.

    Does NOT load the full graph — reads only the metadata fields
    from MongoDB for efficiency.
    """
    connection, _, repo_owner, repo_name = _get_connection_and_token(
        connection_id, current_user.user_id
    )

    repo_full_name = f"{repo_owner}/{repo_name}"

    try:
        # Read only metadata — don't deserialize the full nodes dict
        doc = _load_graph_from_mongo.__wrapped__(repo_full_name) if hasattr(
            _load_graph_from_mongo, "__wrapped__"
        ) else None

        # Fall back to direct MongoDB query for metadata only
        from controllers.repo_parser_controller import _graphs_col
        meta = _graphs_col.find_one(
            {"repo_full_name": repo_full_name},
            {"built_at": 1, "total_nodes": 1, "_id": 0}
        )

        if not meta:
            return HealthResponse(graph_exists=False)

        built_at_str = meta.get("built_at", "")
        age          = _age_hours(built_at_str) if built_at_str else 0.0
        is_stale     = age > GRAPH_CACHE_TTL_HOURS

        return HealthResponse(
            graph_exists=True,
            built_at=built_at_str,
            age_hours=round(age, 2),
            is_stale=is_stale,
            total_nodes=meta.get("total_nodes", 0),
        )

    except Exception as e:
        print(f"ERROR graph_health: {e}")
        return HealthResponse(graph_exists=False)