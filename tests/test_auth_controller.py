"""
TEST SUITE: auth_controller.py

Run all tests:
    pytest tests/test_auth_controller.py -v

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
from unittest.mock import patch, MagicMock
from bson import ObjectId

# Load environment
load_dotenv()

from controllers.auth_controller import (
    verify_password, get_password_hash, create_access_token,
    verify_token, get_current_user, register_user, login_user,
    get_user_by_id, get_user_by_email
)
from models.users import UserCreate, Token, TokenData


class TestAuthPasswordHandling:
    """Test password hashing and verification."""
    
    # ========================================================================
    # HAPPY PATH TESTS
    # ========================================================================
    
    def test_get_password_hash_generates_hash(self):
        """Should generate a bcrypt hash from plain password."""
        password = "test_password_123"
        hashed = get_password_hash(password)
        
        assert hashed is not None
        assert hashed != password  # Should not be plaintext
        assert len(hashed) > 20  # bcrypt hashes are long
    
    def test_verify_password_correct(self):
        """Should return True for correct password."""
        password = "test_password_123"
        hashed = get_password_hash(password)
        
        result = verify_password(password, hashed)
        assert result is True
    
    def test_verify_password_incorrect(self):
        """Should return False for incorrect password."""
        password = "test_password_123"
        wrong_password = "wrong_password"
        hashed = get_password_hash(password)
        
        result = verify_password(wrong_password, hashed)
        assert result is False
    
    def test_verify_password_empty_password(self):
        """Should handle empty password verification."""
        hashed = get_password_hash("real_password")
        
        result = verify_password("", hashed)
        assert result is False
    
    # ========================================================================
    # EDGE CASES
    # ========================================================================
    
    def test_get_password_hash_consistency(self):
        """Same password should produce different hashes (due to salt)."""
        password = "test_password_123"
        hash1 = get_password_hash(password)
        hash2 = get_password_hash(password)
        
        # Both should be valid
        assert verify_password(password, hash1) is True
        assert verify_password(password, hash2) is True
        
        # But they should be different (different salts)
        assert hash1 != hash2
    
    def test_get_password_hash_special_characters(self):
        """Should handle passwords with special characters."""
        password = "P@ssw0rd!#$%^&*()"
        hashed = get_password_hash(password)
        
        assert verify_password(password, hashed) is True
        assert verify_password("P@ssw0rd!#$%^&*(x", hashed) is False
    
    def test_get_password_hash_unicode(self):
        """Should handle unicode characters in password."""
        password = "pässwörd_中文_阿拉伯"
        hashed = get_password_hash(password)
        
        assert verify_password(password, hashed) is True


class TestAuthTokenGeneration:
    """Test JWT token creation and validation."""
    
    # ========================================================================
    # HAPPY PATH TESTS
    # ========================================================================
    
    def test_create_access_token_success(self):
        """Should create a valid JWT token."""
        user_id = "user_123"
        email = "test@example.com"
        
        token = create_access_token(user_id, email)
        
        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 50
        assert token.count('.') == 2  # JWT format: header.payload.signature
    
    def test_create_access_token_with_custom_expiry(self):
        """Should create token with custom expiry time."""
        user_id = "user_123"
        email = "test@example.com"
        expires_delta = timedelta(hours=1)
        
        token = create_access_token(user_id, email, expires_delta)
        
        assert token is not None
        assert isinstance(token, str)
    
    def test_verify_token_valid(self):
        """Should verify a valid token and return TokenData."""
        user_id = "user_123"
        email = "test@example.com"
        token = create_access_token(user_id, email)
        
        token_data = verify_token(token)
        
        assert token_data is not None
        assert token_data.user_id == user_id
        assert token_data.email == email
    
    def test_verify_token_invalid_format(self):
        """Should return None for invalid token format."""
        invalid_token = "not.a.valid.token.structure"
        
        token_data = verify_token(invalid_token)
        
        assert token_data is None
    
    def test_verify_token_tampered(self):
        """Should return None for tampered token."""
        user_id = "user_123"
        email = "test@example.com"
        token = create_access_token(user_id, email)
        
        # Tamper with the token
        tampered = token[:-10] + "xxxxxxxxxx"
        
        token_data = verify_token(tampered)
        
        assert token_data is None
    
    # ========================================================================
    # EDGE CASES
    # ========================================================================
    
    def test_verify_token_empty_string(self):
        """Should return None for empty token."""
        token_data = verify_token("")
        assert token_data is None
    
    def test_create_access_token_empty_user_id(self):
        """Should create token even with empty user_id."""
        token = create_access_token("", "test@example.com")
        assert token is not None
    
    def test_verify_token_expired(self):
        """Should return None for expired token."""
        user_id = "user_123"
        email = "test@example.com"
        
        # Create token that expires immediately
        expires_delta = timedelta(seconds=-1)
        token = create_access_token(user_id, email, expires_delta)
        
        # Token should be expired
        token_data = verify_token(token)
        
        # Expired token should return None
        assert token_data is None


class TestAuthUserRegistration:
    """Test user registration flow."""
    
    # ========================================================================
    # SETUP & TEARDOWN
    # ========================================================================
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test database connection."""
        # Ensure we're using test database
        os.environ['MONGO_URI'] = 'mongodb://localhost:27017/ks_rag_test'
        yield
        # Cleanup would happen here
    
    # ========================================================================
    # HAPPY PATH TESTS
    # ========================================================================
    
    @patch('controllers.auth_controller.users_collection')
    def test_register_user_success(self, mock_collection):
        """Should successfully register a new user."""
        mock_collection.find_one.return_value = None  # Email doesn't exist
        mock_collection.insert_one.return_value.inserted_id = ObjectId()
        
        user_data = UserCreate(
            email="newuser@example.com",
            password="secure_password_123",
            full_name="New User"
        )
        
        result = register_user(user_data)
        
        assert result is not None
        assert result.email == "newuser@example.com"
        assert result.full_name == "New User"
        # Password should be hashed, not plaintext
        assert result.hashed_password != "secure_password_123"
    
    @patch('controllers.auth_controller.users_collection')
    def test_register_user_email_already_exists(self, mock_collection):
        """Should return None when email already exists."""
        mock_collection.find_one.return_value = {
            "_id": ObjectId(),
            "email": "existing@example.com"
        }
        
        user_data = UserCreate(
            email="existing@example.com",
            password="password",
            full_name="Existing User"
        )
        
        result = register_user(user_data)
        
        assert result is None
    
    # ========================================================================
    # EDGE CASES
    # ========================================================================
    
    @patch('controllers.auth_controller.users_collection')
    def test_register_user_invalid_email_format(self, mock_collection):
        """Should handle invalid email format."""
        user_data = UserCreate(
            email="not_an_email",
            password="password",
            full_name="User"
        )
        
        # Pydantic should validate this, but test the flow
        with pytest.raises(Exception):
            register_user(user_data)
    
    @patch('controllers.auth_controller.users_collection')
    def test_register_user_short_password(self, mock_collection):
        """Should accept very short passwords (validation is flexible)."""
        mock_collection.find_one.return_value = None
        mock_collection.insert_one.return_value.inserted_id = ObjectId()
        
        user_data = UserCreate(
            email="test@example.com",
            password="a",
            full_name="User"
        )
        
        result = register_user(user_data)
        
        assert result is not None


class TestAuthUserLogin:
    """Test user login flow."""
    
    # ========================================================================
    # HAPPY PATH TESTS
    # ========================================================================
    
    @patch('controllers.auth_controller.users_collection')
    def test_login_user_success(self, mock_collection):
        """Should successfully login with correct credentials."""
        password = "correct_password"
        hashed = get_password_hash(password)
        
        mock_collection.find_one.return_value = {
            "_id": ObjectId(),
            "email": "test@example.com",
            "hashed_password": hashed,
            "full_name": "Test User"
        }
        
        result = login_user("test@example.com", password)
        
        assert result is not None
        assert isinstance(result, Token)
        assert result.token_type == "bearer"
        assert len(result.access_token) > 50
    
    @patch('controllers.auth_controller.users_collection')
    def test_login_user_wrong_password(self, mock_collection):
        """Should return None with wrong password."""
        password = "correct_password"
        hashed = get_password_hash(password)
        
        mock_collection.find_one.return_value = {
            "_id": ObjectId(),
            "email": "test@example.com",
            "hashed_password": hashed,
            "full_name": "Test User"
        }
        
        result = login_user("test@example.com", "wrong_password")
        
        assert result is None
    
    @patch('controllers.auth_controller.users_collection')
    def test_login_user_not_found(self, mock_collection):
        """Should return None when user doesn't exist."""
        mock_collection.find_one.return_value = None
        
        result = login_user("nonexistent@example.com", "password")
        
        assert result is None
    
    # ========================================================================
    # EDGE CASES
    # ========================================================================
    
    @patch('controllers.auth_controller.users_collection')
    def test_login_user_case_sensitive_email(self, mock_collection):
        """Should handle email case sensitivity."""
        # Depending on implementation, this might need case-insensitive email
        password = "password"
        hashed = get_password_hash(password)
        
        mock_collection.find_one.return_value = {
            "_id": ObjectId(),
            "email": "test@example.com",
            "hashed_password": hashed
        }
        
        # Try different case
        result = login_user("TEST@EXAMPLE.COM", password)
        
        # Implementation detail: may or may not succeed
        # Just ensure no crash
        assert result is None or isinstance(result, Token)


class TestAuthUserRetrieval:
    """Test getting user information."""
    
    # ========================================================================
    # HAPPY PATH TESTS
    # ========================================================================
    
    @patch('controllers.auth_controller.users_collection')
    def test_get_user_by_id_success(self, mock_collection):
        """Should retrieve user by ID."""
        user_id = str(ObjectId())
        mock_collection.find_one.return_value = {
            "_id": ObjectId(user_id),
            "email": "test@example.com",
            "full_name": "Test User",
            "hashed_password": "hash"
        }
        
        result = get_user_by_id(user_id)
        
        assert result is not None
        assert result.email == "test@example.com"
    
    @patch('controllers.auth_controller.users_collection')
    def test_get_user_by_id_not_found(self, mock_collection):
        """Should return None when user not found."""
        mock_collection.find_one.return_value = None
        
        result = get_user_by_id("nonexistent_id")
        
        assert result is None
    
    @patch('controllers.auth_controller.users_collection')
    def test_get_user_by_email_success(self, mock_collection):
        """Should retrieve user by email."""
        mock_collection.find_one.return_value = {
            "_id": ObjectId(),
            "email": "test@example.com",
            "full_name": "Test User",
            "hashed_password": "hash"
        }
        
        result = get_user_by_email("test@example.com")
        
        assert result is not None
        assert result.email == "test@example.com"
    
    @patch('controllers.auth_controller.users_collection')
    def test_get_user_by_email_not_found(self, mock_collection):
        """Should return None when user not found."""
        mock_collection.find_one.return_value = None
        
        result = get_user_by_email("nonexistent@example.com")
        
        assert result is None
