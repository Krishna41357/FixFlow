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


def _doc_to_connectionindb(doc: dict) -> ConnectionInDB:
    """Convert raw MongoDB document to ConnectionInDB."""
    return ConnectionInDB(
        id=str(doc["_id"]),
        user_id=str(doc.get("user_id", "")),
        name=doc.get("name") or doc.get("workspace_name", ""),
        openmetadata_host=doc.get("openmetadata_host") or doc.get("openmetadata_url", ""),
        openmetadata_token=doc.get("openmetadata_token", ""),
        dbt_webhook_secret=doc.get("dbt_webhook_secret"),
        github_repo=doc.get("github_repo"),
        github_installation_id=doc.get("github_installation_id"),
        is_active=doc.get("is_active", True),
        created_at=str(doc.get("created_at", datetime.now(timezone.utc).isoformat()))
    )


def create_connection(user_id: str, connection_data: ConnectionCreate) -> Optional[ConnectionInDB]:
    """
    Saves OpenMetadata host + token + GitHub repo for a user's workspace.
    Returns ConnectionInDB if successful, None on error.
    """
    if not user_id:
        print("ERROR create_connection: user_id required")
        return None

    user_id = str(user_id)

    connection_doc = {
        "user_id": user_id,
        "name": connection_data.name,
        "openmetadata_host": connection_data.openmetadata_host,
        "openmetadata_token": connection_data.openmetadata_token,
        "dbt_webhook_secret": connection_data.dbt_webhook_secret,
        "github_repo": connection_data.github_repo,
        "github_installation_id": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "is_active": True
    }

    try:
        result = connections_collection.insert_one(connection_doc)
        connection_doc["_id"] = result.inserted_id

        # Add connection reference to user document
        users_collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$push": {"connection_ids": str(result.inserted_id)}}
        )

        return _doc_to_connectionindb(connection_doc)

    except Exception as e:
        print(f"ERROR create_connection: {e}")
        return None


def get_user_connections(user_id: str) -> List[ConnectionResponse]:
    """Lists all active connections for a user (tokens masked in response)."""
    if not user_id:
        return []

    user_id = str(user_id)

    try:
        connections = connections_collection.find(
            {"user_id": user_id, "is_active": True}
        ).sort("created_at", -1)

        result = []
        for conn in connections:
            token = conn.get("openmetadata_token", "")
            result.append(ConnectionResponse(
                id=str(conn["_id"]),
                name=conn.get("name") or conn.get("workspace_name", ""),
                openmetadata_host=conn.get("openmetadata_host") or conn.get("openmetadata_url", ""),
                github_repo=conn.get("github_repo"),
                is_active=conn.get("is_active", True),
                created_at=str(conn.get("created_at", ""))
            ))

        return result

    except Exception as e:
        print(f"ERROR get_user_connections: {e}")
        return []


def get_connection_by_id(connection_id: str, user_id: str) -> Optional[ConnectionInDB]:
    """Fetches one connection by ID — used before every OpenMetadata API call."""
    if not connection_id or not user_id:
        return None

    user_id = str(user_id)

    try:
        # Handle non-ObjectId connection_ids gracefully (e.g. "demo_conn")
        try:
            query = {"_id": ObjectId(connection_id), "user_id": user_id, "is_active": True}
        except Exception:
            return None

        connection = connections_collection.find_one(query)

        if not connection:
            print(f"ERROR get_connection_by_id: Connection {connection_id} not found")
            return None

        return _doc_to_connectionindb(connection)

    except Exception as e:
        print(f"ERROR get_connection_by_id: {e}")
        return None


def verify_openmetadata_connection(url: str, token: str) -> bool:
    """Pings GET /api/v1/system/status to verify the connection works."""
    try:
        endpoint = f"{url.rstrip('/')}/api/v1/system/status"
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(endpoint, headers=headers, timeout=10)

        if response.status_code == 200:
            print(f"DEBUG verify_openmetadata_connection: OK {url}")
            return True
        else:
            print(f"ERROR verify_openmetadata_connection: Status {response.status_code}")
            return False
    except Exception as e:
        print(f"ERROR verify_openmetadata_connection: {e}")
        return False


def delete_connection(connection_id: str, user_id: str) -> bool:
    """Soft-deletes a connection by marking it inactive."""
    if not connection_id or not user_id:
        return False

    user_id = str(user_id)

    try:
        result = connections_collection.update_one(
            {"_id": ObjectId(connection_id), "user_id": user_id},
            {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc).isoformat()}}
        )

        if result.modified_count > 0:
            print(f"DEBUG delete_connection: Marked {connection_id} inactive")
            return True

        return False
    except Exception as e:
        print(f"ERROR delete_connection: {e}")
        return False


def set_github_installation_id(connection_id: str, user_id: str, installation_id: str) -> bool:
    """Saves GitHub App installation_id after user authorizes the app."""
    if not connection_id or not user_id or not installation_id:
        return False

    user_id = str(user_id)

    try:
        result = connections_collection.update_one(
            {"_id": ObjectId(connection_id), "user_id": user_id},
            {"$set": {
                "github_installation_id": installation_id,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }}
        )
        return result.modified_count > 0
    except Exception as e:
        print(f"ERROR set_github_installation_id: {e}")
        return False