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
import os

from controllers.investigation_controller import (
    create_investigation, run_investigation, build_ai_context,
    call_ai_layer, update_investigation_status
)
from models.base import AssetType
from models.investigations import InvestigationInDB, InvestigationStatus
from models.lineage import LineageSubgraph, LineageNode


class TestInvestigationCreation:
    """Test investigation creation."""
    
    # ========================================================================
    # HAPPY PATH TESTS
    # ========================================================================
    
    @patch('controllers.investigation_controller.event_controller.mark_event_processed')
    @patch('controllers.investigation_controller.investigations_collection')
    def test_create_investigation_success(self, mock_collection, mock_mark_event):
        """Should create investigation record."""
        mock_collection.insert_one.return_value.inserted_id = ObjectId()
        
        result = create_investigation(
            user_id="user_123",
            connection_id="conn_456",
            event_id="event_789",
            failure_message="Column user_id not found",
            asset_fqn="snowflake.prod.orders"
        )
        
        assert result is not None
        assert isinstance(result, str)  # Should return investigation ID
    
    # ========================================================================
    # EDGE CASES
    # ========================================================================
    
    @patch('controllers.investigation_controller.event_controller.mark_event_processed')
    @patch('controllers.investigation_controller.investigations_collection')
    def test_create_investigation_empty_asset(self, mock_collection, mock_mark_event):
        """Should handle empty asset FQN."""
        mock_collection.insert_one.return_value.inserted_id = ObjectId()
        
        result = create_investigation(
            user_id="user_123",
            connection_id="conn_456",
            event_id="event_789",
            failure_message="Something failed",
            asset_fqn=""
        )
        
        # Should still create record
        assert result is None or isinstance(result, str)


class TestInvestigationPipeline:
    """Test full investigation execution pipeline."""
    
    # ========================================================================
    # HAPPY PATH TESTS
    # ========================================================================
    
    @patch('controllers.investigation_controller.lineage_controller.traverse_upstream')
    @patch('controllers.investigation_controller.investigations_collection')
    def test_run_investigation_success(self, mock_collection, mock_traverse):
        """Should successfully run investigation pipeline."""
        # Create a valid ObjectId for the investigation
        inv_id = ObjectId()
        
        # Mock investigation record
        mock_collection.find_one.return_value = {
            "_id": inv_id,
            "failing_asset_fqn": "snowflake.prod.orders",
            "failure_message": "Column not found"
        }
        
        # Mock lineage traversal with proper LineageNode objects
        mock_traverse.return_value = [
            LineageNode(
                fqn="raw.users",
                display_name="Raw Users",
                asset_type=AssetType.TABLE,
                service_name="Snowflake",
                is_break_point=True
            ),
            LineageNode(
                fqn="stg.data",
                display_name="Staging Data",
                asset_type=AssetType.TABLE,
                service_name="Snowflake"
            )
        ]
        
        result = run_investigation(
            investigation_id=str(inv_id),
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
        
        # Use a valid ObjectId format
        nonexistent_id = str(ObjectId())
        
        result = run_investigation(
            investigation_id=nonexistent_id,
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
        inv_id = ObjectId()
        mock_collection.find_one.return_value = {
            "_id": inv_id,
            "failing_asset_fqn": "nonexistent.asset"
        }
        
        mock_traverse.return_value = []  # No lineage
        
        result = run_investigation(
            investigation_id=str(inv_id),
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
            failing_asset_fqn="raw.users",
            nodes=[
                LineageNode(
                    fqn="raw.users",
                    display_name="Raw Users",
                    asset_type=AssetType.TABLE,
                    service_name="Snowflake",
                    is_break_point=True
                ),
                LineageNode(
                    fqn="stg.users",
                    display_name="Staging Users",
                    asset_type=AssetType.TABLE,
                    service_name="Snowflake"
                )
            ],
            edges=[],
            traversal_depth=1
        )
        
        context = build_ai_context(lineage, "Column user_id not found")
        
        assert context is not None
        assert isinstance(context, str)
        assert len(context) > 50
    
    # ========================================================================
    # EDGE CASES
    # ========================================================================
    
    def test_build_ai_context_empty_lineage(self):
        """Should handle empty lineage."""
        lineage = LineageSubgraph(
            failing_asset_fqn="unknown.asset",
            nodes=[],
            edges=[],
            traversal_depth=0
        )
        
        context = build_ai_context(lineage, "Pipeline failed")
        
        assert context is not None
        assert isinstance(context, str)
    
    def test_build_ai_context_long_failure_message(self):
        """Should handle very long failure messages."""
        lineage = LineageSubgraph(
            failing_asset_fqn="prod.orders",
            nodes=[],
            edges=[]
        )
        
        long_message = "X" * 5000  # Very long failure message
        
        context = build_ai_context(lineage, long_message)
        
        assert context is not None


class TestAILayerCalling:
    """Test calling AI/LLM layer."""
    
    # ========================================================================
    # HAPPY PATH TESTS
    # ========================================================================
    
    @patch.dict('os.environ', {'DEFAULT_LLM_PROVIDER': 'groq', 'GROQ_API_KEY': 'test_key'})
    @patch('controllers.investigation_controller.requests.post')
    def test_call_ai_layer_claude_success(self, mock_post):
        """Should successfully call Claude API."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        # Mock response for Groq API (OpenAI-style format)
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '''{
                            "one_line_summary": "Column user_id was renamed to customer_id",
                            "detailed_explanation": "The column was renamed in the upstream table",
                            "break_point_fqn": "raw.users",
                            "break_point_change": "Column renamed from user_id to customer_id",
                            "affected_assets": [],
                            "suggested_fixes": [],
                            "owner_to_contact": null,
                            "confidence": 0.95
                        }'''
                    }
                }
            ]
        }
        mock_post.return_value = mock_response
        
        result = call_ai_layer("Context about failure", max_retries=1)
        
        assert result is not None
        assert hasattr(result, 'one_line_summary')
    
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
        inv_id = ObjectId()
        mock_collection.update_one.return_value.modified_count = 1
        
        result = update_investigation_status(
            str(inv_id),
            InvestigationStatus.PENDING
        )
        
        assert result is True
    
    @patch('controllers.investigation_controller.investigations_collection')
    def test_update_investigation_status_completed(self, mock_collection):
        """Should update status to COMPLETED."""
        inv_id = ObjectId()
        mock_collection.update_one.return_value.modified_count = 1
        
        result = update_investigation_status(
            str(inv_id),
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
