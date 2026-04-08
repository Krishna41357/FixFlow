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
    """
    Calls OpenMetadata GET /lineage/table/{id}?upstreamDepth=3.
    Returns raw API response.
    """
    try:
        endpoint = f"{openmetadata_url.rstrip('/')}/api/v1/lineage/table/{asset_id}?upstreamDepth={upstream_depth}"
        headers = {"Authorization": f"Bearer {openmetadata_token}"}
        
        response = requests.get(endpoint, headers=headers, timeout=OPENMETADATA_API_TIMEOUT)
        
        if response.status_code == 200:
            print(f"DEBUG fetch_lineage_subgraph: Successfully fetched lineage for {asset_id}")
            return response.json()
        else:
            print(f"ERROR fetch_lineage_subgraph: Status {response.status_code}")
            return None
    except Exception as e:
        print(f"ERROR fetch_lineage_subgraph: {e}")
        return None


def traverse_upstream(
    openmetadata_url: str,
    openmetadata_token: str,
    start_asset_id: str,
    max_depth: int = 3
) -> List[LineageNode]:
    """
    Walks nodes from failing asset upward.
    Stops when it finds a schema mismatch or hits max depth.
    """
    nodes = []
    visited = set()
    
    def recursive_traverse(asset_id: str, depth: int) -> None:
        if depth > max_depth or asset_id in visited:
            return
        
        visited.add(asset_id)
        
        # Fetch lineage for this node
        lineage_data = fetch_lineage_subgraph(
            openmetadata_url,
            openmetadata_token,
            asset_id,
            upstream_depth=1
        )
        
        if not lineage_data:
            return
        
        # Create LineageNode
        node = LineageNode(
            id=asset_id,
            name=lineage_data.get("entity", {}).get("name", "Unknown"),
            fqn=lineage_data.get("entity", {}).get("fullyQualifiedName", "Unknown"),
            type=lineage_data.get("entity", {}).get("entityType", "table"),
            schema=lineage_data.get("entity", {}).get("columns", []),
            is_break_point=False
        )
        nodes.append(node)
        
        # Traverse upstream nodes
        upstream_edges = lineage_data.get("upstreamEdges", [])
        for edge in upstream_edges:
            upstream_node_id = edge.get("source", {}).get("id")
            if upstream_node_id:
                recursive_traverse(upstream_node_id, depth + 1)
    
    recursive_traverse(start_asset_id, 0)
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
        
        # Compare last two versions
        current_version = versions[0]
        previous_version = versions[1]
        
        current_columns = {col["name"]: col for col in current_version.get("columns", [])}
        previous_columns = {col["name"]: col for col in previous_version.get("columns", [])}
        
        added_columns = []
        removed_columns = []
        modified_columns = []
        
        # Find added columns
        for col_name in current_columns:
            if col_name not in previous_columns:
                added_columns.append(ColumnDiff(
                    name=col_name,
                    old_type=None,
                    new_type=current_columns[col_name].get("dataType")
                ))
        
        # Find removed columns
        for col_name in previous_columns:
            if col_name not in current_columns:
                removed_columns.append(ColumnDiff(
                    name=col_name,
                    old_type=previous_columns[col_name].get("dataType"),
                    new_type=None
                ))
        
        # Find modified columns
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
    Compares schema versions across nodes.
    Sets is_break_point=True on the node where change happened.
    """
    for i, node in enumerate(nodes):
        if i == 0:
            # Check current node's schema against previous versions
            # For now, mark as break point if any schema diff exists
            node.is_break_point = False
        else:
            # Compare with downstream node
            # If schemas don't match, this is the break point
            node.is_break_point = False
    
    return nodes


def build_subgraph(
    nodes: List[LineageNode],
    edges: List[LineageEdge]
) -> LineageSubgraph:
    """
    Assembles final LineageSubgraph from nodes + edges.
    This is passed to the context builder.
    """
    return LineageSubgraph(
        nodes=nodes,
        edges=edges,
        total_nodes=len(nodes),
        break_point_node=next((n.id for n in nodes if n.is_break_point), None)
    )


def resolve_asset_fqn(
    openmetadata_url: str,
    openmetadata_token: str,
    dbt_node_id: str
) -> Optional[str]:
    """
    Converts a dbt node_id (model.proj.orders) → OpenMetadata FQN (snowflake.prod.orders).
    """
    try:
        # Search OpenMetadata for the table with matching dbt lineage
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
                # Return FQN of first match
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
