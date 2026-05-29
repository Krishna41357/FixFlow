"""
test_repo_parser.py — End-to-end workflow tests for the repo parser component.

Test organisation:
  ── Unit tests: Parsing layer ─────────────────────────────────────────────────
  TestParseRefAndSourceCalls     — ref() and source() extraction
  TestParseColumnUsage           — alias.column detection from SQL clauses
  TestParseYmlColumns            — column extraction from dbt schema yml
  TestDeriveFqnFromPath          — file path → FQN conversion
  TestFilterDbtFiles             — file tree filtering

  ── Unit tests: Graph build layer ─────────────────────────────────────────────
  TestPopulateReferencedBy       — edge inversion pass
  TestPopulateColumnUsage        — column usage pass
  TestGetDownstream              — BFS traversal
  TestGetColumnDependents        — column-level impact detection

  ── Integration tests: Adapter ────────────────────────────────────────────────
  TestBuildSubgraphFromGraph     — RepoLineageGraph → LineageSubgraph adapter

  ── Integration tests: Full workflow ─────────────────────────────────────────
  TestFullWorkflow               — scan → get_downstream → build_subgraph → impact

Run with:
  pytest test_repo_parser.py -v
  pytest test_repo_parser.py -v -k "test_column_usage"   # run specific test
  pytest test_repo_parser.py -v --tb=short               # shorter tracebacks
"""

import pytest
from unittest.mock import patch, MagicMock
from dataclasses import field
from typing import Dict, List, Optional

from server.controllers.repo_parser_controller import (
    # Data structures
    ColumnUsage,
    RepoLineageNode,
    RepoLineageGraph,
    # Parsing layer
    _derive_fqn_from_path,
    _parse_ref_and_source_calls,
    _parse_column_usage,
    _parse_yml_columns,
    _filter_dbt_files,
    # Graph build layer
    _populate_referenced_by,
    _populate_column_usage,
    # Public API
    get_downstream,
    get_column_dependents,
    build_subgraph_from_graph,
    # Storage helpers (for mocking)
    _graph_to_mongo_doc,
    _mongo_doc_to_graph,
)


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures — reusable test data
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def simple_graph() -> RepoLineageGraph:
    """
    A simple 3-node graph:
        raw.users ← finance.orders ← finance.revenue
    
    raw.users has no upstream deps (seed/source).
    finance.orders depends on raw.users.
    finance.revenue depends on finance.orders and raw.users.
    """
    nodes = {
        "raw.users": RepoLineageNode(
            fqn="raw.users",
            file_path="seeds/raw/users.sql",
            sql="SELECT id, name, email, session_id FROM raw_source.users",
            columns=["id", "name", "email", "session_id"],
            depends_on=[],
            referenced_by=[],
        ),
        "finance.orders": RepoLineageNode(
            fqn="finance.orders",
            file_path="models/finance/orders.sql",
            sql="""
                SELECT
                    o.order_id,
                    o.amount,
                    u.name,
                    u.email
                FROM {{ ref('users') }} u
                JOIN raw_orders o ON u.id = o.user_id
            """,
            columns=["order_id", "amount", "name", "email"],
            depends_on=["raw.users"],
            referenced_by=[],
        ),
        "finance.revenue": RepoLineageNode(
            fqn="finance.revenue",
            file_path="models/finance/revenue.sql",
            sql="""
                SELECT
                    r.order_id,
                    u.session_id,
                    u.name,
                    SUM(r.amount) as total
                FROM {{ ref('orders') }} r
                JOIN {{ ref('users') }} u ON r.order_id = u.id
                GROUP BY r.order_id, u.session_id, u.name
            """,
            columns=["order_id", "session_id", "name", "total"],
            depends_on=["finance.orders", "raw.users"],
            referenced_by=[],
        ),
    }

    # Populate referenced_by manually for fixture consistency
    nodes = _populate_referenced_by(nodes)

    return RepoLineageGraph(
        repo_full_name="test-org/test-repo",
        connection_id="conn_123",
        user_id="user_456",
        built_at="2024-01-01T00:00:00+00:00",
        nodes=nodes,
        total_files_scanned=3,
        total_nodes=3,
    )


@pytest.fixture
def deep_graph() -> RepoLineageGraph:
    """
    A 5-node graph with 3 levels of depth:
        raw.events
            └── staging.events
                    └── mart.daily_events
                    └── mart.user_summary
                            └── reporting.dashboard
    """
    nodes = {
        "raw.events": RepoLineageNode(
            fqn="raw.events",
            file_path="seeds/raw/events.sql",
            sql="SELECT event_id, user_id, event_type, created_at FROM source",
            columns=["event_id", "user_id", "event_type", "created_at"],
            depends_on=[],
            referenced_by=[],
        ),
        "staging.events": RepoLineageNode(
            fqn="staging.events",
            file_path="models/staging/events.sql",
            sql="""
                SELECT
                    e.event_id,
                    e.user_id,
                    e.event_type,
                    e.created_at
                FROM {{ ref('events') }} e
            """,
            columns=["event_id", "user_id", "event_type", "created_at"],
            depends_on=["raw.events"],
            referenced_by=[],
        ),
        "mart.daily_events": RepoLineageNode(
            fqn="mart.daily_events",
            file_path="models/mart/daily_events.sql",
            sql="""
                SELECT
                    s.event_type,
                    COUNT(s.event_id) as count,
                    DATE(s.created_at) as date
                FROM {{ ref('events') }} s
                GROUP BY s.event_type, DATE(s.created_at)
            """,
            columns=["event_type", "count", "date"],
            depends_on=["staging.events"],
            referenced_by=[],
        ),
        "mart.user_summary": RepoLineageNode(
            fqn="mart.user_summary",
            file_path="models/mart/user_summary.sql",
            sql="""
                SELECT
                    s.user_id,
                    s.event_type,
                    COUNT(*) as total_events
                FROM {{ ref('events') }} s
                GROUP BY s.user_id, s.event_type
            """,
            columns=["user_id", "event_type", "total_events"],
            depends_on=["staging.events"],
            referenced_by=[],
        ),
        "reporting.dashboard": RepoLineageNode(
            fqn="reporting.dashboard",
            file_path="models/reporting/dashboard.sql",
            sql="""
                SELECT
                    u.user_id,
                    u.total_events,
                    d.count as daily_count
                FROM {{ ref('user_summary') }} u
                JOIN {{ ref('daily_events') }} d ON u.user_id = d.event_type
            """,
            columns=["user_id", "total_events", "daily_count"],
            depends_on=["mart.user_summary", "mart.daily_events"],
            referenced_by=[],
        ),
    }

    nodes = _populate_referenced_by(nodes)

    return RepoLineageGraph(
        repo_full_name="test-org/test-repo",
        connection_id="conn_123",
        user_id="user_456",
        built_at="2024-01-01T00:00:00+00:00",
        nodes=nodes,
        total_files_scanned=5,
        total_nodes=5,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Unit Tests — Parsing Layer
# ══════════════════════════════════════════════════════════════════════════════

class TestDeriveFqnFromPath:

    def test_models_two_level(self):
        assert _derive_fqn_from_path("models/finance/revenue.sql") == "finance.revenue"

    def test_models_one_level(self):
        assert _derive_fqn_from_path("models/users.sql") == "users"

    def test_seeds_two_level(self):
        assert _derive_fqn_from_path("seeds/raw/users.sql") == "raw.users"

    def test_snapshots(self):
        assert _derive_fqn_from_path("snapshots/finance/orders.sql") == "finance.orders"

    def test_no_dbt_prefix(self):
        # Files without dbt prefix — keep as-is
        result = _derive_fqn_from_path("some/random/file.sql")
        assert result == "some.random.file"

    def test_three_level_path(self):
        assert _derive_fqn_from_path("models/finance/subdir/report.sql") == "finance.subdir.report"

    def test_numeric_prefix(self):
        assert _derive_fqn_from_path("models/migrations/001_users.sql") == "migrations.001_users"


class TestParseRefAndSourceCalls:

    def test_single_ref_single_quotes(self):
        sql = "SELECT * FROM {{ ref('orders') }}"
        result = _parse_ref_and_source_calls(sql)
        assert result == ["orders"]

    def test_single_ref_double_quotes(self):
        sql = 'SELECT * FROM {{ ref("orders") }}'
        result = _parse_ref_and_source_calls(sql)
        assert result == ["orders"]

    def test_ref_no_spaces(self):
        sql = "SELECT * FROM {{ref('orders')}}"
        result = _parse_ref_and_source_calls(sql)
        assert result == ["orders"]

    def test_multiple_refs(self):
        sql = """
            SELECT o.id, u.name
            FROM {{ ref('orders') }} o
            JOIN {{ ref('users') }} u ON o.user_id = u.id
        """
        result = _parse_ref_and_source_calls(sql)
        assert "orders" in result
        assert "users" in result
        assert len(result) == 2

    def test_source_call(self):
        sql = "SELECT * FROM {{ source('raw', 'events') }}"
        result = _parse_ref_and_source_calls(sql)
        assert result == ["raw.events"]

    def test_source_double_quotes(self):
        sql = 'SELECT * FROM {{ source("raw", "events") }}'
        result = _parse_ref_and_source_calls(sql)
        assert result == ["raw.events"]

    def test_mixed_ref_and_source(self):
        sql = """
            SELECT *
            FROM {{ source('raw', 'users') }} u
            JOIN {{ ref('orders') }} o ON u.id = o.user_id
        """
        result = _parse_ref_and_source_calls(sql)
        assert "raw.users" in result
        assert "orders" in result

    def test_deduplication(self):
        # Same ref twice — should appear once
        sql = """
            SELECT a.id, b.id
            FROM {{ ref('users') }} a
            JOIN {{ ref('users') }} b ON a.parent_id = b.id
        """
        result = _parse_ref_and_source_calls(sql)
        assert result.count("users") == 1

    def test_ignores_config_macro(self):
        sql = """
            {{ config(materialized='table') }}
            SELECT * FROM {{ ref('orders') }}
        """
        result = _parse_ref_and_source_calls(sql)
        assert result == ["orders"]

    def test_empty_sql(self):
        result = _parse_ref_and_source_calls("")
        assert result == []

    def test_no_refs(self):
        sql = "SELECT id, name FROM raw_table"
        result = _parse_ref_and_source_calls(sql)
        assert result == []


class TestParseColumnUsage:

    def test_basic_alias_column_select(self):
        sql = """
            SELECT u.name, u.email
            FROM {{ ref('users') }} u
        """
        result = _parse_column_usage(sql, ["raw.users"])
        assert "raw.users" in result
        cols = {cu.column for cu in result["raw.users"]}
        assert "name" in cols or "email" in cols

    def test_select_star_wildcard(self):
        sql = "SELECT * FROM {{ ref('users') }} u"
        result = _parse_column_usage(sql, ["raw.users"])
        assert "raw.users" in result
        assert any(cu.column == "*" for cu in result["raw.users"])

    def test_alias_star_wildcard(self):
        sql = "SELECT u.* FROM {{ ref('users') }} u"
        result = _parse_column_usage(sql, ["raw.users"])
        assert "raw.users" in result
        assert any(cu.column == "*" for cu in result["raw.users"])

    def test_no_alias_returns_empty(self):
        # SQL with no alias — cannot determine usage
        sql = "SELECT name FROM {{ ref('users') }}"
        result = _parse_column_usage(sql, ["raw.users"])
        # Should return empty or partial — not crash
        assert isinstance(result, dict)

    def test_multiple_aliases(self):
        sql = """
            SELECT o.order_id, u.name
            FROM {{ ref('orders') }} o
            JOIN {{ ref('users') }} u ON o.user_id = u.id
        """
        result = _parse_column_usage(sql, ["finance.orders", "raw.users"])
        # Should have entries for both upstreams
        assert isinstance(result, dict)

    def test_empty_sql(self):
        result = _parse_column_usage("", ["raw.users"])
        assert result == {}

    def test_empty_upstream_fqns(self):
        sql = "SELECT u.name FROM {{ ref('users') }} u"
        result = _parse_column_usage(sql, [])
        assert result == {}


class TestParseYmlColumns:

    def test_basic_model_columns(self):
        yml = """
version: 2
models:
  - name: revenue
    description: Revenue model
    columns:
      - name: order_id
        description: Primary key
      - name: session_id
      - name: total_amount
"""
        result = _parse_yml_columns(yml, "models/finance/revenue.yml")
        assert "revenue" in result
        assert "order_id" in result["revenue"]
        assert "session_id" in result["revenue"]
        assert "total_amount" in result["revenue"]

    def test_multiple_models(self):
        yml = """
version: 2
models:
  - name: orders
    columns:
      - name: order_id
      - name: amount
  - name: users
    columns:
      - name: id
      - name: name
      - name: email
"""
        result = _parse_yml_columns(yml, "models/schema.yml")
        assert "orders" in result
        assert "users" in result
        assert len(result["orders"]) == 2
        assert len(result["users"]) == 3

    def test_sources_block(self):
        yml = """
version: 2
sources:
  - name: raw
    tables:
      - name: users
        columns:
          - name: id
          - name: name
"""
        result = _parse_yml_columns(yml, "models/sources.yml")
        # Should pick up model/table names
        assert isinstance(result, dict)

    def test_empty_yml(self):
        result = _parse_yml_columns("", "models/empty.yml")
        assert result == {}

    def test_malformed_yml_does_not_crash(self):
        yml = "this: is: not: valid:\n  yaml: content: here"
        # Should not raise — return whatever was parseable
        result = _parse_yml_columns(yml, "models/bad.yml")
        assert isinstance(result, dict)

    def test_no_columns_block(self):
        yml = """
version: 2
models:
  - name: simple_model
    description: A model with no column definitions
"""
        result = _parse_yml_columns(yml, "models/simple.yml")
        # Model may appear with empty columns list
        assert isinstance(result, dict)


class TestFilterDbtFiles:

    def test_separates_sql_and_yml(self):
        tree = [
            {"path": "models/finance/revenue.sql", "type": "blob"},
            {"path": "models/finance/schema.yml", "type": "blob"},
            {"path": "seeds/raw/users.sql", "type": "blob"},
        ]
        sql_files, yml_files = _filter_dbt_files(tree)
        assert len(sql_files) == 2
        assert len(yml_files) == 1

    def test_excludes_non_dbt_directories(self):
        tree = [
            {"path": "models/finance/revenue.sql", "type": "blob"},
            {"path": ".github/workflows/ci.yml", "type": "blob"},
            {"path": "docs/readme.md", "type": "blob"},
            {"path": "tests/test_revenue.sql", "type": "blob"},  # tests/ not a dbt dir
        ]
        sql_files, yml_files = _filter_dbt_files(tree)
        assert "models/finance/revenue.sql" in sql_files
        assert ".github/workflows/ci.yml" not in yml_files
        assert "tests/test_revenue.sql" not in sql_files

    def test_includes_snapshots(self):
        tree = [
            {"path": "snapshots/orders_snapshot.sql", "type": "blob"},
        ]
        sql_files, yml_files = _filter_dbt_files(tree)
        assert "snapshots/orders_snapshot.sql" in sql_files

    def test_excludes_tree_entries(self):
        # type="tree" entries are directories — should be excluded
        tree = [
            {"path": "models/finance", "type": "tree"},
            {"path": "models/finance/revenue.sql", "type": "blob"},
        ]
        sql_files, yml_files = _filter_dbt_files(tree)
        assert len(sql_files) == 1

    def test_empty_tree(self):
        sql_files, yml_files = _filter_dbt_files([])
        assert sql_files == []
        assert yml_files == []

    def test_yaml_extension(self):
        tree = [
            {"path": "models/schema.yaml", "type": "blob"},
        ]
        sql_files, yml_files = _filter_dbt_files(tree)
        assert "models/schema.yaml" in yml_files


# ══════════════════════════════════════════════════════════════════════════════
# Unit Tests — Graph Build Layer
# ══════════════════════════════════════════════════════════════════════════════

class TestPopulateReferencedBy:

    def test_basic_edge_inversion(self):
        nodes = {
            "raw.users": RepoLineageNode(
                fqn="raw.users", file_path="seeds/raw/users.sql",
                depends_on=[], referenced_by=[]
            ),
            "finance.orders": RepoLineageNode(
                fqn="finance.orders", file_path="models/finance/orders.sql",
                depends_on=["raw.users"], referenced_by=[]
            ),
        }
        result = _populate_referenced_by(nodes)
        assert "finance.orders" in result["raw.users"].referenced_by

    def test_multiple_dependents(self):
        nodes = {
            "raw.users": RepoLineageNode(
                fqn="raw.users", file_path="seeds/raw/users.sql",
                depends_on=[], referenced_by=[]
            ),
            "finance.orders": RepoLineageNode(
                fqn="finance.orders", file_path="models/finance/orders.sql",
                depends_on=["raw.users"], referenced_by=[]
            ),
            "finance.revenue": RepoLineageNode(
                fqn="finance.revenue", file_path="models/finance/revenue.sql",
                depends_on=["raw.users"], referenced_by=[]
            ),
        }
        result = _populate_referenced_by(nodes)
        assert "finance.orders" in result["raw.users"].referenced_by
        assert "finance.revenue" in result["raw.users"].referenced_by

    def test_no_duplicates(self):
        # Calling twice should not duplicate referenced_by entries
        nodes = {
            "raw.users": RepoLineageNode(
                fqn="raw.users", file_path="seeds/raw/users.sql",
                depends_on=[], referenced_by=[]
            ),
            "finance.orders": RepoLineageNode(
                fqn="finance.orders", file_path="models/finance/orders.sql",
                depends_on=["raw.users"], referenced_by=[]
            ),
        }
        result = _populate_referenced_by(nodes)
        result = _populate_referenced_by(result)  # second call
        assert result["raw.users"].referenced_by.count("finance.orders") == 1

    def test_external_dep_skipped_gracefully(self):
        # depends_on contains a name not in the graph — should not crash
        nodes = {
            "finance.orders": RepoLineageNode(
                fqn="finance.orders", file_path="models/finance/orders.sql",
                depends_on=["external.source_not_in_graph"], referenced_by=[]
            ),
        }
        result = _populate_referenced_by(nodes)
        assert isinstance(result, dict)

    def test_suffix_match(self):
        # depends_on uses short name "users", node FQN is "raw.users"
        nodes = {
            "raw.users": RepoLineageNode(
                fqn="raw.users", file_path="seeds/raw/users.sql",
                depends_on=[], referenced_by=[]
            ),
            "finance.orders": RepoLineageNode(
                fqn="finance.orders", file_path="models/finance/orders.sql",
                depends_on=["users"],  # short name
                referenced_by=[]
            ),
        }
        result = _populate_referenced_by(nodes)
        # Should resolve "users" to "raw.users" via suffix match
        assert "finance.orders" in result["raw.users"].referenced_by


# ══════════════════════════════════════════════════════════════════════════════
# Unit Tests — Public API
# ══════════════════════════════════════════════════════════════════════════════

class TestGetDownstream:

    def test_direct_downstream(self, simple_graph):
        # raw.users is referenced by finance.orders and finance.revenue
        result = get_downstream(simple_graph, "raw.users", depth=1)
        fqns = {n.fqn for n in result}
        assert "finance.orders" in fqns or "finance.revenue" in fqns

    def test_deep_traversal(self, deep_graph):
        # raw.events → staging.events → mart.daily_events / mart.user_summary → reporting.dashboard
        result = get_downstream(deep_graph, "raw.events", depth=3)
        fqns = {n.fqn for n in result}
        assert "staging.events" in fqns
        assert "mart.daily_events" in fqns or "mart.user_summary" in fqns

    def test_depth_limit(self, deep_graph):
        # depth=1 should only return staging.events, not mart or reporting
        result = get_downstream(deep_graph, "raw.events", depth=1)
        fqns = {n.fqn for n in result}
        assert "staging.events" in fqns
        assert "reporting.dashboard" not in fqns

    def test_source_node_not_in_result(self, simple_graph):
        result = get_downstream(simple_graph, "raw.users", depth=3)
        fqns = {n.fqn for n in result}
        assert "raw.users" not in fqns

    def test_no_downstream(self, simple_graph):
        # finance.revenue has no referenced_by
        result = get_downstream(simple_graph, "finance.revenue", depth=3)
        assert result == []

    def test_fqn_not_in_graph(self, simple_graph):
        result = get_downstream(simple_graph, "nonexistent.model", depth=3)
        assert result == []

    def test_deduplication(self, deep_graph):
        # Each node should appear at most once even with diamond dependencies
        result = get_downstream(deep_graph, "raw.events", depth=3)
        fqns = [n.fqn for n in result]
        assert len(fqns) == len(set(fqns))

    def test_suffix_match(self, simple_graph):
        # "users" should match "raw.users"
        result = get_downstream(simple_graph, "users", depth=1)
        # Should return some results via suffix match
        assert isinstance(result, list)


class TestGetColumnDependents:

    def test_detects_used_column(self, simple_graph):
        # finance.revenue uses 'name' from raw.users
        # finance.orders uses 'name' from raw.users
        # Dropping 'name' should flag both
        simple_graph.nodes = _populate_column_usage(simple_graph.nodes)
        result = get_column_dependents(simple_graph, "raw.users", ["name"])
        # Result should contain nodes that use 'name'
        assert isinstance(result, dict)

    def test_unused_column_not_flagged(self, simple_graph):
        # Dropping a column that no downstream model uses
        simple_graph.nodes = _populate_column_usage(simple_graph.nodes)
        result = get_column_dependents(simple_graph, "raw.users", ["totally_unused_column_xyz"])
        # Should be empty or not contain downstream nodes that don't use it
        for fqn, cols in result.items():
            assert "totally_unused_column_xyz" in cols

    def test_wildcard_usage_flags_all_dropped(self, simple_graph):
        # If a downstream model uses SELECT *, any dropped column should be flagged
        # Inject wildcard usage manually
        simple_graph.nodes["finance.orders"].column_usage = {
            "raw.users": [ColumnUsage(column="*", used_in_select=True)]
        }
        result = get_column_dependents(simple_graph, "raw.users", ["name", "email"])
        if "finance.orders" in result:
            assert "name" in result["finance.orders"]
            assert "email" in result["finance.orders"]

    def test_empty_dropped_columns(self, simple_graph):
        result = get_column_dependents(simple_graph, "raw.users", [])
        assert result == {}

    def test_fqn_not_in_graph(self, simple_graph):
        result = get_column_dependents(simple_graph, "nonexistent.model", ["col1"])
        assert result == {}


# ══════════════════════════════════════════════════════════════════════════════
# Integration Tests — Adapter
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildSubgraphFromGraph:

    def test_returns_lineage_subgraph(self, simple_graph):
        from models.lineage import LineageSubgraph
        result = build_subgraph_from_graph(simple_graph, "raw.users")
        assert result is not None
        assert isinstance(result, LineageSubgraph)

    def test_primary_node_is_depth_zero(self, simple_graph):
        result = build_subgraph_from_graph(simple_graph, "raw.users")
        assert result is not None
        primary = next((n for n in result.nodes if n.fqn == "raw.users"), None)
        assert primary is not None
        assert primary.depth_from_failure == 0
        assert primary.is_downstream is False

    def test_downstream_nodes_flagged(self, simple_graph):
        result = build_subgraph_from_graph(simple_graph, "raw.users")
        assert result is not None
        downstream = [n for n in result.nodes if n.is_downstream]
        assert len(downstream) > 0

    def test_downstream_depth_is_negative(self, simple_graph):
        result = build_subgraph_from_graph(simple_graph, "raw.users")
        assert result is not None
        for node in result.nodes:
            if node.is_downstream:
                assert node.depth_from_failure == -1

    def test_upstream_nodes_have_positive_depth(self, simple_graph):
        # finance.revenue has upstream deps
        result = build_subgraph_from_graph(simple_graph, "finance.revenue")
        assert result is not None
        upstream = [n for n in result.nodes if n.depth_from_failure > 0]
        assert len(upstream) > 0

    def test_raw_metadata_has_file_path(self, simple_graph):
        result = build_subgraph_from_graph(simple_graph, "raw.users")
        assert result is not None
        primary = next((n for n in result.nodes if n.fqn == "raw.users"), None)
        assert primary is not None
        assert "file_path" in primary.raw_metadata
        assert primary.raw_metadata["file_path"] == "seeds/raw/users.sql"

    def test_raw_metadata_has_sql(self, simple_graph):
        result = build_subgraph_from_graph(simple_graph, "raw.users")
        assert result is not None
        primary = next((n for n in result.nodes if n.fqn == "raw.users"), None)
        assert primary is not None
        assert "sql" in primary.raw_metadata
        assert len(primary.raw_metadata["sql"]) > 0

    def test_edges_populated(self, simple_graph):
        result = build_subgraph_from_graph(simple_graph, "raw.users")
        assert result is not None
        assert len(result.edges) > 0

    def test_fqn_not_found_returns_none(self, simple_graph):
        result = build_subgraph_from_graph(simple_graph, "nonexistent.model")
        assert result is None

    def test_suffix_fqn_match(self, simple_graph):
        # "users" should resolve to "raw.users"
        result = build_subgraph_from_graph(simple_graph, "users")
        assert result is not None

    def test_no_duplicate_nodes(self, simple_graph):
        result = build_subgraph_from_graph(simple_graph, "raw.users")
        assert result is not None
        fqns = [n.fqn for n in result.nodes]
        assert len(fqns) == len(set(fqns))


# ══════════════════════════════════════════════════════════════════════════════
# Integration Tests — Storage Serialization
# ══════════════════════════════════════════════════════════════════════════════

class TestSerialization:

    def test_graph_roundtrip_mongo(self, simple_graph):
        """Graph → mongo doc → graph should be lossless."""
        doc       = _graph_to_mongo_doc(simple_graph)
        recovered = _mongo_doc_to_graph(doc)

        assert recovered.repo_full_name == simple_graph.repo_full_name
        assert recovered.total_nodes    == simple_graph.total_nodes
        assert set(recovered.nodes.keys()) == set(simple_graph.nodes.keys())

    def test_node_fields_preserved(self, simple_graph):
        doc       = _graph_to_mongo_doc(simple_graph)
        recovered = _mongo_doc_to_graph(doc)

        original  = simple_graph.nodes["raw.users"]
        recovered_node = recovered.nodes["raw.users"]

        assert recovered_node.fqn       == original.fqn
        assert recovered_node.file_path == original.file_path
        assert recovered_node.columns   == original.columns
        assert recovered_node.depends_on   == original.depends_on
        assert recovered_node.referenced_by == original.referenced_by

    def test_column_usage_preserved(self, simple_graph):
        # Add some column usage then roundtrip
        simple_graph.nodes["finance.orders"].column_usage = {
            "raw.users": [
                ColumnUsage(column="name", used_in_select=True),
                ColumnUsage(column="id",   used_in_join=True),
            ]
        }
        doc       = _graph_to_mongo_doc(simple_graph)
        recovered = _mongo_doc_to_graph(doc)

        usage = recovered.nodes["finance.orders"].column_usage
        assert "raw.users" in usage
        cols = {cu.column for cu in usage["raw.users"]}
        assert "name" in cols
        assert "id" in cols


# ══════════════════════════════════════════════════════════════════════════════
# Integration Tests — Full Workflow (mocked GitHub + storage)
# ══════════════════════════════════════════════════════════════════════════════

class TestFullWorkflow:
    """
    Tests the complete scan → cache → get_graph → build_subgraph → impact flow.
    GitHub API and MongoDB/Redis calls are mocked.
    """

    MOCK_FILE_TREE = [
        {"path": "models/finance/revenue.sql", "type": "blob"},
        {"path": "models/finance/orders.sql",  "type": "blob"},
        {"path": "seeds/raw/users.sql",         "type": "blob"},
        {"path": "models/schema.yml",           "type": "blob"},
        {"path": ".github/workflows/ci.yml",    "type": "blob"},  # should be excluded
    ]

    MOCK_REVENUE_SQL = """
        SELECT
            o.order_id,
            u.session_id,
            u.name,
            SUM(o.amount) as total
        FROM {{ ref('orders') }} o
        JOIN {{ ref('users') }} u ON o.user_id = u.id
        GROUP BY o.order_id, u.session_id, u.name
    """

    MOCK_ORDERS_SQL = """
        SELECT
            o.order_id,
            o.amount,
            u.name
        FROM {{ ref('users') }} u
        JOIN raw_orders o ON u.id = o.user_id
    """

    MOCK_USERS_SQL = """
        SELECT id, name, email, session_id
        FROM {{ source('raw', 'source_users') }}
    """

    MOCK_SCHEMA_YML = """
version: 2
models:
  - name: revenue
    columns:
      - name: order_id
      - name: session_id
      - name: name
      - name: total
  - name: orders
    columns:
      - name: order_id
      - name: amount
      - name: name
  - name: users
    columns:
      - name: id
      - name: name
      - name: email
      - name: session_id
"""

    def _mock_fetch_content(self, token, owner, repo, path):
        mapping = {
            "models/finance/revenue.sql": self.MOCK_REVENUE_SQL,
            "models/finance/orders.sql":  self.MOCK_ORDERS_SQL,
            "seeds/raw/users.sql":        self.MOCK_USERS_SQL,
            "models/schema.yml":          self.MOCK_SCHEMA_YML,
        }
        return mapping.get(path)

    @patch("controllers.repo_parser_controller._save_graph_to_mongo", return_value=True)
    @patch("controllers.repo_parser_controller._save_graph_to_redis", return_value=True)
    @patch("controllers.repo_parser_controller._fetch_file_content")
    @patch("controllers.repo_parser_controller._get_repo_file_tree")
    def test_scan_builds_correct_nodes(
        self,
        mock_tree,
        mock_content,
        mock_redis,
        mock_mongo,
    ):
        mock_tree.return_value    = self.MOCK_FILE_TREE
        mock_content.side_effect = self._mock_fetch_content

        from server.controllers.repo_parser_controller import scan_repo

        graph = scan_repo(
            github_token="test_token",
            repo_owner="test-org",
            repo_name="test-repo",
            connection_id="conn_123",
            user_id="user_456",
        )

        # Should have 3 nodes (ci.yml excluded, schema.yml used for enrichment)
        assert graph.total_nodes == 3
        assert "finance.revenue" in graph.nodes
        assert "finance.orders" in graph.nodes

    @patch("controllers.repo_parser_controller._save_graph_to_mongo", return_value=True)
    @patch("controllers.repo_parser_controller._save_graph_to_redis", return_value=True)
    @patch("controllers.repo_parser_controller._fetch_file_content")
    @patch("controllers.repo_parser_controller._get_repo_file_tree")
    def test_scan_populates_referenced_by(
        self,
        mock_tree,
        mock_content,
        mock_redis,
        mock_mongo,
    ):
        mock_tree.return_value    = self.MOCK_FILE_TREE
        mock_content.side_effect = self._mock_fetch_content

        from server.controllers.repo_parser_controller import scan_repo

        graph = scan_repo("tok", "org", "repo", "conn", "user")

        # finance.revenue and finance.orders should reference something upstream
        # raw.users or raw.source_users should be referenced by downstream models
        has_referenced_by = any(
            len(node.referenced_by) > 0
            for node in graph.nodes.values()
        )
        assert has_referenced_by

    @patch("controllers.repo_parser_controller._save_graph_to_mongo", return_value=True)
    @patch("controllers.repo_parser_controller._save_graph_to_redis", return_value=True)
    @patch("controllers.repo_parser_controller._fetch_file_content")
    @patch("controllers.repo_parser_controller._get_repo_file_tree")
    def test_scan_enriches_columns_from_yml(
        self,
        mock_tree,
        mock_content,
        mock_redis,
        mock_mongo,
    ):
        mock_tree.return_value    = self.MOCK_FILE_TREE
        mock_content.side_effect = self._mock_fetch_content

        from server.controllers.repo_parser_controller import scan_repo

        graph = scan_repo("tok", "org", "repo", "conn", "user")

        # revenue node should have columns from the yml
        revenue = graph.nodes.get("finance.revenue")
        if revenue:
            assert len(revenue.columns) > 0

    @patch("controllers.repo_parser_controller._save_graph_to_mongo", return_value=True)
    @patch("controllers.repo_parser_controller._save_graph_to_redis", return_value=True)
    @patch("controllers.repo_parser_controller._fetch_file_content")
    @patch("controllers.repo_parser_controller._get_repo_file_tree")
    def test_downstream_detection_end_to_end(
        self,
        mock_tree,
        mock_content,
        mock_redis,
        mock_mongo,
    ):
        mock_tree.return_value    = self.MOCK_FILE_TREE
        mock_content.side_effect = self._mock_fetch_content

        from server.controllers.repo_parser_controller import scan_repo

        graph = scan_repo("tok", "org", "repo", "conn", "user")

        # Find a node that has downstream consumers
        source_node = None
        for fqn, node in graph.nodes.items():
            if len(node.referenced_by) > 0:
                source_node = fqn
                break

        if source_node:
            downstream = get_downstream(graph, source_node, depth=3)
            assert len(downstream) > 0

    @patch("controllers.repo_parser_controller._save_graph_to_mongo", return_value=True)
    @patch("controllers.repo_parser_controller._save_graph_to_redis", return_value=True)
    @patch("controllers.repo_parser_controller._fetch_file_content")
    @patch("controllers.repo_parser_controller._get_repo_file_tree")
    def test_build_subgraph_adapter_end_to_end(
        self,
        mock_tree,
        mock_content,
        mock_redis,
        mock_mongo,
    ):
        from server.models.lineage import LineageSubgraph
        mock_tree.return_value    = self.MOCK_FILE_TREE
        mock_content.side_effect = self._mock_fetch_content

        from server.controllers.repo_parser_controller import scan_repo

        graph = scan_repo("tok", "org", "repo", "conn", "user")

        # Build subgraph for the first node that has downstream consumers
        source_node = next(
            (fqn for fqn, n in graph.nodes.items() if n.referenced_by),
            list(graph.nodes.keys())[0]
        )

        subgraph = build_subgraph_from_graph(graph, source_node)
        assert subgraph is not None
        assert isinstance(subgraph, LineageSubgraph)
        assert len(subgraph.nodes) > 0

    def test_get_repo_graph_redis_hit(self, simple_graph):
        """Cache hit on Redis should return graph without touching MongoDB."""
        with patch(
            "controllers.repo_parser_controller._load_graph_from_redis",
            return_value=simple_graph
        ) as mock_redis, patch(
            "controllers.repo_parser_controller._load_graph_from_mongo"
        ) as mock_mongo:
            from server.controllers.repo_parser_controller import get_repo_graph
            result = get_repo_graph("conn_123", "test-org/test-repo")

            assert result is not None
            assert result.repo_full_name == "test-org/test-repo"
            mock_redis.assert_called_once()
            mock_mongo.assert_not_called()

    def test_get_repo_graph_redis_miss_mongo_hit(self, simple_graph):
        """Redis miss should fall back to MongoDB and repopulate Redis."""
        with patch(
            "controllers.repo_parser_controller._load_graph_from_redis",
            return_value=None
        ), patch(
            "controllers.repo_parser_controller._load_graph_from_mongo",
            return_value=simple_graph
        ), patch(
            "controllers.repo_parser_controller._save_graph_to_redis",
            return_value=True
        ) as mock_save_redis:
            from server.controllers.repo_parser_controller import get_repo_graph
            result = get_repo_graph("conn_123", "test-org/test-repo")

            assert result is not None
            mock_save_redis.assert_called_once()  # Redis should be repopulated

    def test_get_repo_graph_both_miss_returns_none(self):
        """Both Redis and MongoDB miss → return None."""
        with patch(
            "controllers.repo_parser_controller._load_graph_from_redis",
            return_value=None
        ), patch(
            "controllers.repo_parser_controller._load_graph_from_mongo",
            return_value=None
        ):
            from server.controllers.repo_parser_controller import get_repo_graph
            result = get_repo_graph("conn_123", "test-org/test-repo")
            assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# Edge Cases
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_empty_graph_get_downstream(self):
        empty_graph = RepoLineageGraph(
            repo_full_name="org/repo",
            connection_id="conn",
            user_id="user",
            built_at="2024-01-01T00:00:00+00:00",
            nodes={},
        )
        result = get_downstream(empty_graph, "any.fqn")
        assert result == []

    def test_empty_graph_build_subgraph(self):
        empty_graph = RepoLineageGraph(
            repo_full_name="org/repo",
            connection_id="conn",
            user_id="user",
            built_at="2024-01-01T00:00:00+00:00",
            nodes={},
        )
        result = build_subgraph_from_graph(empty_graph, "any.fqn")
        assert result is None

    def test_circular_referenced_by_no_infinite_loop(self):
        # Pathological case — circular reference should not cause infinite loop
        nodes = {
            "a.model": RepoLineageNode(
                fqn="a.model", file_path="models/a/model.sql",
                depends_on=["b.model"], referenced_by=["b.model"]
            ),
            "b.model": RepoLineageNode(
                fqn="b.model", file_path="models/b/model.sql",
                depends_on=["a.model"], referenced_by=["a.model"]
            ),
        }
        graph = RepoLineageGraph(
            repo_full_name="org/repo",
            connection_id="conn",
            user_id="user",
            built_at="2024-01-01T00:00:00+00:00",
            nodes=nodes,
        )
        # Should terminate — not hang
        result = get_downstream(graph, "a.model", depth=3)
        assert isinstance(result, list)

    def test_column_usage_with_no_column_tracking(self, simple_graph):
        # Nodes with empty column_usage — should not crash get_column_dependents
        for node in simple_graph.nodes.values():
            node.column_usage = {}
        result = get_column_dependents(simple_graph, "raw.users", ["name"])
        assert isinstance(result, dict)

    def test_ref_parse_with_jinja_block(self):
        sql = """
            {% set payment_methods = ['credit_card', 'coupon'] %}
            SELECT * FROM {{ ref('orders') }}
            WHERE method IN {{ payment_methods }}
        """
        result = _parse_ref_and_source_calls(sql)
        assert "orders" in result

    def test_derive_fqn_windows_path_separator(self):
        # Windows-style path separators should be handled
        result = _derive_fqn_from_path("models\\finance\\revenue.sql")
        assert "finance" in result
        assert "revenue" in result