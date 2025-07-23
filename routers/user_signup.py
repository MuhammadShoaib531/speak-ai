from fastapi import APIRouter, Depends, HTTPException, status
from datetime import timedelta

from models import User
from schemas import UserCreate, UserResponse, UserLogin, Token, UserCreateResponse
from auth import (
    get_password_hash, 
    authenticate_user, 
    create_access_token, 
    get_current_active_user,
    get_user_by_email,
    create_user,
    ACCESS_TOKEN_EXPIRE_MINUTES
)

# Create router instance
router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)

@router.post("/signup", response_model=UserCreateResponse)
async def signup(user: UserCreate):
    """
    Register a new user with name, email, password, confirm_password, and company_name
    """
    # Check if user already exists
    existing_user = get_user_by_email(email=user.email)
    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Email already registered"
        )
    
    # Create new user
    hashed_password = get_password_hash(user.password)
    new_user = create_user(
        email=user.email,
        name=user.name,
        company_name=user.company_name,
        hashed_password=hashed_password
    )
    response = {
        "message": "User created successfully",
        "user": new_user
    }
    
    return response

@router.post("/login", response_model=Token)
async def login(user_credentials: UserLogin):
    """
    Login with email and password to get access token
    Use this endpoint for simple Bearer token authentication
    """
    user = authenticate_user(user_credentials.email, user_credentials.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_active_user)):
    """
    Get current user information (requires authentication)
    """
    return current_user

@router.get("/protected")
async def protected_route(current_user: User = Depends(get_current_active_user)):
    """
    Example protected route that requires authentication
    """
    return {"message": f"Hello {current_user.name}, this is a protected route!"} 