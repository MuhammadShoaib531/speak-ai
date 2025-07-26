from pydantic import BaseModel, EmailStr, validator
from typing import Optional
from datetime import datetime

class UserBase(BaseModel):
    email: EmailStr
    name: str
    company_name: str

class UserCreate(UserBase):
    password: str
    confirm_password: str
    
    @validator('confirm_password')
    def passwords_match(cls, v, values, **kwargs):
        if 'password' in values and v != values['password']:
            raise ValueError('Passwords do not match')
        return v

class UserLogin(BaseModel):
    email: str
    password: str

class PasswordUpdate(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str
    
    @validator('confirm_password')
    def passwords_match(cls, v, values, **kwargs):
        if 'new_password' in values and v != values['new_password']:
            raise ValueError('New password and confirm password do not match')
        return v

class UserResponse(UserBase):
    id: int
    role: str
    is_active: bool
    is_verified: bool
    created_at: datetime
    
    class Config:
        from_attributes = True

class UserCreateResponse(BaseModel):
    message: str
    user: UserResponse

class Token(BaseModel):
    access_token: str
    token_type: str
    role: str

class TokenData(BaseModel):
    email: Optional[str] = None 