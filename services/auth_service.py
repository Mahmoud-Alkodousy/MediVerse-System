"""
MediVerse - Authentication Service
JWT token generation/validation + password hashing for doctor & manager login.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from jose import jwt, JWTError
import bcrypt
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config.settings import settings
from database.connection import DatabaseManager
from models.auth import CurrentUser

logger = logging.getLogger("mediverse")

# Bearer token extractor
security = HTTPBearer()


# ── Password Utilities ─────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False


# ── JWT Token Utilities ────────────────────────────────────────

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=settings.jwt.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.jwt.SECRET_KEY, algorithm=settings.jwt.ALGORITHM)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.jwt.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.jwt.SECRET_KEY, algorithm=settings.jwt.ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt.SECRET_KEY, algorithms=[settings.jwt.ALGORITHM])
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Authentication Functions ───────────────────────────────────

def authenticate_doctor(email: str, password: str) -> Optional[dict]:
    """Verify doctor credentials and return doctor data."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT doctor_id, doctor_name, email, password_hash, specialty,
                       room_number, floor_number, current_status, profile_image_url,
                       is_active
                FROM doctors
                WHERE email = ? AND is_active = 1
            """, (email,))
            row = cursor.fetchone()
            cursor.close()

        if not row:
            return None

        if not verify_password(password, row[3]):
            return None

        return {
            "user_id": row[0],
            "name": row[1],
            "email": row[2],
            "role": "doctor",
            "specialty": row[4],
            "room_number": row[5],
            "floor_number": row[6],
            "current_status": row[7],
            "profile_image_url": row[8],
        }
    except Exception as e:
        logger.error(f"Doctor authentication error: {e}")
        return None


def authenticate_manager(email: str, password: str) -> Optional[dict]:
    """Verify manager credentials and return manager data."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT manager_id, full_name, email, password_hash, role, is_active
                FROM managers
                WHERE email = ? AND is_active = 1
            """, (email,))
            row = cursor.fetchone()
            cursor.close()

        if not row:
            return None

        if not verify_password(password, row[3]):
            return None

        # Update last_login
        try:
            with DatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE managers SET last_login = GETDATE() WHERE manager_id = ?",
                    (row[0],),
                )
                cursor.close()
        except Exception:
            pass

        return {
            "user_id": row[0],
            "name": row[1],
            "email": row[2],
            "role": row[4] or "admin",
        }
    except Exception as e:
        logger.error(f"Manager authentication error: {e}")
        return None


# ── Dependency: Get Current User from JWT ──────────────────────

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> CurrentUser:
    """FastAPI dependency: extract and validate JWT, return CurrentUser."""
    payload = decode_token(credentials.credentials)

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")

    return CurrentUser(
        user_id=payload.get("user_id"),
        email=payload.get("email", ""),
        role=payload.get("role", ""),
        name=payload.get("name", ""),
        specialty=payload.get("specialty"),
    )


async def require_doctor(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Dependency: ensure the user is a doctor."""
    if current_user.role != "doctor":
        raise HTTPException(status_code=403, detail="Doctor access required")
    return current_user


async def require_manager(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Dependency: ensure the user is a manager/admin."""
    if current_user.role not in ("admin", "super_admin", "manager", "receptionist"):
        raise HTTPException(status_code=403, detail="Manager access required")
    return current_user
