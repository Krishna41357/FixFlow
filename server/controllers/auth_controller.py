import os
from datetime import datetime, timedelta, timezone
from typing import Optional
import bcrypt as bcrypt_lib
from jose import JWTError, jwt
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv

from models.users import UserCreate, UserInDB, Token, TokenData

load_dotenv()

mongo_uri = os.getenv("MONGO_URI")
if not mongo_uri:
    raise RuntimeError("MONGO_URI not set in environment")

client = MongoClient(mongo_uri)
db = client["rag_database"]
users_collection = db["users"]

SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))


# ── Password utils ────────────────────────────────────────────────────────────

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt_lib.checkpw(plain_password[:72].encode(), hashed_password.encode())


def get_password_hash(password: str) -> str:
    return bcrypt_lib.hashpw(password[:72].encode(), bcrypt_lib.gensalt()).decode()


# ── JWT utils ─────────────────────────────────────────────────────────────────

def create_access_token(user_id: str, email: str, expires_delta=None) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {"user_id": user_id, "email": email, "exp": expire}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        email   = payload.get("email")
        if user_id is None or email is None:
            return None
        return TokenData(user_id=user_id, email=email)
    except JWTError:
        return None


def get_current_user(token: str):
    return verify_token(token)


# ── Document helper ───────────────────────────────────────────────────────────

def _doc_to_userindb(doc: dict) -> UserInDB:
    return UserInDB(
        id=str(doc["_id"]),
        email=doc.get("email", ""),
        username=doc.get("username", ""),
        full_name=doc.get("full_name"),
        hashed_password=doc.get("hashed_password", ""),
        is_active=doc.get("is_active", True),
        created_at=str(doc.get("created_at", datetime.now(timezone.utc).isoformat())),
        connection_ids=doc.get("connection_ids", [])
    )


# ── Email/password auth (existing) ────────────────────────────────────────────

def register_user(user_data: UserCreate):
    try:
        if users_collection.find_one({"email": user_data.email}) is not None:
            print(f"ERROR register_user: Email {user_data.email} already registered")
            return None
        hashed_password = get_password_hash(user_data.password)
        user_doc = {
            "email":           user_data.email,
            "username":        user_data.username,
            "full_name":       getattr(user_data, "full_name", None),
            "hashed_password": hashed_password,
            "created_at":      datetime.now(timezone.utc).isoformat(),
            "is_active":       True,
            "connection_ids":  [],
            "github_id":       None,   # populated later if user connects GitHub
            "github_login":    None,
        }
        result = users_collection.insert_one(user_doc)
        user_doc["_id"] = result.inserted_id
        return _doc_to_userindb(user_doc)
    except Exception as e:
        print(f"ERROR register_user: {e}")
        return None


def login_user(email: str, password: str):
    try:
        doc = users_collection.find_one({"email": email})
        if not doc:
            print(f"ERROR login_user: User {email} not found")
            return None
        if not verify_password(password, doc.get("hashed_password", "")):
            print(f"ERROR login_user: Invalid password for {email}")
            return None
        token = create_access_token(user_id=str(doc["_id"]), email=email)
        return Token(access_token=token, token_type="bearer")
    except Exception as e:
        print(f"ERROR login_user: {e}")
        return None


# ── GitHub OAuth auth (new) ───────────────────────────────────────────────────

def register_or_login_github(github_profile: dict) -> Optional[Token]:
    """
    Upsert a user from a GitHub OAuth profile dict, then return a JWT Token.

    github_profile keys expected:
        github_id        (int)
        github_login     (str)   e.g. "octocat"
        github_name      (str|None)
        github_email     (str|None)
        github_avatar_url (str|None)

    Flow:
        1. Look up by github_id  → found: update profile fields, issue token
        2. Look up by email      → found: link github_id to existing account, issue token
        3. Neither found         → create new account, issue token
    """
    try:
        github_id    = github_profile.get("github_id")
        github_login = github_profile.get("github_login", "")
        github_email = github_profile.get("github_email")  # may be None
        github_name  = github_profile.get("github_name")

        now = datetime.now(timezone.utc).isoformat()

        # ── 1. Existing user matched by github_id ─────────────────────────────
        doc = users_collection.find_one({"github_id": github_id})
        if doc:
            # Refresh GitHub profile fields in case they changed
            users_collection.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "github_login":     github_login,
                    "github_name":      github_name,
                    "github_avatar_url": github_profile.get("github_avatar_url"),
                    "updated_at":       now,
                }}
            )
            print(f"DEBUG register_or_login_github: Existing github_id={github_id} logged in")
            token = create_access_token(user_id=str(doc["_id"]), email=doc.get("email", github_login))
            return Token(access_token=token, token_type="bearer")

        # ── 2. Existing email-based account — link GitHub to it ───────────────
        if github_email:
            doc = users_collection.find_one({"email": github_email})
            if doc:
                users_collection.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {
                        "github_id":        github_id,
                        "github_login":     github_login,
                        "github_name":      github_name,
                        "github_avatar_url": github_profile.get("github_avatar_url"),
                        "updated_at":       now,
                    }}
                )
                print(f"DEBUG register_or_login_github: Linked github_id={github_id} to existing email {github_email}")
                token = create_access_token(user_id=str(doc["_id"]), email=github_email)
                return Token(access_token=token, token_type="bearer")

        # ── 3. New user — create account from GitHub profile ──────────────────
        # Use GitHub email if available, otherwise fall back to a placeholder
        email    = github_email or f"{github_login}@github.local"
        username = github_login
        # Ensure username is unique by appending suffix if needed
        if users_collection.find_one({"username": username}):
            username = f"{github_login}_{github_id}"

        user_doc = {
            "email":            email,
            "username":         username,
            "full_name":        github_name,
            "hashed_password":  "",        # no password — GitHub-only account
            "github_id":        github_id,
            "github_login":     github_login,
            "github_avatar_url": github_profile.get("github_avatar_url"),
            "created_at":       now,
            "updated_at":       now,
            "is_active":        True,
            "connection_ids":   [],
        }
        result = users_collection.insert_one(user_doc)
        user_doc["_id"] = result.inserted_id
        print(f"DEBUG register_or_login_github: Created new user for github_login={github_login}")

        token = create_access_token(user_id=str(result.inserted_id), email=email)
        return Token(access_token=token, token_type="bearer")

    except Exception as e:
        print(f"ERROR register_or_login_github: {e}")
        return None


# ── Existing lookup helpers ───────────────────────────────────────────────────

def get_user_by_id(user_id: str):
    try:
        doc = users_collection.find_one({"_id": ObjectId(user_id)})
        if not doc:
            return None
        return _doc_to_userindb(doc)
    except Exception as e:
        print(f"ERROR get_user_by_id: {e}")
        return None


def get_user_by_email(email: str):
    try:
        doc = users_collection.find_one({"email": email})
        if not doc:
            return None
        return _doc_to_userindb(doc)
    except Exception as e:
        print(f"ERROR get_user_by_email: {e}")
        return None


def get_user_by_github_id(github_id: int):
    """Look up a user by their GitHub numeric ID."""
    try:
        doc = users_collection.find_one({"github_id": github_id})
        if not doc:
            return None
        return _doc_to_userindb(doc)
    except Exception as e:
        print(f"ERROR get_user_by_github_id: {e}")
        return None