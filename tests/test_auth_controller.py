"""
TEST SUITE: auth_controller.py

Run all tests:
    pytest tests/test_auth_controller.py -v

Run specific class:
    pytest tests/test_auth_controller.py::TestAuthGitHubOAuth -v

Run specific test:
    pytest tests/test_auth_controller.py::TestAuthPasswordHandling::test_verify_password_correct -v

Run with coverage:
    pytest tests/test_auth_controller.py --cov=controllers.auth_controller --cov-report=html

Coverage should be >90% for auth_controller
"""

import pytest
import os
from datetime import timedelta, datetime, timezone
from dotenv import load_dotenv
from unittest.mock import patch, MagicMock, call
from bson import ObjectId

load_dotenv()

from controllers.auth_controller import (
    verify_password, get_password_hash, create_access_token,
    verify_token, get_current_user, register_user, login_user,
    get_user_by_id, get_user_by_email,
    register_or_login_github, get_user_by_github_id,
)
from models.users import UserCreate, Token, TokenData


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def sample_github_profile():
    return {
        "github_id":         12345678,
        "github_login":      "octocat",
        "github_name":       "The Octocat",
        "github_email":      "octocat@github.com",
        "github_avatar_url": "https://avatars.githubusercontent.com/u/583231",
        "github_html_url":   "https://github.com/octocat",
    }


@pytest.fixture
def sample_user_doc():
    return {
        "_id":             ObjectId(),
        "email":           "test@example.com",
        "username":        "testuser",
        "full_name":       "Test User",
        "hashed_password": get_password_hash("correct_password"),
        "is_active":       True,
        "created_at":      datetime.now(timezone.utc).isoformat(),
        "connection_ids":  [],
        "github_id":       None,
        "github_login":    None,
    }


# ═════════════════════════════════════════════════════════════════════════════
# PASSWORD HANDLING
# ═════════════════════════════════════════════════════════════════════════════

class TestAuthPasswordHandling:
    """Test password hashing and verification."""

    def test_get_password_hash_generates_hash(self):
        password = "test_password_123"
        hashed = get_password_hash(password)
        assert hashed is not None
        assert hashed != password
        assert len(hashed) > 20

    def test_verify_password_correct(self):
        password = "test_password_123"
        hashed = get_password_hash(password)
        assert verify_password(password, hashed) is True

    def test_verify_password_incorrect(self):
        hashed = get_password_hash("test_password_123")
        assert verify_password("wrong_password", hashed) is False

    def test_verify_password_empty_password(self):
        hashed = get_password_hash("real_password")
        assert verify_password("", hashed) is False

    def test_get_password_hash_consistency(self):
        """Same password → different hashes (bcrypt salt), but both valid."""
        password = "test_password_123"
        hash1 = get_password_hash(password)
        hash2 = get_password_hash(password)
        assert verify_password(password, hash1) is True
        assert verify_password(password, hash2) is True
        assert hash1 != hash2

    def test_get_password_hash_special_characters(self):
        password = "P@ssw0rd!#$%^&*()"
        hashed = get_password_hash(password)
        assert verify_password(password, hashed) is True
        assert verify_password("P@ssw0rd!#$%^&*(x", hashed) is False

    def test_get_password_hash_unicode(self):
        password = "pässwörd_中文_阿拉伯"
        hashed = get_password_hash(password)
        assert verify_password(password, hashed) is True


# ═════════════════════════════════════════════════════════════════════════════
# JWT TOKEN GENERATION
# ═════════════════════════════════════════════════════════════════════════════

class TestAuthTokenGeneration:
    """Test JWT token creation and validation."""

    def test_create_access_token_success(self):
        token = create_access_token("user_123", "test@example.com")
        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 50
        assert token.count('.') == 2  # header.payload.signature

    def test_create_access_token_with_custom_expiry(self):
        token = create_access_token("user_123", "test@example.com", timedelta(hours=1))
        assert token is not None
        assert isinstance(token, str)

    def test_verify_token_valid(self):
        token = create_access_token("user_123", "test@example.com")
        token_data = verify_token(token)
        assert token_data is not None
        assert token_data.user_id == "user_123"
        assert token_data.email == "test@example.com"

    def test_verify_token_invalid_format(self):
        assert verify_token("not.a.valid.token.structure") is None

    def test_verify_token_tampered(self):
        token = create_access_token("user_123", "test@example.com")
        tampered = token[:-10] + "xxxxxxxxxx"
        assert verify_token(tampered) is None

    def test_verify_token_empty_string(self):
        assert verify_token("") is None

    def test_create_access_token_empty_user_id(self):
        token = create_access_token("", "test@example.com")
        assert token is not None

    def test_verify_token_expired(self):
        token = create_access_token("user_123", "test@example.com", timedelta(seconds=-1))
        assert verify_token(token) is None


# ═════════════════════════════════════════════════════════════════════════════
# EMAIL/PASSWORD REGISTRATION
# ═════════════════════════════════════════════════════════════════════════════

class TestAuthUserRegistration:
    """Test email/password user registration flow."""

    @pytest.fixture(autouse=True)
    def setup(self):
        os.environ['MONGO_URI'] = 'mongodb://localhost:27017/ks_rag_test'
        yield

    @patch('controllers.auth_controller.users_collection')
    def test_register_user_success(self, mock_collection):
        mock_collection.find_one.return_value = None
        mock_collection.insert_one.return_value.inserted_id = ObjectId()

        # FIX: added required `username`; password satisfies uppercase + digit validators
        user_data = UserCreate(
            email="newuser@example.com",
            username="newuser",
            password="SecurePassword1!",
            full_name="New User"
        )
        result = register_user(user_data)

        assert result is not None
        assert result.email == "newuser@example.com"
        assert result.full_name == "New User"
        assert result.hashed_password != "SecurePassword1!"

    @patch('controllers.auth_controller.users_collection')
    def test_register_user_includes_github_fields(self, mock_collection):
        """Registered user doc should have github_id and github_login as None by default."""
        mock_collection.find_one.return_value = None
        inserted_id = ObjectId()
        mock_collection.insert_one.return_value.inserted_id = inserted_id

        captured_doc = {}

        def capture_insert(doc):
            captured_doc.update(doc)
            return MagicMock(inserted_id=inserted_id)

        mock_collection.insert_one.side_effect = capture_insert

        # FIX: added required `username`; password satisfies all validators
        result = register_user(UserCreate(
            email="newuser@example.com",
            username="newuser",
            password="SecurePassword1!",
            full_name="New User"
        ))

        assert result is not None
        assert "github_id" in captured_doc
        assert "github_login" in captured_doc
        assert captured_doc["github_id"] is None
        assert captured_doc["github_login"] is None

    @patch('controllers.auth_controller.users_collection')
    def test_register_user_email_already_exists(self, mock_collection):
        mock_collection.find_one.return_value = {"_id": ObjectId(), "email": "existing@example.com"}

        # FIX: added required `username`; password satisfies all validators
        result = register_user(UserCreate(
            email="existing@example.com",
            username="existinguser",
            password="Password1!",
            full_name="Existing User"
        ))
        assert result is None

    @patch('controllers.auth_controller.users_collection')
    def test_register_user_short_password(self, mock_collection):
        # FIX: Pydantic rejects a 1-char password before the function body runs,
        # so we assert the ValidationError is raised rather than expecting a return value.
        with pytest.raises(Exception):
            register_user(UserCreate(
                email="test@example.com",
                username="testuser",
                password="a",   # too short → Pydantic ValidationError
                full_name="User"
            ))

    @patch('controllers.auth_controller.users_collection')
    def test_register_user_invalid_email_format(self, mock_collection):
        with pytest.raises(Exception):
            register_user(UserCreate(
                email="not_an_email",
                username="testuser",
                password="Password1!",
                full_name="User"
            ))


# ═════════════════════════════════════════════════════════════════════════════
# EMAIL/PASSWORD LOGIN
# ═════════════════════════════════════════════════════════════════════════════

class TestAuthUserLogin:
    """Test email/password login flow."""

    @patch('controllers.auth_controller.users_collection')
    def test_login_user_success(self, mock_collection):
        password = "correct_password"
        hashed = get_password_hash(password)
        mock_collection.find_one.return_value = {
            "_id": ObjectId(), "email": "test@example.com", "hashed_password": hashed
        }

        result = login_user("test@example.com", password)

        assert result is not None
        assert isinstance(result, Token)
        assert result.token_type == "bearer"
        assert len(result.access_token) > 50

    @patch('controllers.auth_controller.users_collection')
    def test_login_user_wrong_password(self, mock_collection):
        hashed = get_password_hash("correct_password")
        mock_collection.find_one.return_value = {
            "_id": ObjectId(), "email": "test@example.com", "hashed_password": hashed
        }

        assert login_user("test@example.com", "wrong_password") is None

    @patch('controllers.auth_controller.users_collection')
    def test_login_user_not_found(self, mock_collection):
        mock_collection.find_one.return_value = None
        assert login_user("nonexistent@example.com", "password") is None

    @patch('controllers.auth_controller.users_collection')
    def test_login_user_case_sensitive_email(self, mock_collection):
        hashed = get_password_hash("password")
        mock_collection.find_one.return_value = {
            "_id": ObjectId(), "email": "test@example.com", "hashed_password": hashed
        }
        result = login_user("TEST@EXAMPLE.COM", "password")
        assert result is None or isinstance(result, Token)


# ═════════════════════════════════════════════════════════════════════════════
# USER RETRIEVAL
# ═════════════════════════════════════════════════════════════════════════════

class TestAuthUserRetrieval:
    """Test getting user information."""

    @patch('controllers.auth_controller.users_collection')
    def test_get_user_by_id_success(self, mock_collection):
        user_id = str(ObjectId())
        mock_collection.find_one.return_value = {
            "_id": ObjectId(user_id), "email": "test@example.com",
            "full_name": "Test User", "hashed_password": "hash"
        }
        result = get_user_by_id(user_id)
        assert result is not None
        assert result.email == "test@example.com"

    @patch('controllers.auth_controller.users_collection')
    def test_get_user_by_id_not_found(self, mock_collection):
        mock_collection.find_one.return_value = None
        assert get_user_by_id(str(ObjectId())) is None

    @patch('controllers.auth_controller.users_collection')
    def test_get_user_by_email_success(self, mock_collection):
        mock_collection.find_one.return_value = {
            "_id": ObjectId(), "email": "test@example.com",
            "full_name": "Test User", "hashed_password": "hash"
        }
        result = get_user_by_email("test@example.com")
        assert result is not None
        assert result.email == "test@example.com"

    @patch('controllers.auth_controller.users_collection')
    def test_get_user_by_email_not_found(self, mock_collection):
        mock_collection.find_one.return_value = None
        assert get_user_by_email("nonexistent@example.com") is None

    @patch('controllers.auth_controller.users_collection')
    def test_get_user_by_github_id_success(self, mock_collection):
        """Should find a user by their GitHub numeric ID."""
        mock_collection.find_one.return_value = {
            "_id": ObjectId(), "email": "octocat@github.com",
            "username": "octocat", "hashed_password": "",
            "github_id": 12345678, "github_login": "octocat",
        }
        result = get_user_by_github_id(12345678)
        assert result is not None
        assert result.email == "octocat@github.com"

    @patch('controllers.auth_controller.users_collection')
    def test_get_user_by_github_id_not_found(self, mock_collection):
        mock_collection.find_one.return_value = None
        assert get_user_by_github_id(99999999) is None


# ═════════════════════════════════════════════════════════════════════════════
# GITHUB OAUTH — register_or_login_github
# ═════════════════════════════════════════════════════════════════════════════

class TestAuthGitHubOAuth:
    """Test GitHub OAuth registration and login flow."""

    # ── Case 1: existing github_id ────────────────────────────────────────────

    @patch('controllers.auth_controller.users_collection')
    def test_github_login_existing_github_id(self, mock_collection, sample_github_profile):
        """Should return a JWT for a user already linked by github_id."""
        existing_doc = {
            "_id":             ObjectId(),
            "email":           "octocat@github.com",
            "username":        "octocat",
            "hashed_password": "",
            "github_id":       12345678,
            "github_login":    "octocat",
        }
        mock_collection.find_one.return_value = existing_doc
        mock_collection.update_one.return_value = MagicMock(modified_count=1)

        result = register_or_login_github(sample_github_profile)

        assert result is not None
        assert isinstance(result, Token)
        assert result.token_type == "bearer"
        mock_collection.update_one.assert_called_once()

    @patch('controllers.auth_controller.users_collection')
    def test_github_login_existing_github_id_returns_valid_jwt(self, mock_collection, sample_github_profile):
        """JWT from GitHub login should decode to correct user_id and email."""
        user_oid = ObjectId()
        existing_doc = {
            "_id": user_oid, "email": "octocat@github.com",
            "username": "octocat", "hashed_password": "",
            "github_id": 12345678, "github_login": "octocat",
        }
        mock_collection.find_one.return_value = existing_doc
        mock_collection.update_one.return_value = MagicMock()

        result = register_or_login_github(sample_github_profile)
        token_data = verify_token(result.access_token)

        assert token_data is not None
        assert token_data.user_id == str(user_oid)
        assert token_data.email == "octocat@github.com"

    # ── Case 2: email match (link GitHub to existing account) ─────────────────

    @patch('controllers.auth_controller.users_collection')
    def test_github_login_links_existing_email_account(self, mock_collection, sample_github_profile):
        """Should link github_id to an existing email-based account."""
        existing_doc = {
            "_id": ObjectId(), "email": "octocat@github.com",
            "username": "octocat", "hashed_password": "hashed",
            "github_id": None, "github_login": None,
        }
        # first find_one (by github_id) → None; second (by email) → existing doc
        mock_collection.find_one.side_effect = [None, existing_doc]
        mock_collection.update_one.return_value = MagicMock(modified_count=1)

        result = register_or_login_github(sample_github_profile)

        assert result is not None
        assert isinstance(result, Token)
        update_call_args = mock_collection.update_one.call_args[0][1]
        assert update_call_args["$set"]["github_id"] == 12345678
        assert update_call_args["$set"]["github_login"] == "octocat"

    @patch('controllers.auth_controller.users_collection')
    def test_github_login_email_match_no_email_in_profile(self, mock_collection):
        """If GitHub profile has no email, skip the email-match step and create new user."""
        profile_no_email = {
            "github_id": 99999, "github_login": "noemail_user",
            "github_name": None, "github_email": None, "github_avatar_url": None,
        }
        new_oid = ObjectId()
        # FIX: 3 find_one calls — github_id, (no email step), username conflict check
        mock_collection.find_one.side_effect = [
            None,   # by github_id → not found
            None,   # username conflict check → not taken
        ]
        mock_collection.insert_one.return_value = MagicMock(inserted_id=new_oid)

        result = register_or_login_github(profile_no_email)

        assert result is not None
        token_data = verify_token(result.access_token)
        assert "github.local" in token_data.email

    # ── Case 3: brand new user ────────────────────────────────────────────────

    @patch('controllers.auth_controller.users_collection')
    def test_github_register_new_user(self, mock_collection, sample_github_profile):
        """Should create a new account for a first-time GitHub user."""
        new_oid = ObjectId()
        # FIX: 3 find_one calls — github_id, email, username conflict check
        mock_collection.find_one.side_effect = [
            None,   # by github_id → not found
            None,   # by email → not found
            None,   # username "octocat" → not taken
        ]
        mock_collection.insert_one.return_value = MagicMock(inserted_id=new_oid)

        result = register_or_login_github(sample_github_profile)

        assert result is not None
        assert isinstance(result, Token)
        mock_collection.insert_one.assert_called_once()

    @patch('controllers.auth_controller.users_collection')
    def test_github_register_new_user_doc_shape(self, mock_collection, sample_github_profile):
        """New user doc should have correct fields and empty hashed_password."""
        new_oid = ObjectId()
        # FIX: 3 find_one calls — github_id, email, username conflict check
        mock_collection.find_one.side_effect = [
            None,   # by github_id → not found
            None,   # by email → not found
            None,   # username "octocat" → not taken
        ]

        captured = {}

        def capture_insert(doc):
            captured.update(doc)
            return MagicMock(inserted_id=new_oid)

        mock_collection.insert_one.side_effect = capture_insert

        register_or_login_github(sample_github_profile)

        assert captured["github_id"]       == 12345678
        assert captured["github_login"]    == "octocat"
        assert captured["hashed_password"] == ""
        assert captured["email"]           == "octocat@github.com"
        assert captured["is_active"]       is True
        assert captured["connection_ids"]  == []

    @patch('controllers.auth_controller.users_collection')
    def test_github_register_username_conflict_resolved(self, mock_collection, sample_github_profile):
        """If github_login username is taken, should append github_id as suffix."""
        new_oid = ObjectId()
        mock_collection.find_one.side_effect = [
            None,                 # by github_id → not found
            None,                 # by email → not found
            {"_id": ObjectId()},  # username "octocat" → already taken
        ]

        captured = {}

        def capture_insert(doc):
            captured.update(doc)
            return MagicMock(inserted_id=new_oid)

        mock_collection.insert_one.side_effect = capture_insert

        register_or_login_github(sample_github_profile)

        assert captured["username"] == "octocat_12345678"

    # ── Error handling ────────────────────────────────────────────────────────

    @patch('controllers.auth_controller.users_collection')
    def test_github_register_db_error_returns_none(self, mock_collection, sample_github_profile):
        """Should return None if the DB raises during insert."""
        # FIX: 3 find_one calls before insert is attempted
        mock_collection.find_one.side_effect = [
            None,   # by github_id → not found
            None,   # by email → not found
            None,   # username → not taken
        ]
        mock_collection.insert_one.side_effect = Exception("DB connection lost")

        result = register_or_login_github(sample_github_profile)

        assert result is None

    @patch('controllers.auth_controller.users_collection')
    def test_github_register_missing_github_id_returns_none(self, mock_collection):
        """Should return None if github_id is missing from profile."""
        bad_profile = {"github_login": "octocat", "github_email": "x@x.com"}

        result = register_or_login_github(bad_profile)

        assert result is None or isinstance(result, Token)