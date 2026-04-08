"""
lineage.py — Lineage graph schemas for Pipeline Autopsy.

These represent what the lineage engine fetches and traverses
from the OpenMetadata API. We store a snapshot of the relevant
subgraph with each Investigation so the diagnosis is reproducible.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, computed_field

from .base import AssetType, SeverityLevel


# ── Schema change representation ──────────────────────────────────────────────

class ColumnDiff(BaseModel):
    """
    A single column that changed between schema versions.
    This is the core signal the lineage engine uses to detect breaks.
    """
    column_name: str
    change_type: str = Field(
        ...,
        description="renamed | dropped | type_changed | added"
    )
    old_value: Optional[str] = Field(
        None, description="Previous name or type"
    )
    new_value: Optional[str] = Field(
        None, description="New name or type (None if dropped)"
    )


class SchemaDiff(BaseModel):
    """
    Schema change between two versions of an asset.
    Fetched from OpenMetadata's schema version history endpoint.
    """
    asset_fqn: str
    previous_version: Optional[str] = None
    current_version: Optional[str] = None
    changed_at: Optional[str] = None
    changed_by: Optional[str] = None
    column_diffs: List[ColumnDiff] = Field(default_factory=list)

    @computed_field
    @property
    def has_breaking_changes(self) -> bool:
        """True if any column was renamed, dropped, or type-changed."""
        breaking = {"renamed", "dropped", "type_changed"}
        return any(d.change_type in breaking for d in self.column_diffs)


# ── Lineage node and edge ─────────────────────────────────────────────────────

class LineageNode(BaseModel):
    """
    A single asset node in the lineage graph.
    Represents one table, view, dashboard, or pipeline.
    """
    fqn: str                            # OpenMetadata fully qualified name
    display_name: str
    asset_type: AssetType
    service_name: str                   # which connector (Snowflake, BigQuery, etc.)
    owner_email: Optional[str] = None
    owner_team: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

    # Set by the traversal engine
    depth_from_failure: int = Field(
        0,
        description="0 = the failing asset, 1 = direct upstream, etc."
    )
    is_break_point: bool = Field(
        False,
        description="True if this is the node where the schema break originated"
    )
    schema_diff: Optional[SchemaDiff] = None
    severity: Optional[SeverityLevel] = None

    # Raw OpenMetadata metadata, kept for debugging
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)


class LineageEdge(BaseModel):
    """
    A directed edge between two lineage nodes.
    Direction: from_fqn is upstream of to_fqn.
    """
    from_fqn: str       # upstream asset
    to_fqn: str         # downstream asset (closer to the failure)
    edge_type: str = Field(
        "QueryLineage",
        description="Source type from OpenMetadata: QueryLineage | PipelineLineage | DbtLineage …"
    )
    # Column-level detail when available
    column_mappings: List[Dict[str, str]] = Field(
        default_factory=list,
        description="List of {from_column, to_column} dicts"
    )
    transformation_sql: Optional[str] = None


class LineageSubgraph(BaseModel):
    """
    The portion of the lineage graph relevant to one investigation.
    Stored as a snapshot alongside each Investigation document.
    """
    failing_asset_fqn: str
    nodes: List[LineageNode] = Field(default_factory=list)
    edges: List[LineageEdge] = Field(default_factory=list)
    traversal_depth: int = Field(
        0, description="How many hops upstream we walked"
    )

    @computed_field
    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @computed_field
    @property
    def break_point_node(self) -> Optional[LineageNode]:
        """The node the engine identified as the root of the break."""
        for node in self.nodes:
            if node.is_break_point:
                return node
        return None

    @computed_field
    @property
    def affected_asset_fqns(self) -> List[str]:
        """All downstream assets that will be broken by the root cause."""
        return [n.fqn for n in self.nodes if not n.is_break_point]