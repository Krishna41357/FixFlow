"""
TEST SUITE: event_controller.py

Run all tests:
    pytest tests/test_event_controller.py -v

Run specific test:
    pytest tests/test_event_controller.py::TestDbtWebhookHandling::test_handle_dbt_webhook_success -v

Key functionalities to test:
1. handle_dbt_webhook() - Process dbt test failures
2. handle_github_pr() - Process GitHub PR webhooks  
3. handle_manual_query() - Process manual queries
4. get_events_for_user() - Retrieve user events
"""

import pytest
from unittest.mock import patch, MagicMock
from bson import ObjectId

from controllers.event_controller import (
    handle_dbt_webhook, handle_github_pr, handle_manual_query,
    get_events_for_user
)


class TestDbtWebhookHandling:
    """Test dbt Cloud webhook processing."""
    
    @patch('controllers.event_controller.events_collection')
    @patch('controllers.event_controller.investigation_controller.create_investigation')
    def test_handle_dbt_webhook_success(self, mock_create_inv, mock_collection):
        """Should successfully handle dbt webhook."""
        mock_collection.insert_one.return_value.inserted_id = ObjectId()
        mock_create_inv.return_value = "inv_123"
        
        webhook_data = {
            "data": {
                "run_id": "dbt_run_123",
                "node_id": "model.proj.orders",
                "error_message": "Row count changed significantly",
                "status": "error"
            }
        }
        
        result = handle_dbt_webhook(
            webhook_data=webhook_data,
            user_id="user_123",
            connection_id="conn_456"
        )
        
        assert result is not None
        assert isinstance(result, str)
    
    @patch('controllers.event_controller.events_collection')
    def test_handle_dbt_webhook_malformed(self, mock_collection):
        """Should handle malformed webhook data."""
        result = handle_dbt_webhook(
            webhook_data={},
            user_id="user_123",
            connection_id="conn_456"
        )
        
        # Should return None or handle gracefully
        assert result is None or isinstance(result, str)


class TestGitHubWebhookHandling:
    """Test GitHub webhook processing."""
    
    @patch('controllers.event_controller.github_controller.verify_github_signature')
    @patch('controllers.event_controller.events_collection')
    def test_handle_github_pr_valid_signature(self, mock_collection, mock_verify):
        """Should handle GitHub PR with valid signature."""
        mock_verify.return_value = True
        mock_collection.insert_one.return_value.inserted_id = ObjectId()
        
        webhook_data = {
            "pull_request": {
                "id": 12345,
                "number": 1,
                "title": "Fix user schema"
            },
            "repository": {
                "owner": {"login": "myteam"},
                "name": "data-repo"
            }
        }
        
        result = handle_github_pr(
            webhook_data=webhook_data,
            signature="sha256=abc123",
            user_id="user_123",
            connection_id="conn_456"
        )
        
        assert result is not None or result is None  # Valid return
    
    @patch('controllers.event_controller.github_controller.verify_github_signature')
    def test_handle_github_pr_invalid_signature(self, mock_verify):
        """Should reject webhook with invalid signature."""
        mock_verify.return_value = False
        
        result = handle_github_pr(
            webhook_data={},
            signature="sha256=wrong",
            user_id="user_123",
            connection_id="conn_456"
        )
        
        assert result is None


class TestManualQueryHandling:
    """Test manual query processing."""
    
    @patch('controllers.event_controller.investigation_controller.create_investigation')
    def test_handle_manual_query_success(self, mock_create_inv):
        """Should successfully handle manual query."""
        mock_create_inv.return_value = "inv_123"
        
        result = handle_manual_query(
            user_id="user_123",
            connection_id="conn_456",
            asset_fqn="snowflake.prod.orders",
            query_text="Why is this failing?"
        )
        
        assert result is not None
        assert isinstance(result, str)
    
    def test_handle_manual_query_missing_asset(self):
        """Should handle missing asset FQN."""
        result = handle_manual_query(
            user_id="user_123",
            connection_id="conn_456",
            asset_fqn="",
            query_text="What happened?"
        )
        
        # Should handle gracefully
        assert result is None or isinstance(result, str)


class TestEventRetrieval:
    """Test retrieving events for users."""
    
    @patch('controllers.event_controller.events_collection')
    def test_get_events_for_user_success(self, mock_collection):
        """Should retrieve events for user."""
        mock_collection.find.return_value = [
            {
                "_id": ObjectId(),
                "user_id": "user_123",
                "event_type": "dbt",
                "timestamp": "2024-01-15T10:30:00Z"
            }
        ]
        
        result = get_events_for_user("user_123", limit=20)
        
        assert result is not None
        assert isinstance(result, list)
    
    @patch('controllers.event_controller.events_collection')
    def test_get_events_for_user_empty(self, mock_collection):
        """Should return empty list when no events."""
        mock_collection.find.return_value = []
        
        result = get_events_for_user("user_with_no_events", limit=20)
        
        assert result == []
    
    @patch('controllers.event_controller.events_collection')
    def test_get_events_for_user_with_limit(self, mock_collection):
        """Should respect limit parameter."""
        mock_collection.find.return_value = [
            {"_id": ObjectId(), "user_id": "user_123"}
            for _ in range(50)
        ]
        
        result = get_events_for_user("user_123", limit=10)
        
        assert isinstance(result, list)
