from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class User:
    id: Optional[int] = None
    email: str = ""
    name: str = ""
    company_name: str = ""
    hashed_password: str = ""
    is_active: bool = True
    is_verified: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    @classmethod
    def from_db_row(cls, row):
        """Create User instance from database row"""
        if row is None:
            return None
        return cls(
            id=row[0],
            email=row[1],
            name=row[2],
            company_name=row[3],
            hashed_password=row[4],
            is_active=row[5],
            is_verified=row[6],
            created_at=row[7],
            updated_at=row[8]
        )

@dataclass
class Agent:
    id: Optional[int] = None
    user_id: int = None
    agent_id: str = ""
    agent_name: str = ""
    first_message: str = ""
    prompt: str = ""
    llm: str = ""
    documentation_id: Optional[str] = None
    file_name: Optional[str] = None
    file_url: Optional[str] = None
    voice_id: Optional[str] = None
    twilio_number: str = ""
    business_name: Optional[str] = None
    agent_type: Optional[str] = None
    speaking_style: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_db_row(cls, row):
        """Create Agent instance from database row"""
        if row is None:
            return None
        return cls(
            id=row[0],
            user_id=row[1],
            agent_id=row[2],
            agent_name=row[3],
            first_message=row[4],
            prompt=row[5],
            llm=row[6],
            documentation_id=row[7],
            file_name=row[8],
            file_url=row[9],
            voice_id=row[10],
            twilio_number=row[11],
            business_name=row[12],
            agent_type=row[13],
            speaking_style=row[14],
            created_at=row[15],
            updated_at=row[16]
        ) 