"""Auth Router - /auth/doctor/login, /auth/manager/login, /auth/refresh, /auth/me"""

from fastapi import APIRouter, HTTPException, Depends
from models.auth import LoginRequest, TokenResponse, RefreshRequest, CurrentUser
from services.auth_service import (
    authenticate_doctor, authenticate_manager,
    create_access_token, create_refresh_token, decode_token, get_current_user,
)
from config.settings import settings

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login")
async def unified_login(req: LoginRequest):
    """Unified login - checks managers first, then doctors."""
    # 1) Try managers first
    user = authenticate_manager(req.email, req.password)
    if user:
        td = {"user_id": user["user_id"], "email": user["email"], "role": user.get("role", "admin"), "name": user["name"]}
        return TokenResponse(
            access_token=create_access_token(td),
            refresh_token=create_refresh_token(td),
            expires_in=settings.jwt.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user=user,
        )

    # 2) Try doctors
    user = authenticate_doctor(req.email, req.password)
    if user:
        # Mark doctor as online
        try:
            from database.connection import DatabaseManager
            with DatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE doctors SET is_online = 1, current_status = 'available' WHERE doctor_id = ?", (user["user_id"],))
                conn.commit()
                cursor.close()
        except Exception:
            pass
        td = {"user_id": user["user_id"], "email": user["email"], "role": "doctor", "name": user["name"], "specialty": user.get("specialty")}
        return TokenResponse(
            access_token=create_access_token(td),
            refresh_token=create_refresh_token(td),
            expires_in=settings.jwt.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user=user,
        )

    # 3) Not found anywhere
    raise HTTPException(status_code=401, detail="Invalid email or password")


# Keep the old endpoints for backward compatibility
@router.post("/doctor/login", response_model=TokenResponse)
async def doctor_login(req: LoginRequest):
    user = authenticate_doctor(req.email, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    td = {"user_id": user["user_id"], "email": user["email"], "role": "doctor", "name": user["name"], "specialty": user.get("specialty")}
    return TokenResponse(access_token=create_access_token(td), refresh_token=create_refresh_token(td),
                         expires_in=settings.jwt.ACCESS_TOKEN_EXPIRE_MINUTES*60, user=user)

@router.post("/manager/login", response_model=TokenResponse)
async def manager_login(req: LoginRequest):
    user = authenticate_manager(req.email, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    td = {"user_id": user["user_id"], "email": user["email"], "role": user.get("role","admin"), "name": user["name"]}
    return TokenResponse(access_token=create_access_token(td), refresh_token=create_refresh_token(td),
                         expires_in=settings.jwt.ACCESS_TOKEN_EXPIRE_MINUTES*60, user=user)

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(req: RefreshRequest):
    payload = decode_token(req.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    td = {k: payload.get(k) for k in ("user_id","email","role","name","specialty")}
    return TokenResponse(access_token=create_access_token(td), refresh_token=create_refresh_token(td),
                         expires_in=settings.jwt.ACCESS_TOKEN_EXPIRE_MINUTES*60, user=td)

@router.get("/me")
async def get_me(user: CurrentUser = Depends(get_current_user)):
    return {"user_id": user.user_id, "email": user.email, "role": user.role, "name": user.name, "specialty": user.specialty}


# ── Patient Login by National ID ──
from pydantic import BaseModel, Field

class PatientLoginRequest(BaseModel):
    national_id: str = Field(..., min_length=14, max_length=14)

@router.post("/patient-login")
async def patient_login(req: PatientLoginRequest):
    """Login patient using national_id — returns JWT token."""
    from database.connection import DatabaseManager
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, full_name, national_id, phone_number, gender FROM Patients WHERE national_id = ?", (req.national_id,))
            row = cursor.fetchone()
            cursor.close()
        if not row:
            raise HTTPException(status_code=404, detail="لم يتم العثور على مريض بهذا الرقم القومي")
        user = {
            "user_id": row[0], "name": row[1], "email": row[2],
            "role": "patient", "national_id": row[2],
            "phone": row[3], "gender": row[4],
        }
        td = {"user_id": row[0], "email": row[2], "role": "patient", "name": row[1]}
        return TokenResponse(
            access_token=create_access_token(td),
            refresh_token=create_refresh_token(td),
            expires_in=settings.jwt.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user=user,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/logout")
async def logout(user: CurrentUser = Depends(get_current_user)):
    """Mark doctor as offline on logout."""
    if user.role == "doctor":
        try:
            from database.connection import DatabaseManager
            with DatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE doctors SET is_online = 0, current_status = 'unavailable' WHERE doctor_id = ?", (user.user_id,))
                conn.commit()
                cursor.close()
        except Exception:
            pass
    return {"message": "Logged out"}
