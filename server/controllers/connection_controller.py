import os
import requests
from typing import List, Optional
from datetime import datetime, timezone
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv

from models.users import ConnectionCreate, ConnectionInDB, ConnectionResponse

load_dotenv()

# MongoDB setup
mongo_uri = os.getenv("MONGO_URI")
if not mongo_uri:
    raise RuntimeError("MONGO_URI not set in environment")

client = MongoClient(mongo_uri)
db = client["rag_database"]
connections_collection = db["connections"]
users_collection = db["users"]


def create_connection(user_id: str, connection_data: ConnectionCreate) -> Optional[ConnectionInDB]:
    """
    Saves OpenMetadata URL + token + GitHub repo for a user's workspace.
    Returns ConnectionInDB if successful, None on error.
    """
    if not user_id:
        print("ERROR create_connection: user_id required")
        return None
    
    user_id = str(user_id)
    
    # Verify OpenMetadata connection before saving
    if not verify_openmetadata_connection(connection_data.openmetadata_url, connection_data.openmetadata_token):
        print("ERROR create_connection: Failed to verify OpenMetadata connection")
        return None
    
    connection_doc = {
        "user_id": user_id,
        "workspace_name": connection_data.workspace_name,
        "openmetadata_url": connection_data.openmetadata_url,
        "openmetadata_token": connection_data.openmetadata_token,  # Consider encryption in production
        "github_repo": connection_data.github_repo,
        "github_installation_id": None,  # Set after GitHub App installation
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "is_active": True
    }
    
    try:
        result = connections_collection.insert_one(connection_doc)
        
        # Add connection reference to user document
        users_collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$push": {"connections": str(result.inserted_id)}}
        )
        
        return ConnectionInDB(
            id=str(result.inserted_id),
            user_id=user_id,
            workspace_name=connection_data.workspace_name,
            openmetadata_url=connection_data.openmetadata_url,
            github_repo=connection_data.github_repo,
            github_installation_id=None,
            created_at=connection_doc["created_at"]
        )
    except Exception as e:
        print(f"ERROR create_connection: {e}")
        return None


def get_user_connections(user_id: str) -> List[ConnectionResponse]:
    """Lists all connections for a user (tokens masked in response)."""
    if not user_id:
        return []
    
    user_id = str(user_id)
    
    try:
        connections = connections_collection.find(
            {"user_id": user_id, "is_active": True}
        ).sort("created_at", -1)
        
        result = []
        for conn in connections:
            result.append(ConnectionResponse(
                id=str(conn["_id"]),
                user_id=user_id,
                workspace_name=conn.get("workspace_name"),
                openmetadata_url=conn.get("openmetadata_url"),
                github_repo=conn.get("github_repo"),
                github_installation_id=conn.get("github_installation_id"),
                created_at=conn.get("created_at", datetime.now(timezone.utc)),
                token_masked=f"***{conn.get('openmetadata_token', '')[-4:]}"  # Show last 4 chars
            ))
        
        return result
    except Exception as e:
        print(f"ERROR get_user_connections: {e}")
        return []


def get_connection_by_id(connection_id: str, user_id: str) -> Optional[ConnectionInDB]:
    """Fetches one connection — used internally before every API call to OpenMetadata."""
    if not connection_id or not user_id:
        return None
    
    user_id = str(user_id)
    
    try:
        connection = connections_collection.find_one({
            "_id": ObjectId(connection_id),
            "user_id": user_id,
            "is_active": True
        })
        
        if not connection:
            print(f"ERROR get_connection_by_id: Connection {connection_id} not found for user {user_id}")
            return None
        
        return ConnectionInDB(
            id=str(connection["_id"]),
            user_id=user_id,
            workspace_name=connection.get("workspace_name"),
            openmetadata_url=connection.get("openmetadata_url"),
            openmetadata_token=connection.get("openmetadata_token"),
            github_repo=connection.get("github_repo"),
            github_installation_id=connection.get("github_installation_id"),
            created_at=connection.get("created_at", datetime.now(timezone.utc))
        )
    except Exception as e:
        print(f"ERROR get_connection_by_id: {e}")
        return None


def verify_openmetadata_connection(url: str, token: str) -> bool:
    """Pings GET /api/v1/system/status to check the URL+token actually works."""
    try:
        endpoint = f"{url.rstrip('/')}/api/v1/system/status"
        headers = {"Authorization": f"Bearer {token}"}
        
        response = requests.get(endpoint, headers=headers, timeout=10)
        
        if response.status_code == 200:
            print(f"DEBUG verify_openmetadata_connection: Successfully verified {url}")
            return True
        else:
            print(f"ERROR verify_openmetadata_connection: Status {response.status_code} from {url}")
            return False
    except Exception as e:
        print(f"ERROR verify_openmetadata_connection: {e}")
        return False


def delete_connection(connection_id: str, user_id: str) -> bool:
    """Removes connection + cascades to mark related investigations as orphaned."""
    if not connection_id or not user_id:
        return False
    
    user_id = str(user_id)
    
    try:
        # Mark connection as inactive instead of hard delete
        result = connections_collection.update_one(
            {"_id": ObjectId(connection_id), "user_id": user_id},
            {
                "$set": {
                    "is_active": False,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        if result.modified_count > 0:
            # TODO: Cascade to mark related investigations as orphaned
            print(f"DEBUG delete_connection: Marked connection {connection_id} as inactive")
            return True
        
        return False
    except Exception as e:
        print(f"ERROR delete_connection: {e}")
        return False


def set_github_installation_id(connection_id: str, user_id: str, installation_id: str) -> bool:
    """Called after GitHub App install. Saves the installation_id on the connection."""
    if not connection_id or not user_id or not installation_id:
        return False
    
    user_id = str(user_id)
    
    try:
        result = connections_collection.update_one(
            {"_id": ObjectId(connection_id), "user_id": user_id},
            {
                "$set": {
                    "github_installation_id": installation_id,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        return result.modified_count > 0
    except Exception as e:
        print(f"ERROR set_github_installation_id: {e}")
        return False
