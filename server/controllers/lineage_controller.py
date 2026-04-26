import os
import requests
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from dotenv import load_dotenv

from models.lineage import (
    LineageNode, LineageEdge, LineageSubgraph,
    SchemaDiff, ColumnDiff
)

load_dotenv()

OPENMETADATA_API_TIMEOUT = 30


def fetch_lineage_subgraph(
    openmetadata_url: str,
    openmetadata_token: str,
    asset_id: str,
    upstream_depth: int = 3
) -> Optional[Dict[str, Any]]:
    try:
        # Use FQN-based endpoint if asset_id looks like a FQN (contains dots)
        if '.' in asset_id:
            endpoint = f"{openmetadata_url.rstrip('/')}/api/v1/lineage/getLineage?fqn={asset_id}&type=table&upstreamDepth={upstream_depth}&downstreamDepth=1"
        else:
            endpoint = f"{openmetadata_url.rstrip('/')}/api/v1/lineage/table/{asset_id}?upstreamDepth={upstream_depth}"

        headers = {"Authorization": f"Bearer {openmetadata_token}"}
        print(f"DEBUG fetch_lineage_subgraph: Calling {endpoint}")
        response = requests.get(endpoint, headers=headers, timeout=OPENMETADATA_API_TIMEOUT)

        if response.status_code == 200:
            print(f"DEBUG fetch_lineage_subgraph: Successfully fetched lineage for {asset_id}")
            return response.json()
        else:
            print(f"ERROR fetch_lineage_subgraph: Status {response.status_code} - {response.text[:200]}")
            return None
    except Exception as e:
        print(f"ERROR fetch_lineage_subgraph: {e}")
        return None


def _parse_entity_ref(ref: dict) -> Optional[str]:
    """
    Extract FQN from an entity reference object.
    OpenMetadata uses different shapes in different response versions:
      { "fqn": "..." }
      { "fullyQualifiedName": "..." }
      { "id": "...", "name": "..." }   ← id-only, no FQN available
    """
    if not ref:
        return None
    return (
        ref.get("fqn")
        or ref.get("fullyQualifiedName")
        or ref.get("name")          # last resort — may not be fully qualified
    )


def traverse_upstream(
    openmetadata_url: str,
    openmetadata_token: str,
    start_asset_id: str,
    max_depth: int = 3
) -> List[LineageNode]:
    """
    Fetches the full lineage graph in one call (upstreamDepth=max_depth),
    then flattens all nodes from the response into LineageNode objects.

    The /lineage/getLineage response shape is:
    {
        "entity": { "fullyQualifiedName": "...", "name": "..." },
        "nodes": [
            { "fullyQualifiedName": "...", "name": "...", "type": "table" },
            ...
        ],
        "upstreamEdges": [
            { "fromEntity": "uuid", "toEntity": "uuid" },
            ...
        ],
        "downstreamEdges": [ ... ]
    }

    NOTE: the edges use UUIDs, not FQNs, so we index nodes by id to resolve them.
    The nodes array contains ALL reachable upstream/downstream nodes.
    """
    from models.base import AssetType

    # Single call with full depth — no recursion needed
    lineage_data = fetch_lineage_subgraph(
        openmetadata_url, openmetadata_token, start_asset_id,
        upstream_depth=max_depth
    )

    if not lineage_data:
        print(f"ERROR traverse_upstream: No lineage data returned for {start_asset_id}")
        return []

    nodes: List[LineageNode] = []
    seen_fqns: set = set()

    # ── 1. Add the primary (failing) entity itself ────────────────────────────
    entity = lineage_data.get("entity", {})
    primary_fqn = (
        entity.get("fullyQualifiedName")
        or entity.get("fqn")
        or start_asset_id
    )
    primary_name = entity.get("name", primary_fqn.split(".")[-1])
    primary_service = primary_fqn.split(".")[0] if "." in primary_fqn else "unknown"

    primary_node = LineageNode(
        fqn=primary_fqn,
        display_name=primary_name,
        asset_type=AssetType.TABLE,
        service_name=primary_service,
        is_break_point=False,
        depth_from_failure=0,
        raw_metadata=entity
    )
    nodes.append(primary_node)
    seen_fqns.add(primary_fqn)

    print(f"DEBUG traverse_upstream: primary node = {primary_fqn}")

    # ── 2. Parse all upstream/downstream nodes from the nodes array ───────────
    # OpenMetadata returns a flat list of all related nodes
    related_nodes = lineage_data.get("nodes", [])
    print(f"DEBUG traverse_upstream: found {len(related_nodes)} related nodes in response")

    for n in related_nodes:
        fqn = (
            n.get("fullyQualifiedName")
            or n.get("fqn")
            or n.get("name")
        )
        if not fqn or fqn in seen_fqns:
            continue

        name = n.get("name", fqn.split(".")[-1])
        service = fqn.split(".")[0] if "." in fqn else "unknown"
        entity_type = n.get("type", "table").lower()

        # Map OpenMetadata type strings to AssetType
        asset_type_map = {
            "table": AssetType.TABLE,
            "pipeline": AssetType.PIPELINE,
            "dashboard": AssetType.DASHBOARD,
            "topic": AssetType.TOPIC,
        }
        asset_type = asset_type_map.get(entity_type, AssetType.TABLE)

        node = LineageNode(
            fqn=fqn,
            display_name=name,
            asset_type=asset_type,
            service_name=service,
            is_break_point=False,
            depth_from_failure=1,   # upstream nodes are all depth 1+ from failure
            raw_metadata=n
        )
        nodes.append(node)
        seen_fqns.add(fqn)

    print(f"DEBUG traverse_upstream: total nodes after parsing = {len(nodes)}")
    return nodes


def fetch_schema_diff(
    openmetadata_url: str,
    openmetadata_token: str,
    table_id: str
) -> Optional[SchemaDiff]:
    """
    Calls OpenMetadata GET /tables/{id}/versions to get schema history.
    Builds SchemaDiff.
    """
    try:
        endpoint = f"{openmetadata_url.rstrip('/')}/api/v1/tables/{table_id}/versions"
        headers = {"Authorization": f"Bearer {openmetadata_token}"}

        response = requests.get(endpoint, headers=headers, timeout=OPENMETADATA_API_TIMEOUT)

        if response.status_code != 200:
            print(f"ERROR fetch_schema_diff: Status {response.status_code}")
            return None

        versions_data = response.json()
        versions = versions_data.get("data", [])

        if len(versions) < 2:
            print(f"DEBUG fetch_schema_diff: Less than 2 versions for {table_id}")
            return None

        current_version = versions[0]
        previous_version = versions[1]

        current_columns = {col["name"]: col for col in current_version.get("columns", [])}
        previous_columns = {col["name"]: col for col in previous_version.get("columns", [])}

        added_columns = []
        removed_columns = []
        modified_columns = []

        for col_name in current_columns:
            if col_name not in previous_columns:
                added_columns.append(ColumnDiff(
                    name=col_name,
                    old_type=None,
                    new_type=current_columns[col_name].get("dataType")
                ))

        for col_name in previous_columns:
            if col_name not in current_columns:
                removed_columns.append(ColumnDiff(
                    name=col_name,
                    old_type=previous_columns[col_name].get("dataType"),
                    new_type=None
                ))

        for col_name in current_columns:
            if col_name in previous_columns:
                old_type = previous_columns[col_name].get("dataType")
                new_type = current_columns[col_name].get("dataType")
                if old_type != new_type:
                    modified_columns.append(ColumnDiff(
                        name=col_name,
                        old_type=old_type,
                        new_type=new_type
                    ))

        return SchemaDiff(
            table_id=table_id,
            added_columns=added_columns,
            removed_columns=removed_columns,
            modified_columns=modified_columns,
            timestamp=datetime.now(timezone.utc)
        )
    except Exception as e:
        print(f"ERROR fetch_schema_diff: {e}")
        return None


def detect_break_point(nodes: List[LineageNode]) -> List[LineageNode]:
    """
    Marks the upstream-most node with schema changes as the break point.
    Currently marks the deepest upstream node as the candidate break point
    when there are multiple nodes (simple heuristic until schema diff is wired).
    """
    if len(nodes) <= 1:
        return nodes

    # Mark the deepest upstream node as the break point (highest depth_from_failure)
    deepest = max(nodes, key=lambda n: n.depth_from_failure)
    deepest.is_break_point = True
    return nodes


def build_subgraph(
    nodes: List[LineageNode],
    edges: List[LineageEdge]
) -> LineageSubgraph:
    """Assembles final LineageSubgraph from nodes + edges."""
    return LineageSubgraph(
        nodes=nodes,
        edges=edges,
        total_nodes=len(nodes),
        break_point_node=next((n.fqn for n in nodes if n.is_break_point), None)
    )


def resolve_asset_fqn(
    openmetadata_url: str,
    openmetadata_token: str,
    dbt_node_id: str
) -> Optional[str]:
    """Converts a dbt node_id (model.proj.orders) → OpenMetadata FQN."""
    try:
        endpoint = f"{openmetadata_url.rstrip('/')}/api/v1/search/query"
        headers = {"Authorization": f"Bearer {openmetadata_token}"}

        data = {
            "query": dbt_node_id,
            "filters": "entityType:Table"
        }

        response = requests.post(endpoint, json=data, headers=headers, timeout=OPENMETADATA_API_TIMEOUT)

        if response.status_code == 200:
            results = response.json().get("hits", {}).get("hits", [])
            if results:
                fqn = results[0].get("_source", {}).get("fullyQualifiedName")
                print(f"DEBUG resolve_asset_fqn: Resolved {dbt_node_id} to FQN {fqn}")
                return fqn

        print(f"ERROR resolve_asset_fqn: Could not resolve {dbt_node_id}")
        return None
    except Exception as e:
        print(f"ERROR resolve_asset_fqn: {e}")
        return None


def fetch_table_details(
    openmetadata_url: str,
    openmetadata_token: str,
    table_id: str
) -> Optional[Dict[str, Any]]:
    """Fetch detailed information about a table from OpenMetadata."""
    try:
        endpoint = f"{openmetadata_url.rstrip('/')}/api/v1/tables/{table_id}"
        headers = {"Authorization": f"Bearer {openmetadata_token}"}

        response = requests.get(endpoint, headers=headers, timeout=OPENMETADATA_API_TIMEOUT)

        if response.status_code == 200:
            return response.json()
        else:
            print(f"ERROR fetch_table_details: Status {response.status_code}")
            return None
    except Exception as e:
        print(f"ERROR fetch_table_details: {e}")
        return None