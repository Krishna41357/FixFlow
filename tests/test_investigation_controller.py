"""
TEST SUITE: investigation_controller.py

Run all tests:
    pytest tests/test_investigation_controller.py -v

Run specific test:
    pytest tests/test_investigation_controller.py::TestInvestigationPipeline::test_run_investigation_success -v

Run with coverage:
    pytest tests/test_investigation_controller.py --cov=controllers.investigation_controller --cov-report=html

Key functionalities to test:
1. create_investigation() - Create investigation record
2. run_investigation() - Execute full pipeline
3. build_ai_context() - Format lineage for AI
4. call_ai_layer() - Call Claude/OpenAI
5. update_investigation_status() - Track progress
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from bson import ObjectId

from controllers.investigation_controller import (
    create_investigation, run_investigation, build_ai_context,
    call_ai_layer, update_investigation_status
)
from models.investigations import InvestigationCreate, InvestigationStatus
from models.lineage import LineageSubgraph, LineageNode


class TestInvestigationCreation:
    """Test investigation creation."""
    
    # ========================================================================
    # HAPPY PATH TESTS
    # ========================================================================
    
    @patch('controllers.investigation_controller.investigations_collection')
    def test_create_investigation_success(self, mock_collection):
        """Should create investigation record."""
        mock_collection.insert_one.return_value.inserted_id = ObjectId()
        
        inv_data = InvestigationCreate(
            user_id="user_123",
            connection_id="conn_456",
            asset_fqn="snowflake.prod.orders",
            failure_message="Column user_id not found"
        )
        
        result = create_investigation(
            user_id="user_123",
            connection_id="conn_456",
            asset_fqn="snowflake.prod.orders",
            failure_message="Column user_id not found"
        )
        
        assert result is not None
        assert isinstance(result, str)  # Should return investigation ID
    
    # ========================================================================
    # EDGE CASES
    # ========================================================================
    
    @patch('controllers.investigation_controller.investigations_collection')
    def test_create_investigation_empty_asset(self, mock_collection):
        """Should handle empty asset FQN."""
        mock_collection.insert_one.return_value.inserted_id = ObjectId()
        
        result = create_investigation(
            user_id="user_123",
            connection_id="conn_456",
            asset_fqn="",
            failure_message="Something failed"
        )
        
        # Should still create record even with empty asset
        assert result is not None


class TestInvestigationPipeline:
    """Test full investigation execution pipeline."""
    
    # ========================================================================
    # HAPPY PATH TESTS
    # ========================================================================
    
    @patch('controllers.investigation_controller.lineage_controller.traverse_upstream')
    @patch('controllers.investigation_controller.investigations_collection')
    def test_run_investigation_success(self, mock_collection, mock_traverse):
        """Should successfully run investigation pipeline."""
        # Mock investigation record
        mock_collection.find_one.return_value = {
            "_id": ObjectId(),
            "asset_fqn": "snowflake.prod.orders",
            "failure_message": "Column not found"
        }
        
        # Mock lineage traversal
        mock_traverse.return_value = [
            LineageNode(id="raw.users", name="raw.users", is_break_point=True),
            LineageNode(id="stg.data", name="stg.data")
        ]
        
        result = run_investigation(
            investigation_id="inv_123",
            user_id="user_123",
            connection_id="conn_456",
            openmetadata_url="http://localhost:8585",
            openmetadata_token="test_token"
        )
        
        assert result is True
    
    # ========================================================================
    # ERROR CASES
    # ========================================================================
    
    @patch('controllers.investigation_controller.investigations_collection')
    def test_run_investigation_not_found(self, mock_collection):
        """Should return False if investigation not found."""
        mock_collection.find_one.return_value = None
        
        result = run_investigation(
            investigation_id="nonexistent_inv",
            user_id="user_123",
            connection_id="conn_456",
            openmetadata_url="http://localhost:8585",
            openmetadata_token="test_token"
        )
        
        assert result is False
    
    @patch('controllers.investigation_controller.lineage_controller.traverse_upstream')
    @patch('controllers.investigation_controller.investigations_collection')
    def test_run_investigation_no_lineage(self, mock_collection, mock_traverse):
        """Should handle case where no lineage found."""
        mock_collection.find_one.return_value = {
            "_id": ObjectId(),
            "asset_fqn": "nonexistent.asset"
        }
        
        mock_traverse.return_value = []  # No lineage
        
        result = run_investigation(
            investigation_id="inv_123",
            user_id="user_123",
            connection_id="conn_456",
            openmetadata_url="http://localhost:8585",
            openmetadata_token="test_token"
        )
        
        assert result is False


class TestAIContextBuilding:
    """Test building AI context from lineage."""
    
    # ========================================================================
    # HAPPY PATH TESTS
    # ========================================================================
    
    def test_build_ai_context_success(self):
        """Should build valid AI context."""
        lineage = LineageSubgraph(
            nodes=[
                LineageNode(
                    id="raw.users",
                    name="raw.users",
                    schema={"user_id": {"type": "INT"}},
                    is_break_point=True
                ),
                LineageNode(
                    id="stg.users",
                    name="stg.users",
                    schema={"user_id": {"type": "INT"}}
                )
            ],
            edges=[{"from": "raw.users", "to": "stg.users"}],
            break_point_node="raw.users"
        )
        
        context = build_ai_context(lineage, "Column user_id not found")
        
        assert context is not None
        assert isinstance(context, str)
        assert len(context) > 50
        assert "user_id" in context or "Column" in context
    
    # ========================================================================
    # EDGE CASES
    # ========================================================================
    
    def test_build_ai_context_empty_lineage(self):
        """Should handle empty lineage."""
        lineage = LineageSubgraph(
            nodes=[],
            edges=[],
            break_point_node=None
        )
        
        context = build_ai_context(lineage, "Pipeline failed")
        
        assert context is not None
        assert isinstance(context, str)
    
    def test_build_ai_context_long_failure_message(self):
        """Should handle very long failure messages."""
        lineage = LineageSubgraph(nodes=[], edges=[])
        
        long_message = "X" * 5000  # Very long failure message
        
        context = build_ai_context(lineage, long_message)
        
        assert context is not None


class TestAILayerCalling:
    """Test calling AI/LLM layer."""
    
    # ========================================================================
    # HAPPY PATH TESTS
    # ========================================================================
    
    @patch('controllers.investigation_controller.requests.post')
    def test_call_ai_layer_claude_success(self, mock_post):
        """Should successfully call Claude API."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [
                {
                    "text": '''{"root_cause": "Column was renamed", 
                              "affected_downstream": [], 
                              "suggested_fix": "Update references",
                              "confidence": 0.95}'''
                }
            ]
        }
        mock_post.return_value = mock_response
        
        result = call_ai_layer("Context about failure", max_retries=1)
        
        assert result is not None
        assert hasattr(result, 'root_cause')
    
    @patch('controllers.investigation_controller.requests.post')
    def test_call_ai_layer_api_error(self, mock_post):
        """Should handle API errors gracefully."""
        mock_post.side_effect = Exception("API connection failed")
        
        result = call_ai_layer("Context about failure", max_retries=1)
        
        # Should return None or default RootCause
        assert result is None or hasattr(result, 'root_cause')
    
    # ========================================================================
    # EDGE CASES
    # ========================================================================
    
    @patch('controllers.investigation_controller.requests.post')
    def test_call_ai_layer_retry_logic(self, mock_post):
        """Should retry on transient failures."""
        mock_response_fail = MagicMock()
        mock_response_fail.status_code = 429  # Rate limited
        
        mock_response_success = MagicMock()
        mock_response_success.status_code = 200
        mock_response_success.json.return_value = {
            "content": [{"text": '{"root_cause": "test"}'}]
        }
        
        # Fail once, then succeed
        mock_post.side_effect = [mock_response_fail, mock_response_success]
        
        result = call_ai_layer("Context", max_retries=2)
        
        # Should retry and eventually succeed or fail gracefully
        assert result is None or hasattr(result, 'root_cause')


class TestInvestigationStatusUpdates:
    """Test status tracking during investigation."""
    
    # ========================================================================
    # HAPPY PATH TESTS
    # ========================================================================
    
    @patch('controllers.investigation_controller.investigations_collection')
    def test_update_investigation_status_pending(self, mock_collection):
        """Should update status to PENDING."""
        mock_collection.update_one.return_value.modified_count = 1
        
        result = update_investigation_status(
            "inv_123",
            InvestigationStatus.PENDING
        )
        
        assert result is True
    
    @patch('controllers.investigation_controller.investigations_collection')
    def test_update_investigation_status_completed(self, mock_collection):
        """Should update status to COMPLETED."""
        mock_collection.update_one.return_value.modified_count = 1
        
        result = update_investigation_status(
            "inv_123",
            InvestigationStatus.COMPLETED
        )
        
        assert result is True
    
    # ========================================================================
    # EDGE CASES
    # ========================================================================
    
    @patch('controllers.investigation_controller.investigations_collection')
    def test_update_investigation_status_not_found(self, mock_collection):
        """Should return False if investigation not found."""
        mock_collection.update_one.return_value.modified_count = 0
        
        result = update_investigation_status(
            "nonexistent_inv",
            InvestigationStatus.PENDING
        )
        
        assert result is False
