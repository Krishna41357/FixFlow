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
        # downstreamDepth matches upstreamDepth so we get full consumer graph
        if '.' in asset_id:
            endpoint = (
                f"{openmetadata_url.rstrip('/')}/api/v1/lineage/getLineage"
                f"?fqn={asset_id}&type=table"
                f"&upstreamDepth={upstream_depth}"
                f"&downstreamDepth={upstream_depth}"   # FIXED: was hardcoded to 1
            )
        else:
            endpoint = (
                f"{openmetadata_url.rstrip('/')}/api/v1/lineage/table/{asset_id}"
                f"?upstreamDepth={upstream_depth}"
            )

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
    Fetches the full lineage graph in one call (upstreamDepth=max_depth,
    downstreamDepth=max_depth), then flattens all nodes from the response
    into LineageNode objects.

    The /lineage/getLineage response shape is:
    {
        "entity": { "fullyQualifiedName": "...", "name": "..." },
        "nodes": [
            { "id": "uuid", "fullyQualifiedName": "...", "name": "...", "type": "table" },
            ...
        ],
        "upstreamEdges": [
            { "fromEntity": "uuid", "toEntity": "uuid" },
            ...
        ],
        "downstreamEdges": [
            { "fromEntity": "uuid", "toEntity": "uuid" },
            ...
        ]
    }

    Node classification:
      - depth_from_failure=0,  is_downstream=False  → the changed asset itself (primary)
      - depth_from_failure=1+, is_downstream=False  → upstream ancestors (raw sources, staging)
      - depth_from_failure=-1, is_downstream=True   → downstream consumers (dashboards, reports)

    The is_downstream flag is determined by cross-referencing downstreamEdges:
    any node whose ID appears as a toEntity in downstreamEdges is a consumer.
    """
    from models.base import AssetType

    # Single call — no recursion needed
    lineage_data = fetch_lineage_subgraph(
        openmetadata_url, openmetadata_token, start_asset_id,
        upstream_depth=max_depth
    )

    if not lineage_data:
        print(f"ERROR traverse_upstream: No lineage data returned for {start_asset_id}")
        return []

    nodes: List[LineageNode] = []
    seen_fqns: set = set()

    # ── 1. Add the primary (changed/failing) entity itself ────────────────────
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
        is_downstream=False,
        depth_from_failure=0,
        raw_metadata=entity
    )
    nodes.append(primary_node)
    seen_fqns.add(primary_fqn)

    print(f"DEBUG traverse_upstream: primary node = {primary_fqn}")

    # ── 2. Build downstream consumer ID set from downstreamEdges ─────────────
    # downstreamEdges: fromEntity is the primary asset, toEntity is the consumer.
    # We collect all toEntity IDs — any node whose ID is in this set is a consumer
    # of the changed asset and needs its SQL fetched for impact analysis.
    downstream_entity_ids: set = set()
    for edge in lineage_data.get("downstreamEdges", []):
        to_id = str(edge.get("toEntity") or edge.get("toId", ""))
        if to_id:
            downstream_entity_ids.add(to_id)

    print(
        f"DEBUG traverse_upstream: "
        f"{len(downstream_entity_ids)} downstream consumer IDs from downstreamEdges"
    )

    # ── 3. Build id → fqn map from the nodes array ───────────────────────────
    # Edges use UUIDs, not FQNs, so we need this map to resolve consumer IDs
    # to FQNs for the is_downstream flag.
    id_to_fqn: Dict[str, str] = {}
    for n in lineage_data.get("nodes", []):
        node_id = str(n.get("id", ""))
        node_fqn = (
            n.get("fullyQualifiedName")
            or n.get("fqn")
            or n.get("name")
        )
        if node_id and node_fqn:
            id_to_fqn[node_id] = node_fqn

    # Resolve downstream IDs to FQNs for O(1) lookup during node parsing
    downstream_fqns_set: set = {
        id_to_fqn[eid]
        for eid in downstream_entity_ids
        if eid in id_to_fqn
    }

    print(
        f"DEBUG traverse_upstream: "
        f"{len(downstream_fqns_set)} downstream consumer FQNs resolved"
    )

    # ── 4. Parse all related nodes (upstream ancestors + downstream consumers) ─
    related_nodes = lineage_data.get("nodes", [])
    print(f"DEBUG traverse_upstream: found {len(related_nodes)} related nodes in response")

    asset_type_map = {
        "table":     AssetType.TABLE,
        "pipeline":  AssetType.PIPELINE,
        "dashboard": AssetType.DASHBOARD,
        "topic":     AssetType.TOPIC,
    }

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
        asset_type = asset_type_map.get(entity_type, AssetType.TABLE)

        # Tag as downstream consumer if its FQN appears in the resolved set
        is_downstream = fqn in downstream_fqns_set

        node = LineageNode(
            fqn=fqn,
            display_name=name,
            asset_type=asset_type,
            service_name=service,
            is_break_point=False,
            is_downstream=is_downstream,
            # Negative depth = downstream consumer; positive = upstream ancestor
            depth_from_failure=(-1 if is_downstream else 1),
            raw_metadata=n
        )
        nodes.append(node)
        seen_fqns.add(fqn)

    upstream_count   = sum(1 for n in nodes if n.depth_from_failure > 0)
    downstream_count = sum(1 for n in nodes if n.is_downstream)
    print(
        f"DEBUG traverse_upstream: total={len(nodes)} "
        f"(primary=1, upstream={upstream_count}, downstream={downstream_count})"
    )
    return nodes


# ── Layer 2: fetch current schema of a changed asset ─────────────────────────

def fetch_asset_schema(
    openmetadata_url: str,
    openmetadata_token: str,
    fqn: str
) -> List[dict]:
    """
    Fetches the current column list for an asset from OpenMetadata.

    Calls GET /api/v1/tables/name/{fqn}?fields=columns

    Returns a list of column dicts: [{"name": "...", "dataType": "..."}, ...]
    Returns [] if the asset is not found or the call fails — callers must
    handle an empty list gracefully (it means schema is unknown, not that
    the asset has no columns).

    This is Layer 2 of the downstream context block: it gives the AI the
    current contract that downstream consumers depend on, so it can reason
    about what the PR diff is breaking.
    """
    try:
        from urllib.parse import quote
        encoded_fqn = quote(fqn, safe="")
        endpoint = (
            f"{openmetadata_url.rstrip('/')}/api/v1/tables/name/"
            f"{encoded_fqn}?fields=columns"
        )
        headers = {"Authorization": f"Bearer {openmetadata_token}"}

        print(f"DEBUG fetch_asset_schema: Calling {endpoint}")
        response = requests.get(endpoint, headers=headers, timeout=OPENMETADATA_API_TIMEOUT)

        if response.status_code == 200:
            data = response.json()
            columns = data.get("columns", [])
            result = [
                {
                    "name":     col.get("name", ""),
                    "dataType": col.get("dataType", col.get("dataTypeDisplay", "UNKNOWN"))
                }
                for col in columns
                if col.get("name")
            ]
            print(f"DEBUG fetch_asset_schema: Found {len(result)} columns for {fqn}")
            return result

        elif response.status_code == 404:
            print(f"DEBUG fetch_asset_schema: Asset {fqn} not found in OpenMetadata (404)")
            return []
        else:
            print(
                f"ERROR fetch_asset_schema: Status {response.status_code} for {fqn} "
                f"— {response.text[:200]}"
            )
            return []

    except Exception as e:
        print(f"ERROR fetch_asset_schema: {e}")
        return []


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

        current_version  = versions[0]
        previous_version = versions[1]

        current_columns  = {col["name"]: col for col in current_version.get("columns", [])}
        previous_columns = {col["name"]: col for col in previous_version.get("columns", [])}

        added_columns    = []
        removed_columns  = []
        modified_columns = []

        for col_name in current_columns:
            if col_name not in previous_columns:
                added_columns.append(ColumnDiff(
                    column_name=col_name,
                    change_type="added",
                    old_value=None,
                    new_value=current_columns[col_name].get("dataType")
                ))

        for col_name in previous_columns:
            if col_name not in current_columns:
                removed_columns.append(ColumnDiff(
                    column_name=col_name,
                    change_type="dropped",
                    old_value=previous_columns[col_name].get("dataType"),
                    new_value=None
                ))

        for col_name in current_columns:
            if col_name in previous_columns:
                old_type = previous_columns[col_name].get("dataType")
                new_type = current_columns[col_name].get("dataType")
                if old_type != new_type:
                    modified_columns.append(ColumnDiff(
                        column_name=col_name,
                        change_type="type_changed",
                        old_value=old_type,
                        new_value=new_type
                    ))

        return SchemaDiff(
            asset_fqn=table_id,
            column_diffs=added_columns + removed_columns + modified_columns
        )
    except Exception as e:
        print(f"ERROR fetch_schema_diff: {e}")
        return None


def detect_break_point(nodes: List[LineageNode]) -> List[LineageNode]:
    """
    Marks the upstream-most node with schema changes as the break point.
    Currently marks the deepest upstream node as the candidate break point
    when there are multiple nodes (simple heuristic until schema diff is wired).

    Only considers upstream nodes (depth_from_failure > 0) — downstream
    consumers are never the source of a break.
    """
    if len(nodes) <= 1:
        return nodes

    # Only upstream ancestors are candidates for break point
    upstream_nodes = [n for n in nodes if n.depth_from_failure > 0]
    if not upstream_nodes:
        return nodes

    deepest = max(upstream_nodes, key=lambda n: n.depth_from_failure)
    deepest.is_break_point = True
    return nodes


def build_subgraph(
    nodes: List[LineageNode],
    edges: List[LineageEdge]
) -> LineageSubgraph:
    """Assembles final LineageSubgraph from nodes + edges."""
    failing_fqn = next((n.fqn for n in nodes if n.depth_from_failure == 0), "")
    return LineageSubgraph(
        failing_asset_fqn=failing_fqn,
        nodes=nodes,
        edges=edges,
        traversal_depth=max((n.depth_from_failure for n in nodes if n.depth_from_failure > 0), default=0)
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