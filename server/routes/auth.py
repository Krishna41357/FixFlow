"""
Authentication Routes
Handles user registration, login, and JWT token management.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from models.users import UserCreate, UserLogin, Token, TokenData
from controllers import auth_controller

router = APIRouter(prefix="/users", tags=["auth"])
security = HTTPBearer()


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> TokenData:
    """
    FastAPI dependency to extract and validate current user from JWT token.
    Used on all protected routes.
    """
    token = credentials.credentials
    token_data = auth_controller.verify_token(token)
    
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return token_data


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate) -> Token:
    """
    Register a new user.
    
    **Request:**
    ```json
    {
        "email": "user@example.com",
        "password": "securepassword",
        "full_name": "John Doe"
    }
    ```
    
    **Response:**
    ```json
    {
        "access_token": "eyJhbGciOiJIUzI1NiIs...",
        "token_type": "bearer"
    }
    ```
    """
    # Check if user already exists
    existing = auth_controller.get_user_by_email(user_data.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Register user
    registered_user = auth_controller.register_user(user_data)
    if not registered_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to register user"
        )
    
    # Create token
    token = auth_controller.create_access_token(
        user_id=registered_user.id,
        email=registered_user.email
    )
    
    return Token(access_token=token, token_type="bearer")


@router.post("/login", response_model=Token)
async def login(credentials: UserLogin) -> Token:
    """
    Login with email and password.
    
    **Query Parameters:**
    - `email`: User email
    - `password`: User password
    
    **Response:**
    ```json
    {
        "access_token": "eyJhbGciOiJIUzI1NiIs...",
        "token_type": "bearer"
    }
    ```
    """
    token = auth_controller.login_user(credentials.email, credentials.password)
    
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    return token


@router.get("/me", response_model=dict)
async def get_current_user_info(current_user: TokenData = Depends(get_current_user)) -> dict:
    """
    Get current user information from JWT token.
    
    **Response:**
    ```json
    {
        "user_id": "507f1f77bcf86cd799439011",
        "email": "user@example.com"
    }
    ```
    """
    return {
        "user_id": current_user.user_id,
        "email": current_user.email
    }


@router.post("/refresh", response_model=Token)
async def refresh_token(current_user: TokenData = Depends(get_current_user)) -> Token:
    """
    Refresh JWT token using current token.
    
    **Response:**
    ```json
    {
        "access_token": "eyJhbGciOiJIUzI1NiIs...",
        "token_type": "bearer"
    }
    ```
    """
    new_token = auth_controller.create_access_token(
        user_id=current_user.user_id,
        email=current_user.email
    )
    
    return Token(access_token=new_token, token_type="bearer")
