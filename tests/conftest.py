"""
Pytest configuration and shared fixtures.

This file provides:
1. Common test fixtures (mock database, mock APIs)
2. Test database configuration
3. Environment setup for all tests
"""

import pytest
import os
from unittest.mock import MagicMock, patch

# Set test environment
os.environ['DEBUG'] = 'true'
os.environ['MONGO_URI'] = 'mongodb://localhost:27017/ks_rag_test'


@pytest.fixture
def mock_mongodb():
    """Provide mock MongoDB connection for tests."""
    with patch('controllers.auth_controller.MongoClient') as mock_client:
        mock_db = MagicMock()
        mock_client.return_value = mock_db
        yield mock_db


@pytest.fixture
def mock_openai_api():
    """Provide mock OpenAI API for tests."""
    with patch('controllers.investigation_controller.requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"root_cause": "test", "confidence": 0.95}'
                    }
                }
            ]
        }
        mock_post.return_value = mock_response
        yield mock_post


@pytest.fixture
def mock_openmetadata_api():
    """Provide mock OpenMetadata API for tests."""
    with patch('controllers.lineage_controller.requests.get') as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "entity": {
                "id": "snowflake.prod.test_table",
                "name": "test_table"
            },
            "upstreamEdges": []
        }
        mock_get.return_value = mock_response
        yield mock_get


@pytest.fixture
def sample_lineage_nodes():
    """Provide sample lineage nodes for tests."""
    from models.lineage import LineageNode
    
    return [
        LineageNode(
            id="raw.data",
            name="raw.data",
            schema={"id": {"type": "INT"}, "value": {"type": "FLOAT"}}
        ),
        LineageNode(
            id="stg.data",
            name="stg.data",
            schema={"id": {"type": "INT"}, "value": {"type": "FLOAT"}},
            is_break_point=False
        ),
        LineageNode(
            id="fact.data",
            name="fact.data",
            schema={"id": {"type": "INT"}},
            is_break_point=True
        )
    ]


@pytest.fixture
def sample_user_data():
    """Provide sample user data for tests."""
    from models.users import UserCreate
    
    return UserCreate(
        email="test@example.com",
        password="test_password_123",
        full_name="Test User"
    )
