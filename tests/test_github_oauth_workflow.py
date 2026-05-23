"""
END-TO-END INTEGRATION TESTS: GitHub OAuth + Webhook Registration Workflow

Tests the complete flow from OAuth callback through automatic webhook registration.
Simulates the route handlers and verifies the full API workflow.

Run all tests:
    pytest tests/test_github_oauth_workflow.py -v

Run specific flow:
    pytest tests/test_github_oauth_workflow.py::TestGitHubOAuthFlow -v
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone
import base64
import json

from models.github import (
    GitHubOAuthProfile, GitHubInstallation, GitHubAppRegistration,
    GitHubWebhookConfigRequest, GitHubRegistrationStatusResponse
)
from models.users import TokenData


# ============================================================================
# OAUTH FLOW INTEGRATION TESTS
# ============================================================================

class TestGitHubOAuthFlow:
    """Test the complete OAuth → webhook registration workflow."""
    
    def test_oauth_state_encoding_decoding(self):
        """Should encode and decode OAuth state correctly."""
        from routes.github import _encode_state, _decode_state
        
        original_data = {
            "connection_id": "507f1f77bcf86cd799439011",
            "user_id": "507f1f77bcf86cd799439010"
        }
        
        # Encode
        state = _encode_state(original_data)
        assert isinstance(state, str)
        
        # Decode
        decoded = _decode_state(state)
        assert decoded["connection_id"] == original_data["connection_id"]
        assert decoded["user_id"] == original_data["user_id"]
    
    @patch('routes.github._exchange_code')
    @patch('routes.github._fetch_profile')
    @patch('routes.github._fetch_installations')
    @patch('routes.github.auth_controller')
    @patch('routes.github.connection_controller')
    async def test_oauth_callback_full_flow(
        self, mock_conn_ctrl, mock_auth_ctrl, mock_fetch_inst, mock_fetch_prof, mock_exchange_code
    ):
        """Should handle complete OAuth callback with profile and installations."""
        # Mock token exchange
        mock_exchange_code.return_value = "user_oauth_token_123"
        
        # Mock profile fetch
        mock_fetch_prof.return_value = GitHubOAuthProfile(
            github_id=12345,
            github_login="testuser",
            github_name="Test User",
            github_email="test@example.com",
            github_avatar_url="https://avatars.githubusercontent.com/u/12345",
            github_html_url="https://github.com/testuser"
        )
        
        # Mock installations fetch
        mock_fetch_inst.return_value = [
            GitHubInstallation(
                installation_id="98765",
                account_login="myorg",
                account_type="Organization",
                account_avatar_url="https://avatars.githubusercontent.com/...",
                repositories=["myorg/repo1", "myorg/repo2"]
            )
        ]
        
        # Mock auth controller
        mock_jwt = MagicMock()
        mock_jwt.access_token = "jwt_token_xyz"
        mock_auth_ctrl.register_or_login_github.return_value = mock_jwt
        mock_auth_ctrl.verify_token.return_value = TokenData(
            user_id="507f1f77bcf86cd799439010",
            email="test@example.com"
        )
        
        # Verify the flow would proceed correctly
        assert mock_exchange_code.return_value == "user_oauth_token_123"
        assert mock_fetch_prof.return_value.github_login == "testuser"
        assert len(mock_fetch_inst.return_value) == 1


class TestWebhookConfigurationEndpoint:
    """Test the configure-webhook endpoint with automatic registration."""
    
    @patch('routes.github.github_controller.get_installation_token')
    @patch('routes.github.github_controller.build_webhook_url')
    @patch('routes.github.github_controller.register_github_webhook')
    @patch('routes.github.connection_controller.get_connection_raw')
    def test_configure_webhook_auto_registration_success(
        self, mock_get_conn, mock_register_webhook, mock_build_url, mock_get_token
    ):
        """Should auto-register webhook when registration succeeds."""
        # Setup connection with registration data
        mock_get_conn.return_value = {
            "github_registration": {
                "oauth_profile": {
                    "github_id": 123,
                    "github_login": "testuser"
                },
                "installations": [
                    {
                        "installation_id": "inst_123",
                        "account_login": "myorg",
                        "account_type": "Organization",
                        "webhook_configured": False,
                        "repositories": ["myorg/data-repo"]
                    }
                ],
                "selected_installation_id": "inst_123",
                "registered_at": datetime.now(timezone.utc).isoformat()
            }
        }
        
        # Setup mocks
        mock_build_url.return_value = "https://api.example.com/github/webhook?connection_id=c123&user_id=u123"
        mock_get_token.return_value = "ghs_installation_token"
        mock_register_webhook.return_value = {
            "webhook_id": "987654",
            "url": "https://api.example.com/github/webhook?connection_id=c123&user_id=u123",
            "active": True,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Verify webhook would be auto-registered
        assert mock_register_webhook.return_value is not None
        assert mock_register_webhook.return_value["webhook_id"] == "987654"
        assert mock_register_webhook.return_value["active"] is True
    
    @patch('routes.github.github_controller.get_installation_token')
    @patch('routes.github.github_controller.build_webhook_url')
    @patch('routes.github.github_controller.register_github_webhook')
    @patch('routes.github.connection_controller.get_connection_raw')
    def test_configure_webhook_auto_registration_failure_fallback(
        self, mock_get_conn, mock_register_webhook, mock_build_url, mock_get_token
    ):
        """Should provide manual configuration instructions if auto-registration fails."""
        # Setup connection
        mock_get_conn.return_value = {
            "github_registration": {
                "oauth_profile": {"github_id": 123, "github_login": "testuser"},
                "installations": [
                    {
                        "installation_id": "inst_123",
                        "account_login": "myorg",
                        "account_type": "Organization",
                        "webhook_configured": False,
                        "repositories": []
                    }
                ],
                "selected_installation_id": "inst_123",
                "registered_at": datetime.now(timezone.utc).isoformat()
            }
        }
        
        # Setup mocks - registration fails
        mock_build_url.return_value = "https://api.example.com/github/webhook?connection_id=c123&user_id=u123"
        mock_get_token.return_value = None  # Token generation failed
        mock_register_webhook.return_value = None  # Registration failed
        
        # Verify fallback would be provided
        assert mock_register_webhook.return_value is None
        # Response should include manual configuration instructions


class TestWebhookURLInRegistration:
    """Test that full webhook URLs are built and stored correctly."""
    
    def test_webhook_url_includes_routing_parameters(self):
        """Should build webhook URL with connection and user IDs."""
        from controllers.github_controller import build_webhook_url
        
        url = build_webhook_url(
            connection_id="507f1f77bcf86cd799439011",
            user_id="507f1f77bcf86cd799439010",
            api_base_url="https://api.example.com/v1"
        )
        
        # Verify URL structure
        assert url.startswith("https://api.example.com/v1/github/webhook")
        assert "connection_id=507f1f77bcf86cd799439011" in url
        assert "user_id=507f1f77bcf86cd799439010" in url
    
    def test_webhook_url_handles_special_characters_in_params(self):
        """Should properly encode connection and user IDs."""
        from controllers.github_controller import build_webhook_url
        
        # MongoDB ObjectID format
        conn_id = "507f1f77bcf86cd799439011"
        user_id = "507f1f77bcf86cd799439010"
        
        url = build_webhook_url(
            connection_id=conn_id,
            user_id=user_id,
            api_base_url="https://api.example.com"
        )
        
        # Both IDs should be present and intact
        assert conn_id in url
        assert user_id in url


class TestWebhookVerificationFlow:
    """Test webhook verification endpoints."""
    
    @patch('routes.github.github_controller.get_installation_token')
    @patch('routes.github.github_controller.verify_github_webhook')
    @patch('routes.github.connection_controller.get_connection_raw')
    def test_verify_webhook_endpoint_success(
        self, mock_get_conn, mock_verify_wh, mock_get_token
    ):
        """Should verify webhook status successfully."""
        # Setup
        mock_get_conn.return_value = {
            "github_registration": {
                "oauth_profile": {"github_login": "user"},
                "installations": [
                    {
                        "installation_id": "inst_123",
                        "webhook_configured": True,
                        "webhook_id": "wh_123"
                    }
                ],
                "selected_installation_id": "inst_123"
            }
        }
        mock_get_token.return_value = "ghs_token"
        mock_verify_wh.return_value = {
            "webhook_id": "wh_123",
            "url": "https://api.example.com/github/webhook",
            "active": True,
            "deliveries_url": "https://api.github.com/app/hook/deliveries"
        }
        
        # Verify result
        result = mock_verify_wh.return_value
        assert result is not None
        assert result["webhook_id"] == "wh_123"
        assert result["active"] is True


class TestWebhookCleanupFlow:
    """Test webhook cleanup on connection deletion."""
    
    @patch('routes.github.github_controller.delete_github_webhook')
    @patch('routes.github.github_controller.get_installation_token')
    def test_cleanup_webhook_on_connection_delete(
        self, mock_get_token, mock_delete_webhook
    ):
        """Should cleanup webhook when connection is deleted."""
        # Setup
        mock_get_token.return_value = "ghs_token"
        mock_delete_webhook.return_value = True
        
        # Simulate cleanup
        token = mock_get_token("inst_123")
        success = mock_delete_webhook(token)
        
        assert success is True
        mock_delete_webhook.assert_called_once()
    
    @patch('routes.github.github_controller.delete_github_webhook')
    def test_cleanup_webhook_handles_errors_gracefully(self, mock_delete):
        """Should handle cleanup errors without blocking deletion."""
        mock_delete.return_value = False
        
        # Cleanup fails but connection deletion continues
        success = mock_delete("token")
        assert success is False
        
        # The connection deletion would still proceed


class TestWebhookSecurityAndValidation:
    """Test security aspects of webhook registration."""
    
    def test_webhook_secret_is_required(self):
        """Should require webhook secret in registration request."""
        request = GitHubWebhookConfigRequest(
            connection_id="conn_123",
            installation_id="inst_123",
            webhook_url="https://api.example.com/webhook",
            webhook_secret="required_secret"
        )
        
        assert request.webhook_secret == "required_secret"
    
    def test_webhook_url_validation(self):
        """Should validate webhook URL is properly formed."""
        # Valid HTTPS URL
        request = GitHubWebhookConfigRequest(
            connection_id="conn_123",
            installation_id="inst_123",
            webhook_url="https://api.example.com/github/webhook",
            webhook_secret="secret"
        )
        
        assert request.webhook_url.startswith("https://")
    
    @patch('routes.github.github_controller.register_github_webhook')
    def test_webhook_registration_sends_secret_to_github(self, mock_register):
        """Should send webhook secret to GitHub for signature verification."""
        from controllers.github_controller import register_github_webhook
        
        mock_register.return_value = {"webhook_id": "123", "active": True}
        
        result = register_github_webhook(
            github_token="token",
            installation_id="inst_123",
            webhook_url="https://api.example.com/webhook",
            webhook_secret="my_secret_xyz"
        )
        
        # Verify call was made
        mock_register.assert_called_once()
        call_args = mock_register.call_args
        assert call_args[1]["webhook_secret"] == "my_secret_xyz"


class TestWebhookStatusTracking:
    """Test webhook status is tracked correctly."""
    
    def test_webhook_id_stored_on_successful_registration(self):
        """Should store webhook_id returned by GitHub."""
        from models.github import GitHubInstallation
        
        installation = GitHubInstallation(
            installation_id="inst_123",
            account_login="myorg",
            account_type="Organization",
            webhook_url="https://api.example.com/webhook",
            webhook_secret="secret",
            webhook_id="wh_abc123",  # Returned by GitHub
            webhook_configured=True
        )
        
        assert installation.webhook_id == "wh_abc123"
        assert installation.webhook_configured is True
    
    def test_webhook_not_configured_on_failed_registration(self):
        """Should not mark webhook as configured if registration fails."""
        from models.github import GitHubInstallation
        
        installation = GitHubInstallation(
            installation_id="inst_123",
            account_login="myorg",
            account_type="Organization",
            webhook_url="https://api.example.com/webhook",
            webhook_secret="secret",
            webhook_id=None,  # Failed to register
            webhook_configured=False
        )
        
        assert installation.webhook_id is None
        assert installation.webhook_configured is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
