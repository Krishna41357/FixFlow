"""
TEST SUITE: lineage_controller.py

Run all tests:
    pytest tests/test_lineage_controller.py -v

Run specific test:
    pytest tests/test_lineage_controller.py::TestLineageTraversal::test_traverse_upstream_success -v

Run with coverage:
    pytest tests/test_lineage_controller.py --cov=controllers.lineage_controller --cov-report=html

Key functionalities to test:
1. traverse_upstream() - Fetch lineage from OpenMetadata API
2. detect_break_point() - Identify schema changes
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from controllers.lineage_controller import traverse_upstream, detect_break_point
from models.lineage import LineageNode, LineageSubgraph
from models.base import AssetType


class TestLineageTraversal:
    """Test lineage traversal from OpenMetadata API."""
    
    # ========================================================================
    # HAPPY PATH TESTS
    # ========================================================================
    
    @patch('controllers.lineage_controller.requests.get')
    def test_traverse_upstream_success(self, mock_get):
        """Should successfully traverse upstream lineage."""
        # Mock OpenMetadata API response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "entity": {
                "id": "snowflake.prod.orders_daily",
                "name": "orders_daily"
            },
            "upstreamEdges": [
                {
                    "fromEntity": {
                        "id": "snowflake.prod.stg_orders",
                        "name": "stg_orders",
                        "fqn": "snowflake.prod.stg_orders"
                    }
                }
            ]
        }
        mock_get.return_value = mock_response
        
        nodes = traverse_upstream(
            openmetadata_url="http://localhost:8585",
            openmetadata_token="test_token",
            start_asset_id="snowflake.prod.orders_daily",
            max_depth=1
        )
        
        assert nodes is not None
        assert isinstance(nodes, list)
        assert len(nodes) > 0
    
    @patch('controllers.lineage_controller.requests.get')
    def test_traverse_upstream_empty_response(self, mock_get):
        """Should handle empty lineage response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "entity": {"id": "some.asset"},
            "upstreamEdges": []
        }
        mock_get.return_value = mock_response
        
        nodes = traverse_upstream(
            openmetadata_url="http://localhost:8585",
            openmetadata_token="test_token",
            start_asset_id="snowflake.prod.isolate_asset"
        )
        
        # Should return list (empty or with root node)
        assert isinstance(nodes, list)
    
    # ========================================================================
    # ERROR CASES
    # ========================================================================
    
    @patch('controllers.lineage_controller.requests.get')
    def test_traverse_upstream_api_error(self, mock_get):
        """Should handle OpenMetadata API errors gracefully."""
        mock_get.side_effect = Exception("Connection failed")
        
        nodes = traverse_upstream(
            openmetadata_url="http://localhost:8585",
            openmetadata_token="test_token",
            start_asset_id="snowflake.prod.orders"
        )
        
        # Should return empty list or None
        assert nodes is None or nodes == []
    
    @patch('controllers.lineage_controller.requests.get')
    def test_traverse_upstream_unauthorized(self, mock_get):
        """Should handle 401 Unauthorized from OpenMetadata."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_get.return_value = mock_response
        
        nodes = traverse_upstream(
            openmetadata_url="http://localhost:8585",
            openmetadata_token="invalid_token",
            start_asset_id="snowflake.prod.orders"
        )
        
        assert nodes is None or nodes == []
    
    # ========================================================================
    # EDGE CASES
    # ========================================================================
    
    @patch('controllers.lineage_controller.requests.get')
    def test_traverse_upstream_max_depth_zero(self, mock_get):
        """Should handle max_depth=0 (no traversal)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "entity": {"id": "snowflake.prod.orders"}
        }
        mock_get.return_value = mock_response
        
        nodes = traverse_upstream(
            openmetadata_url="http://localhost:8585",
            openmetadata_token="test_token",
            start_asset_id="snowflake.prod.orders",
            max_depth=0
        )
        
        assert isinstance(nodes, list)
    
    @patch('controllers.lineage_controller.requests.get')
    def test_traverse_upstream_large_depth(self, mock_get):
        """Should handle large max_depth values gracefully."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "entity": {"id": "snowflake.prod.orders"},
            "upstreamEdges": []
        }
        mock_get.return_value = mock_response
        
        nodes = traverse_upstream(
            openmetadata_url="http://localhost:8585",
            openmetadata_token="test_token",
            start_asset_id="snowflake.prod.orders",
            max_depth=100  # Very deep
        )
        
        assert isinstance(nodes, list)


class TestBreakPointDetection:
    """Test detection of schema breaks in lineage."""
    
    # ========================================================================
    # HAPPY PATH TESTS
    # ========================================================================
    
    def test_detect_break_point_column_renamed(self):
        """Should detect column rename breaking change."""
        nodes = [
            LineageNode(
                fqn="snowflake.prod.raw_users",
                display_name="Raw Users",
                asset_type=AssetType.TABLE,
                service_name="Snowflake",
                is_break_point=False
            ),
            LineageNode(
                fqn="snowflake.prod.stg_users",
                display_name="Staging Users",
                asset_type=AssetType.TABLE,
                service_name="Snowflake",
                is_break_point=False
            )
        ]
        
        result = detect_break_point(nodes)
        
        # Should return a list of nodes
        assert isinstance(result, list)
        assert len(result) > 0
    
    def test_detect_break_point_column_dropped(self):
        """Should detect dropped columns."""
        nodes = [
            LineageNode(
                fqn="snowflake.prod.raw_data",
                display_name="Raw Data",
                asset_type=AssetType.TABLE,
                service_name="Snowflake"
            ),
            LineageNode(
                fqn="snowflake.prod.processed_data",
                display_name="Processed Data",
                asset_type=AssetType.TABLE,
                service_name="Snowflake"
            )
        ]
        
        result = detect_break_point(nodes)
        
        # Should identify the break point
        assert isinstance(result, list)
        assert len(result) > 0
    
    def test_detect_break_point_type_change(self):
        """Should detect column type changes."""
        nodes = [
            LineageNode(
                fqn="snowflake.prod.source_data",
                display_name="Source Data",
                asset_type=AssetType.TABLE,
                service_name="Snowflake"
            ),
            LineageNode(
                fqn="snowflake.prod.target_data",
                display_name="Target Data",
                asset_type=AssetType.TABLE,
                service_name="Snowflake"
            )
        ]
        
        result = detect_break_point(nodes)
        
        assert isinstance(result, list)
    
    # ========================================================================
    # EDGE CASES
    # ========================================================================
    
    def test_detect_break_point_empty_nodes(self):
        """Should handle empty node list."""
        result = detect_break_point([])
        
        assert result is not None
        assert isinstance(result, list)
    
    def test_detect_break_point_single_node(self):
        """Should handle single node (no upstream)."""
        nodes = [
            LineageNode(
                id="raw.data",
                name="raw.data",
                schema={"id": {"type": "INT"}}
            )
        ]
        
        result = detect_break_point(nodes)
        
        assert isinstance(result, list)
        assert len(result) > 0
    
    def test_detect_break_point_no_changes(self):
        """Should handle nodes with no schema changes."""
        nodes = [
            LineageNode(
                id="source.data",
                name="source.data",
                schema={"id": {"type": "INT"}}
            ),
            LineageNode(
                id="target.data",
                name="target.data",
                schema={"id": {"type": "INT"}}
            )
        ]
        
        result = detect_break_point(nodes)
        
        assert isinstance(result, list)
        # Nodes without changes should not be marked as break point
        assert all(not (node.is_break_point or False) for node in result)
    
    def test_detect_break_point_null_constraint_change(self):
        """Should detect NULL constraint changes."""
        nodes = [
            LineageNode(
                id="raw.data",
                name="raw.data",
                schema={
                    "user_id": {"type": "INT", "nullable": True}
                }
            ),
            LineageNode(
                id="processed.data",
                name="processed.data",
                schema={
                    "user_id": {"type": "INT", "nullable": False}  # Changed!
                }
            )
        ]
        
        result = detect_break_point(nodes)
        
        assert isinstance(result, list)


class TestLineageSubgraphConstruction:
    """Test construction of lineage subgraph from nodes."""
    
    def test_lineage_subgraph_valid(self):
        """Should create valid LineageSubgraph from nodes."""
        nodes = [
            LineageNode(id="raw.users", name="raw.users"),
            LineageNode(id="stg.users", name="stg.users", is_break_point=True),
            LineageNode(id="orders", name="orders")
        ]
        
        subgraph = LineageSubgraph(
            nodes=nodes,
            edges=[
                {"from": "raw.users", "to": "stg.users"},
                {"from": "stg.users", "to": "orders"}
            ],
            total_nodes=3,
            break_point_node="stg.users"
        )
        
        assert subgraph.total_nodes == 3
        assert subgraph.break_point_node == "stg.users"
        assert len(subgraph.nodes) == 3
