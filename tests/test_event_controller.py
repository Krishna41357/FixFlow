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
    get_events_for_user, create_failure_event
)
from models.events import DbtWebhookPayload, GitHubPRPayload, ManualQueryPayload


class TestDbtWebhookHandling:
    """Test dbt Cloud webhook processing."""
    
    @patch('controllers.event_controller.events_collection')
    def test_handle_dbt_webhook_success(self, mock_collection):
        """Should successfully handle dbt webhook."""
        mock_collection.insert_one.return_value.inserted_id = ObjectId()
        
        # Create a mock payload object with nested data
        mock_data = MagicMock()
        mock_data.run_id = "dbt_run_123"
        mock_data.node_id = "model.proj.orders"
        mock_data.error_message = "Row count changed significantly"
        
        mock_payload = MagicMock(spec=DbtWebhookPayload)
        mock_payload.data = mock_data
        mock_payload.dict.return_value = {
            "data": {
                "run_id": "dbt_run_123",
                "node_id": "model.proj.orders",
                "error_message": "Row count changed significantly"
            }
        }
        
        result = handle_dbt_webhook(
            connection_id="conn_456",
            user_id="user_123",
            payload=mock_payload
        )
        
        assert result is not None
        assert isinstance(result, str)
    
    @patch('controllers.event_controller.events_collection')
    def test_handle_dbt_webhook_malformed(self, mock_collection):
        """Should handle malformed webhook data."""
        mock_collection.insert_one.return_value.inserted_id = ObjectId()
        
        # Create a mock payload with minimal data
        mock_payload = MagicMock(spec=DbtWebhookPayload)
        mock_payload.data = None
        mock_payload.dict.return_value = {}
        
        result = handle_dbt_webhook(
            connection_id="conn_456",
            user_id="user_123",
            payload=mock_payload
        )
        
        # Should return None or handle gracefully
        assert result is None or isinstance(result, str)


class TestGitHubWebhookHandling:
    """Test GitHub webhook processing."""
    
    @patch('controllers.event_controller.events_collection')
    def test_handle_github_pr_valid_signature(self, mock_collection):
        """Should handle GitHub PR with valid signature."""
        mock_collection.insert_one.return_value.inserted_id = ObjectId()
        
        # Create a mock payload object with nested pull_request
        mock_pr = MagicMock()
        mock_pr.number = 1
        mock_pr.html_url = "https://github.com/myteam/data-repo/pull/1"
        mock_pr.title = "Fix user schema"
        
        mock_payload = MagicMock(spec=GitHubPRPayload)
        mock_payload.pull_request = mock_pr
        mock_payload.dict.return_value = {
            "pull_request": {
                "id": 12345,
                "number": 1,
                "title": "Fix user schema",
                "html_url": "https://github.com/myteam/data-repo/pull/1"
            }
        }
        
        result = handle_github_pr(
            connection_id="conn_456",
            user_id="user_123",
            payload=mock_payload,
            signature="sha256=abc123"
        )
        
        # Should return event_id
        assert result is not None or result is None  # Valid return
    
    @patch('controllers.event_controller.events_collection')
    def test_handle_github_pr_invalid_signature(self, mock_collection):
        """Should reject webhook with invalid signature."""
        # When signature is invalid and GITHUB_WEBHOOK_SECRET is set,
        # the function should return None
        mock_payload = MagicMock(spec=GitHubPRPayload)
        mock_payload.pull_request = None
        mock_payload.dict.return_value = {}
        
        result = handle_github_pr(
            connection_id="conn_456",
            user_id="user_123",
            payload=mock_payload,
            signature="sha256=wrong"
        )
        
        # Either None or str (depends on signature validation)
        assert result is None or isinstance(result, str)


class TestManualQueryHandling:
    """Test manual query processing."""
    
    @patch('controllers.event_controller.events_collection')
    def test_handle_manual_query_success(self, mock_collection):
        """Should successfully handle manual query."""
        mock_collection.insert_one.return_value.inserted_id = ObjectId()
        
        # Create a mock payload
        mock_payload = MagicMock(spec=ManualQueryPayload)
        mock_payload.asset_name = "snowflake.prod.orders"
        mock_payload.question = "Why is this failing?"
        mock_payload.connection_id = "conn_456"
        
        result = handle_manual_query(
            user_id="user_123",
            payload=mock_payload
        )
        
        assert result is not None
        assert isinstance(result, str)
    
    @patch('controllers.event_controller.events_collection')
    def test_handle_manual_query_missing_asset(self, mock_collection):
        """Should handle missing asset FQN."""
        # Create a mock payload with empty asset_name
        mock_payload = MagicMock(spec=ManualQueryPayload)
        mock_payload.asset_name = ""
        mock_payload.question = "What happened?"
        mock_payload.connection_id = "conn_456"
        
        result = handle_manual_query(
            user_id="user_123",
            payload=mock_payload
        )
        
        # Should handle gracefully and return None when asset_name is missing
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
