from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import os
from dotenv import load_dotenv

from database import get_db
from models import User
from schemas import TokenData

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def get_user_by_email(email: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, email, name, company_name, hashed_password, role, is_active, is_verified, created_at, updated_at FROM users WHERE email = %s",
            (email,)
        )
        row = cursor.fetchone()
        return User.from_db_row(row)

def create_user(email: str, name: str, company_name: str, hashed_password: str, role: str = "Admin"):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO users (email, name, company_name, hashed_password, role) 
               VALUES (%s, %s, %s, %s, %s) 
               RETURNING id, email, name, company_name, hashed_password, role, is_active, is_verified, created_at, updated_at""",
            (email, name, company_name, hashed_password, role)
        )
        row = cursor.fetchone()
        conn.commit()
        return User.from_db_row(row)

def authenticate_user(email: str, password: str):
    user = get_user_by_email(email)
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    if SECRET_KEY and ALGORITHM:
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        return encoded_jwt
    raise HTTPException(status_code=500, detail="JWT configuration error")

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        if not SECRET_KEY or not ALGORITHM:
            raise credentials_exception
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email)
    except JWTError:
        raise credentials_exception
    user = get_user_by_email(email=token_data.email or "")
    if user is None:
        raise credentials_exception
    return user

async def get_current_active_user(current_user: User = Depends(get_current_user)):
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def update_user_password(user_id: int, new_password_hash: str):
    """Update user password in database"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users 
            SET hashed_password = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (new_password_hash, user_id))
        
        if cursor.rowcount == 0:
            return False
        
        conn.commit()
        return True 