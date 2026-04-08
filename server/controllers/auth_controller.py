import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from passlib.context import CryptContext
from jose import JWTError, jwt
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv

from models.users import UserCreate, UserInDB, Token, TokenData

load_dotenv()

# Password hashing setup
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# MongoDB setup
mongo_uri = os.getenv("MONGO_URI")
if not mongo_uri:
    raise RuntimeError("MONGO_URI not set in environment")

client = MongoClient(mongo_uri)
db = client["rag_database"]
users_collection = db["users"]

# JWT setup
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against hashed password using bcrypt."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)


def create_access_token(user_id: str, email: str, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token with user_id and email."""
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {
        "user_id": user_id,
        "email": email,
        "exp": expire
    }
    
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> Optional[TokenData]:
    """Decode JWT token and return TokenData. Both fields required."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("user_id")
        email: str = payload.get("email")
        
        if user_id is None or email is None:
            return None
        
        return TokenData(user_id=user_id, email=email)
    except JWTError:
        return None


def get_current_user(token: str) -> Optional[TokenData]:
    """
    FastAPI dependency. Returns TokenData from JWT token.
    Used on every protected route.
    """
    return verify_token(token)


def register_user(user_data: UserCreate) -> Optional[UserInDB]:
    """
    Register a new user. Checks email uniqueness, hashes password, inserts UserInDB.
    Returns UserInDB if successful, None if email already exists.
    """
    # Check if email already exists
    existing_user = users_collection.find_one({"email": user_data.email})
    if existing_user:
        print(f"ERROR register_user: Email {user_data.email} already registered")
        return None
    
    # Hash the password
    hashed_password = get_password_hash(user_data.password)
    
    # Create user document
    user_doc = {
        "email": user_data.email,
        "full_name": user_data.full_name,
        "hashed_password": hashed_password,
        "created_at": datetime.now(timezone.utc),
        "is_active": True,
        "connections": []  # References to connections
    }
    
    try:
        result = users_collection.insert_one(user_doc)
        user_doc["_id"] = result.inserted_id
        
        return UserInDB(
            id=str(result.inserted_id),
            email=user_data.email,
            full_name=user_data.full_name,
            is_active=True
        )
    except Exception as e:
        print(f"ERROR register_user: {e}")
        return None


def login_user(email: str, password: str) -> Optional[Token]:
    """
    Login user. Finds user by email, verifies password, returns Token.
    Returns Token if successful, None if email not found or password incorrect.
    """
    user_doc = users_collection.find_one({"email": email})
    if not user_doc:
        print(f"ERROR login_user: User {email} not found")
        return None
    
    # Verify password
    if not verify_password(password, user_doc.get("hashed_password", "")):
        print(f"ERROR login_user: Invalid password for {email}")
        return None
    
    # Create access token
    access_token = create_access_token(
        user_id=str(user_doc["_id"]),
        email=email
    )
    
    return Token(access_token=access_token, token_type="bearer")


def get_user_by_id(user_id: str) -> Optional[UserInDB]:
    """Get user by ID."""
    try:
        user_doc = users_collection.find_one({"_id": ObjectId(user_id)})
        if not user_doc:
            return None
        
        return UserInDB(
            id=str(user_doc["_id"]),
            email=user_doc.get("email"),
            full_name=user_doc.get("full_name"),
            is_active=user_doc.get("is_active", True)
        )
    except Exception as e:
        print(f"ERROR get_user_by_id: {e}")
        return None


def get_user_by_email(email: str) -> Optional[UserInDB]:
    """Get user by email."""
    try:
        user_doc = users_collection.find_one({"email": email})
        if not user_doc:
            return None
        
        return UserInDB(
            id=str(user_doc["_id"]),
            email=user_doc.get("email"),
            full_name=user_doc.get("full_name"),
            is_active=user_doc.get("is_active", True)
        )
    except Exception as e:
        print(f"ERROR get_user_by_email: {e}")
        return None
