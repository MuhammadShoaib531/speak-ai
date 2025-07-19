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