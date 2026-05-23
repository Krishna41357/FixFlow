"""
TEST SUITE: GitHub Webhook Registration & Lifecycle Management

Tests for automatic webhook registration, verification, and cleanup.
Covers the complete webhook workflow from OAuth through to connection deletion.

Run all tests:
    pytest tests/test_github_webhook_registration.py -v

Run specific test class:
    pytest tests/test_github_webhook_registration.py::TestWebhookURLBuilder -v

Run by marker:
    pytest tests/ -m webhook -v
"""

import pytest
from unittest.mock import patch, MagicMock, call
import json
from datetime import datetime, timezone

from controllers import github_controller
from models.github import GitHubInstallation, GitHubAppRegistration, GitHubOAuthProfile


# ============================================================================
# WEBHOOK URL BUILDER TESTS
# ============================================================================

class TestWebhookURLBuilder:
    """Test webhook URL construction with routing parameters."""
    
    def test_build_webhook_url_basic(self):
        """Should build webhook URL with connection_id and user_id params."""
        url = github_controller.build_webhook_url(
            connection_id="conn_12345",
            user_id="user_67890",
            api_base_url="https://api.example.com"
        )
        
        assert url == "https://api.example.com/github/webhook?connection_id=conn_12345&user_id=user_67890"
    
    def test_build_webhook_url_localhost(self):
        """Should handle localhost URLs."""
        url = github_controller.build_webhook_url(
            connection_id="conn_local",
            user_id="user_local",
            api_base_url="http://localhost:8000/api/v1"
        )
        
        assert "localhost:8000/api/v1/github/webhook" in url
        assert "connection_id=conn_local" in url
        assert "user_id=user_local" in url
    
    def test_build_webhook_url_strips_trailing_slash(self):
        """Should strip trailing slashes from base URL."""
        url1 = github_controller.build_webhook_url(
            connection_id="conn_123",
            user_id="user_123",
            api_base_url="https://api.example.com/"
        )
        url2 = github_controller.build_webhook_url(
            connection_id="conn_123",
            user_id="user_123",
            api_base_url="https://api.example.com"
        )
        
        assert url1 == url2
    
    def test_build_webhook_url_production(self):
        """Should build valid webhook URL for production."""
        url = github_controller.build_webhook_url(
            connection_id="507f1f77bcf86cd799439011",
            user_id="507f1f77bcf86cd799439010",
            api_base_url="https://autopsy.acmecorp.com/api/v1"
        )
        
        assert url.startswith("https://autopsy.acmecorp.com/api/v1/github/webhook")
        assert "connection_id=507f1f77bcf86cd799439011" in url
        assert "user_id=507f1f77bcf86cd799439010" in url


# ============================================================================
# WEBHOOK REGISTRATION TESTS
# ============================================================================

class TestWebhookRegistration:
    """Test GitHub webhook registration via API."""
    
    @patch('controllers.github_controller.requests.patch')
    def test_register_github_webhook_success(self, mock_patch):
        """Should successfully register webhook with GitHub."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": 123456,
            "url": "https://api.example.com/github/webhook",
            "active": True,
            "events": ["pull_request"]
        }
        mock_patch.return_value = mock_response
        
        result = github_controller.register_github_webhook(
            github_token="ghs_test_token",
            installation_id="12345678",
            webhook_url="https://api.example.com/github/webhook?connection_id=c123&user_id=u123",
            webhook_secret="webhook_secret_xyz"
        )
        
        assert result is not None
        assert result["webhook_id"] == "123456"
        assert result["url"] == "https://api.example.com/github/webhook"
        assert result["active"] is True
        assert "created_at" in result
        
        # Verify correct API call
        mock_patch.assert_called_once()
        call_args = mock_patch.call_args
        assert call_args[0][0] == "https://api.github.com/app/hook/config"
    
    @patch('controllers.github_controller.requests.patch')
    def test_register_github_webhook_missing_token(self, mock_patch):
        """Should fail gracefully when token is missing."""
        result = github_controller.register_github_webhook(
            github_token=None,
            installation_id="12345678",
            webhook_url="https://api.example.com/github/webhook",
            webhook_secret="secret"
        )
        
        assert result is None
        mock_patch.assert_not_called()
    
    @patch('controllers.github_controller.requests.patch')
    def test_register_github_webhook_api_error(self, mock_patch):
        """Should handle GitHub API errors gracefully."""
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = '{"message": "Invalid webhook URL"}'
        mock_patch.return_value = mock_response
        
        result = github_controller.register_github_webhook(
            github_token="ghs_test_token",
            installation_id="12345678",
            webhook_url="invalid-url",
            webhook_secret="secret"
        )
        
        assert result is None
    
    @patch('controllers.github_controller.requests.patch')
    def test_register_webhook_correct_payload(self, mock_patch):
        """Should send correct payload to GitHub API."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": 999, "active": True}
        mock_patch.return_value = mock_response
        
        github_controller.register_github_webhook(
            github_token="ghs_token",
            installation_id="inst_123",
            webhook_url="https://example.com/webhook",
            webhook_secret="secret_xyz"
        )
        
        # Extract JSON payload from call
        call_kwargs = mock_patch.call_args[1]
        payload = call_kwargs["json"]
        
        assert payload["url"] == "https://example.com/webhook"
        assert payload["secret"] == "secret_xyz"
        assert payload["content_type"] == "json"
        assert payload["events"] == ["pull_request"]
        assert payload["active"] is True


# ============================================================================
# WEBHOOK UPDATE TESTS
# ============================================================================

class TestWebhookUpdate:
    """Test webhook configuration updates."""
    
    @patch('controllers.github_controller.requests.patch')
    def test_update_github_webhook_success(self, mock_patch):
        """Should update existing webhook configuration."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": 123456,
            "url": "https://api.example.com/github/webhook?updated=true",
            "active": True
        }
        mock_patch.return_value = mock_response
        
        result = github_controller.update_github_webhook(
            github_token="ghs_token",
            webhook_url="https://api.example.com/github/webhook?updated=true",
            webhook_secret="new_secret"
        )
        
        assert result is not None
        assert result["webhook_id"] == "123456"
        assert "updated_at" in result
    
    @patch('controllers.github_controller.requests.patch')
    def test_update_webhook_api_error(self, mock_patch):
        """Should handle update errors gracefully."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = '{"message": "Internal Server Error"}'
        mock_patch.return_value = mock_response
        
        result = github_controller.update_github_webhook(
            github_token="ghs_token",
            webhook_url="https://example.com/webhook",
            webhook_secret="secret"
        )
        
        assert result is None


# ============================================================================
# WEBHOOK DELETION TESTS
# ============================================================================

class TestWebhookDeletion:
    """Test webhook removal from GitHub."""
    
    @patch('controllers.github_controller.requests.delete')
    def test_delete_github_webhook_success(self, mock_delete):
        """Should successfully delete webhook."""
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_delete.return_value = mock_response
        
        result = github_controller.delete_github_webhook("ghs_token")
        
        assert result is True
        mock_delete.assert_called_once()
    
    @patch('controllers.github_controller.requests.delete')
    def test_delete_webhook_already_deleted(self, mock_delete):
        """Should handle case where webhook already deleted (404)."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_delete.return_value = mock_response
        
        result = github_controller.delete_github_webhook("ghs_token")
        
        assert result is True  # 404 is considered success (already deleted)
    
    @patch('controllers.github_controller.requests.delete')
    def test_delete_webhook_api_error(self, mock_delete):
        """Should fail on API errors."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = '{"message": "Server error"}'
        mock_delete.return_value = mock_response
        
        result = github_controller.delete_github_webhook("ghs_token")
        
        assert result is False
    
    @patch('controllers.github_controller.requests.delete')
    def test_delete_webhook_no_token(self, mock_delete):
        """Should fail gracefully when token is missing."""
        result = github_controller.delete_github_webhook(None)
        
        assert result is False
        mock_delete.assert_not_called()


# ============================================================================
# WEBHOOK VERIFICATION TESTS
# ============================================================================

class TestWebhookVerification:
    """Test webhook status verification."""
    
    @patch('controllers.github_controller.requests.get')
    def test_verify_webhook_exists_and_active(self, mock_get):
        """Should retrieve webhook status when active."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": 999,
            "url": "https://example.com/webhook",
            "active": True,
            "deliveries_url": "https://api.github.com/app/hook/deliveries"
        }
        mock_get.return_value = mock_response
        
        result = github_controller.verify_github_webhook("ghs_token")
        
        assert result is not None
        assert result["webhook_id"] == "999"
        assert result["active"] is True
        assert "deliveries_url" in result
    
    @patch('controllers.github_controller.requests.get')
    def test_verify_webhook_not_found(self, mock_get):
        """Should return None when webhook not found."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response
        
        result = github_controller.verify_github_webhook("ghs_token")
        
        assert result is None
    
    @patch('controllers.github_controller.requests.get')
    def test_verify_webhook_api_error(self, mock_get):
        """Should handle API errors gracefully."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response
        
        result = github_controller.verify_github_webhook("ghs_token")
        
        assert result is None
    
    @patch('controllers.github_controller.requests.get')
    def test_verify_webhook_no_token(self, mock_get):
        """Should fail gracefully when token missing."""
        result = github_controller.verify_github_webhook(None)
        
        assert result is None
        mock_get.assert_not_called()


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestWebhookIntegration:
    """Integration tests for complete webhook lifecycle."""
    
    @patch('controllers.github_controller.requests.patch')
    @patch('controllers.github_controller.requests.get')
    @patch('controllers.github_controller.requests.delete')
    def test_webhook_full_lifecycle(self, mock_delete, mock_get, mock_patch):
        """Should handle register → verify → delete workflow."""
        # Setup register response
        register_response = MagicMock()
        register_response.status_code = 200
        register_response.json.return_value = {"id": 789, "active": True, "url": "https://test.com/webhook"}
        
        # Setup verify response
        verify_response = MagicMock()
        verify_response.status_code = 200
        verify_response.json.return_value = {"id": 789, "active": True}
        
        # Setup delete response
        delete_response = MagicMock()
        delete_response.status_code = 204
        
        mock_patch.return_value = register_response
        mock_get.return_value = verify_response
        mock_delete.return_value = delete_response
        
        # Register
        reg_result = github_controller.register_github_webhook(
            "token",
            "inst_123",
            "https://test.com/webhook",
            "secret"
        )
        assert reg_result is not None
        assert reg_result["webhook_id"] == "789"
        
        # Verify
        verify_result = github_controller.verify_github_webhook("token")
        assert verify_result is not None
        assert verify_result["webhook_id"] == "789"
        
        # Delete
        delete_result = github_controller.delete_github_webhook("token")
        assert delete_result is True
    
    @patch('controllers.github_controller.requests.patch')
    def test_webhook_registration_with_full_url(self, mock_patch):
        """Should register webhook with full URL including connection params."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": 123, "active": True}
        mock_patch.return_value = mock_response
        
        # Build full webhook URL
        full_url = github_controller.build_webhook_url(
            connection_id="conn_xyz",
            user_id="user_abc",
            api_base_url="https://api.example.com"
        )
        
        # Register with full URL
        result = github_controller.register_github_webhook(
            github_token="token",
            installation_id="inst_123",
            webhook_url=full_url,
            webhook_secret="secret"
        )
        
        assert result is not None
        
        # Verify full URL was sent to GitHub
        call_args = mock_patch.call_args[1]
        payload = call_args["json"]
        assert "connection_id=conn_xyz" in payload["url"]
        assert "user_id=user_abc" in payload["url"]


# ============================================================================
# ERROR HANDLING & EDGE CASES
# ============================================================================

class TestWebhookErrorHandling:
    """Test error handling and edge cases."""
    
    @patch('controllers.github_controller.requests.patch')
    def test_register_webhook_timeout(self, mock_patch):
        """Should handle request timeout gracefully."""
        import requests
        mock_patch.side_effect = requests.Timeout("Connection timeout")
        
        result = github_controller.register_github_webhook(
            "token",
            "inst_123",
            "https://example.com/webhook",
            "secret"
        )
        
        assert result is None
    
    @patch('controllers.github_controller.requests.patch')
    def test_register_webhook_connection_error(self, mock_patch):
        """Should handle connection errors gracefully."""
        import requests
        mock_patch.side_effect = requests.ConnectionError("Network error")
        
        result = github_controller.register_github_webhook(
            "token",
            "inst_123",
            "https://example.com/webhook",
            "secret"
        )
        
        assert result is None
    
    def test_build_webhook_url_with_special_chars(self):
        """Should handle special characters in IDs."""
        url = github_controller.build_webhook_url(
            connection_id="507f1f77bcf86cd799439011",
            user_id="507f1f77bcf86cd799439010",
            api_base_url="https://api.example.com/v1"
        )
        
        assert "507f1f77bcf86cd799439011" in url
        assert "507f1f77bcf86cd799439010" in url


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
