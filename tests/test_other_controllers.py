"""
TEST SUITE: connection_controller.py + github_controller.py + chat_controller.py

Run all tests:
    pytest tests/test_other_controllers.py -v

Key functionalities to test:
1. Connection: CRUD operations for OpenMetadata + GitHub connections
2. GitHub: Webhook verification, diff parsing, signature validation  
3. Chat: Session management, message handling, followup detection
"""

import pytest
from unittest.mock import patch, MagicMock
from bson import ObjectId

from controllers.connection_controller import (
    create_connection, get_user_connections, get_connection_by_id,
    verify_openmetadata_connection, delete_connection
)
from controllers.github_controller import (
    verify_github_signature, parse_pr_diff
)
from controllers.chat_controller import (
    create_session, get_session, list_sessions,
    handle_query, update_session_title, delete_session
)


# ============================================================================
# CONNECTION CONTROLLER TESTS
# ============================================================================

class TestConnectionManagement:
    """Test OpenMetadata + GitHub connection management."""
    
    @patch('controllers.connection_controller.connections_collection')
    def test_create_connection_success(self, mock_collection):
        """Should create new connection."""
        mock_collection.find_one.return_value = None  # Email doesn't exist
        mock_collection.insert_one.return_value.inserted_id = ObjectId()
        
        result = create_connection(
            user_id="user_123",
            workspace_name="Production",
            openmetadata_url="http://localhost:8585",
            openmetadata_token="token_123",
            github_repo="myteam/data-repo"
        )
        
        assert result is not None
    
    @patch('controllers.connection_controller.connections_collection')
    def test_create_connection_duplicate(self, mock_collection):
        """Should not create duplicate connections for same workspace."""
        mock_collection.find_one.return_value = {"_id": ObjectId()}
        
        result = create_connection(
            user_id="user_123",
            workspace_name="Production",
            openmetadata_url="http://localhost:8585",
            openmetadata_token="token_123",
            github_repo="myteam/data-repo"
        )
        
        assert result is None
    
    @patch('controllers.connection_controller.connections_collection')
    def test_get_user_connections(self, mock_collection):
        """Should retrieve all connections for user."""
        mock_collection.find.return_value = [
            {
                "_id": ObjectId(),
                "user_id": "user_123",
                "workspace_name": "Production"
            }
        ]
        
        result = get_user_connections("user_123")
        
        assert isinstance(result, list)
        assert len(result) > 0
    
    @patch('controllers.connection_controller.connections_collection')
    def test_get_connection_by_id(self, mock_collection):
        """Should retrieve specific connection."""
        conn_id = ObjectId()
        mock_collection.find_one.return_value = {
            "_id": conn_id,
            "user_id": "user_123"
        }
        
        result = get_connection_by_id(str(conn_id), "user_123")
        
        assert result is not None
    
    @patch('controllers.connection_controller.requests.get')
    def test_verify_openmetadata_connection(self, mock_get):
        """Should verify OpenMetadata connection."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response
        
        result = verify_openmetadata_connection(
            "http://localhost:8585",
            "token_123"
        )
        
        assert result is True
    
    @patch('controllers.connection_controller.requests.get')
    def test_verify_openmetadata_connection_failed(self, mock_get):
        """Should return False when connection fails."""
        mock_get.side_effect = Exception("Connection failed")
        
        result = verify_openmetadata_connection(
            "http://invalid:8585",
            "bad_token"
        )
        
        assert result is False
    
    @patch('controllers.connection_controller.connections_collection')
    def test_delete_connection(self, mock_collection):
        """Should delete connection."""
        mock_collection.update_one.return_value.modified_count = 1
        
        result = delete_connection(str(ObjectId()), "user_123")
        
        assert result is True


# ============================================================================
# GITHUB CONTROLLER TESTS
# ============================================================================

class TestGitHubSignatureVerification:
    """Test GitHub webhook signature verification."""
    
    def test_verify_github_signature_valid(self):
        """Should verify valid GitHub signature."""
        payload = '{"action": "opened"}'
        secret = "test_secret"
        
        # Generate correct signature
        import hmac
        import hashlib
        expected_sig = "sha256=" + hmac.new(
            secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        
        result = verify_github_signature(payload, expected_sig, secret)
        
        assert result is True
    
    def test_verify_github_signature_invalid(self):
        """Should reject invalid signature."""
        payload = '{"action": "opened"}'
        signature = "sha256=invalid_signature_123"
        secret = "test_secret"
        
        result = verify_github_signature(payload, signature, secret)
        
        assert result is False
    
    def test_verify_github_signature_missing(self):
        """Should handle missing signature."""
        result = verify_github_signature('{}', "", "secret")
        
        assert result is False


class TestGitHubPRDiffParsing:
    """Test GitHub PR diff parsing."""
    
    @patch('controllers.github_controller.requests.get')
    def test_parse_pr_diff_sql_files(self, mock_get):
        """Should extract SQL file changes from PR."""
        mock_response = MagicMock()
        mock_response.text = """
--- a/models/staging/stg_users.sql
+++ b/models/staging/stg_users.sql
@@ -1,5 +1,5 @@
select
-  user_id,
+  customer_id,
"""
        mock_get.return_value = mock_response
        
        result = parse_pr_diff(
            repo="myteam/data-repo",
            pr_number=1,
            github_token="token_123"
        )
        
        assert result is not None
        assert isinstance(result, dict)
    
    @patch('controllers.github_controller.requests.get')
    def test_parse_pr_diff_no_relevant_files(self, mock_get):
        """Should ignore non-SQL files."""
        mock_response = MagicMock()
        mock_response.text = """
--- a/README.md
+++ b/README.md
@@ -1,5 +1,5 @@
# Updated docs
"""
        mock_get.return_value = mock_response
        
        result = parse_pr_diff(
            repo="myteam/data-repo",
            pr_number=1,
            github_token="token_123"
        )
        
        # Should filter out non-SQL files
        assert result is not None


# ============================================================================
# CHAT CONTROLLER TESTS
# ============================================================================

class TestChatSessionManagement:
    """Test chat session creation and management."""
    
    @patch('controllers.chat_controller.chat_sessions_collection')
    def test_create_session_success(self, mock_collection):
        """Should create new chat session."""
        mock_collection.insert_one.return_value.inserted_id = ObjectId()
        
        result = create_session(
            user_id="user_123",
            connection_id="conn_456",
            title="Orders Issue"
        )
        
        assert result is not None
        assert isinstance(result, str)
    
    @patch('controllers.chat_controller.chat_sessions_collection')
    def test_get_session_success(self, mock_collection):
        """Should retrieve chat session."""
        session_id = ObjectId()
        mock_collection.find_one.return_value = {
            "_id": session_id,
            "user_id": "user_123",
            "title": "Orders Issue",
            "messages": []
        }
        
        result = get_session(str(session_id), "user_123")
        
        assert result is not None
        assert result["title"] == "Orders Issue"
    
    @patch('controllers.chat_controller.chat_sessions_collection')
    def test_get_session_unauthorized(self, mock_collection):
        """Should reject access to other user's session."""
        mock_collection.find_one.return_value = {
            "_id": ObjectId(),
            "user_id": "other_user"
        }
        
        result = get_session(str(ObjectId()), "user_123")
        
        assert result is None
    
    @patch('controllers.chat_controller.chat_sessions_collection')
    def test_list_sessions(self, mock_collection):
        """Should list user's sessions."""
        mock_collection.find.return_value = [
            {
                "_id": ObjectId(),
                "user_id": "user_123",
                "title": "Session 1"
            },
            {
                "_id": ObjectId(),
                "user_id": "user_123",
                "title": "Session 2"
            }
        ]
        
        result = list_sessions("user_123", limit=20)
        
        assert isinstance(result, list)
        assert len(result) == 2
    
    @patch('controllers.chat_controller.chat_sessions_collection')
    def test_update_session_title(self, mock_collection):
        """Should update session title."""
        mock_collection.update_one.return_value.modified_count = 1
        
        result = update_session_title(
            str(ObjectId()),
            "user_123",
            "New Title"
        )
        
        assert result is True
    
    @patch('controllers.chat_controller.chat_sessions_collection')
    def test_delete_session(self, mock_collection):
        """Should delete session."""
        mock_collection.delete_one.return_value.deleted_count = 1
        
        result = delete_session(str(ObjectId()), "user_123")
        
        assert result is True


class TestChatQueryHandling:
    """Test chat query processing."""
    
    @patch('controllers.chat_controller.investigation_controller.create_investigation')
    @patch('controllers.chat_controller.chat_sessions_collection')
    def test_handle_query_new_investigation(self, mock_sessions, mock_inv):
        """Should create investigation for new queries."""
        mock_inv.return_value = "inv_123"
        mock_sessions.find_one.return_value = {
            "_id": ObjectId(),
            "messages": []
        }
        mock_sessions.update_one.return_value.modified_count = 1
        
        result = handle_query(
            session_id=str(ObjectId()),
            user_id="user_123",
            query_text="Why is this failing?"
        )
        
        assert result is not None
    
    @patch('controllers.chat_controller.chat_sessions_collection')
    def test_handle_query_followup_detection(self, mock_sessions):
        """Should detect when query is followup to existing investigation."""
        session_id = str(ObjectId())
        mock_sessions.find_one.return_value = {
            "_id": ObjectId(session_id),
            "investigation_id": "inv_123",
            "messages": [
                {
                    "role": "assistant",
                    "content": "Column was renamed"
                }
            ]
        }
        
        result = handle_query(
            session_id=session_id,
            user_id="user_123",
            query_text="What about downstream assets?"
        )
        
        assert result is not None
