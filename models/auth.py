"""
MediVerse - Authentication Pydantic Models
"""

from typing import Optional
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


class RefreshRequest(BaseModel):
    refresh_token: str


class CurrentUser(BaseModel):
    """Decoded JWT payload."""
    user_id: int
    email: str
    role: str          # 'doctor' or 'manager' or 'admin' or 'super_admin'
    name: str
    specialty: Optional[str] = None   # only for doctors
