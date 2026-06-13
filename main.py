"""
================================================================================
MediVerse Main API Application v4.0
================================================================================
- Keeps ALL existing endpoints (check-face, check-national-id, register-patient,
  chatbot/consultation, chatbot/doctors, chatbot/models) with SAME signatures.
- Adds new routers: auth, appointments, doctor dashboard, manager dashboard.
================================================================================
"""

import os
import time
import uuid
import signal
import shutil
import secrets
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from threading import Lock
from typing import Optional, Dict

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Depends
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from config.settings import settings
from database.connection import DatabaseManager, init_pyodbc_pool, get_db_connection
from middleware.rate_limiter import rate_limiter
from middleware.request_logger import RequestLoggingMiddleware
from utils.helpers import sanitize_value

# ── Import existing services (unchanged logic) ─────────────────
from services.face_recognition_service import (
    init_face_model, cleanup as face_cleanup,
    validate_image_file, extract_face_embedding,
    load_embeddings_and_patients, find_matching_patient,
    get_patient_by_national_id, save_new_patient,
    init_database as init_face_db,
)
from services.chatbot_service import (
    get_chatbot,
    ConsultationResult as ChatbotConsultationResult,
    ChatbotDatabaseManager, HealthCheck as ChatbotHealth, cache_mgr,
)

# ── Import Pydantic models ─────────────────────────────────────
from models.patient import (
    PatientOut, PatientRegistration,
    FaceCheckResponse, NationalIdCheckResponse, PatientRegistrationResponse,
)
from models.consultation import ConsultationRequest

# ── Import new routers ─────────────────────────────────────────
from routers.auth_router import router as auth_router
from routers.patient_router import router as patient_router
from routers.appointment_router import router as appointment_router
from routers.doctor_router import router as doctor_router
from routers.manager_router import router as manager_router
from routers.health_router import router as health_router
from routers.medical_files_router import router as medical_files_router
from routers.pharmacy_router import router as pharmacy_router

# ── Logging Setup ──────────────────────────────────────────────
from logging.handlers import RotatingFileHandler

def setup_logging():
    fmt = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
    )
    fh = RotatingFileHandler(settings.log.FILE, maxBytes=settings.log.MAX_BYTES,
                              backupCount=settings.log.BACKUP_COUNT, encoding='utf-8')
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log.LEVEL.upper()))
    if not root.handlers:
        root.addHandler(fh)
        root.addHandler(ch)
    return logging.getLogger("mediverse")

logger = setup_logging()

# ── Face Model Singleton ───────────────────────────────────────
class FaceAppSingleton:
    _instance = None
    _lock = Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance.app = None
        return cls._instance

    def initialize(self):
        if self.app is None:
            with self._lock:
                if self.app is None:
                    self.app = init_face_model()
        return self.app

    def get(self):
        if self.app is None:
            raise RuntimeError("Face app not initialized")
        return self.app

    def cleanup(self):
        try:
            face_cleanup()
        finally:
            self.app = None

face_app_singleton = FaceAppSingleton()

# ── Temp Cleanup ───────────────────────────────────────────────
def _cleanup_old_temp_files(temp_dir: Path, max_age_hours: int = 1):
    if not temp_dir.exists():
        return
    cutoff = datetime.now() - timedelta(hours=max_age_hours)
    for fp in temp_dir.glob("*"):
        try:
            if fp.is_file() and datetime.fromtimestamp(fp.stat().st_mtime) < cutoff:
                fp.unlink()
        except Exception:
            pass

async def _periodic_cleanup(temp_dir: Path):
    try:
        while True:
            await asyncio.sleep(3600)
            _cleanup_old_temp_files(temp_dir)
    except asyncio.CancelledError:
        return


async def _auto_queue_scheduled_appointments():
    """Every 60s, check for scheduled appointments whose time has come and add to queue."""
    try:
        while True:
            await asyncio.sleep(60)
            try:
                with DatabaseManager.get_connection() as conn:
                    cursor = conn.cursor()
                    # Find appointments for today that are 'scheduled' and their time has come (within 5 min)
                    cursor.execute("""
                        SELECT a.appointment_id, a.patient_id, a.doctor_id, a.appointment_time
                        FROM appointments a
                        WHERE a.appointment_date = CAST(GETDATE() AS DATE)
                          AND a.status = 'scheduled'
                          AND CAST(a.appointment_time AS TIME) <= DATEADD(MINUTE, 5, CAST(GETDATE() AS TIME))
                          AND NOT EXISTS (
                              SELECT 1 FROM appointment_queue q 
                              WHERE q.patient_id = a.patient_id 
                                AND q.doctor_id = a.doctor_id
                                AND q.queue_status IN ('waiting','called','in_progress')
                          )
                    """)
                    rows = cursor.fetchall()
                    for row in rows:
                        appt_id, patient_id, doctor_id, appt_time = row
                        # Add to queue with high priority (position 1 = front)
                        cursor.execute("""
                            INSERT INTO appointment_queue (
                                patient_id, doctor_id, appointment_id,
                                severity_level, queue_position, queue_status,
                                joined_queue_at, initial_priority, current_priority,
                                estimated_wait_minutes
                            )
                            VALUES (?, ?, ?, 5, 0, 'waiting', GETDATE(), 10, 10, 0)
                        """, (patient_id, doctor_id, appt_id))
                        # Update appointment status
                        cursor.execute("""
                            UPDATE appointments SET status = 'queued' 
                            WHERE appointment_id = ?
                        """, (appt_id,))
                        logger.info(f"Auto-queued appointment {appt_id} for patient {patient_id}")
                    if rows:
                        conn.commit()
                    cursor.close()
            except Exception as e:
                logger.error(f"Auto-queue error: {e}")
    except asyncio.CancelledError:
        return

# ── Lifespan ───────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 80)
    logger.info("MediVerse API v4.0 - Starting Up")
    logger.info("=" * 80)

    temp_dir = Path(settings.image.TEMP_UPLOAD_DIR)
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Init DB pools
    try:
        init_pyodbc_pool()
        DatabaseManager.get_engine()  # init SQLAlchemy pool too
        # Ensure is_online column exists in doctors table
        try:
            with DatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS 
                                   WHERE TABLE_NAME='doctors' AND COLUMN_NAME='is_online')
                    BEGIN
                        ALTER TABLE doctors ADD is_online BIT DEFAULT 0
                    END
                """)
                # Reset all doctors to offline on server start
                cursor.execute("UPDATE doctors SET is_online = 0")
                # Update view to include is_online
                cursor.execute("""
                    CREATE OR ALTER VIEW vw_AvailableDoctors AS
                    SELECT 
                        d.doctor_id, d.doctor_name, d.specialty, d.room_number, d.floor_number,
                        d.current_patients_count, d.average_consultation_minutes, d.rating,
                        d.current_status, d.profile_image_url, d.shift_start, d.shift_end,
                        d.max_patients_per_day, d.years_of_experience,
                        ISNULL(d.is_online, 0) AS is_online,
                        ISNULL(wc.waiting_count, 0) AS waiting_count,
                        ISNULL(wc.waiting_count, 0) * d.average_consultation_minutes AS estimated_wait_minutes
                    FROM doctors d
                    OUTER APPLY (
                        SELECT COUNT(*) AS waiting_count 
                        FROM appointment_queue q 
                        WHERE q.doctor_id = d.doctor_id AND q.queue_status = 'waiting'
                    ) wc
                    WHERE d.is_active = 1
                """)
                conn.commit()
                cursor.close()
            logger.info("doctors.is_online column ensured")
        except Exception as e:
            logger.warning(f"is_online column setup: {e}")
    except Exception as e:
        logger.error(f"Database init failed: {e}")
        raise

    # Init face recognition database pool
    try:
        init_face_db()
        logger.info("Face recognition DB pool initialized")
    except Exception as e:
        logger.warning(f"Face DB init failed (non-fatal): {e}")

    # Init face model
    try:
        face_app_singleton.initialize()
    except Exception as e:
        logger.error(f"Face model init failed: {e}")
        raise

    _cleanup_old_temp_files(temp_dir)
    cleanup_task = asyncio.create_task(_periodic_cleanup(temp_dir))
    autoqueue_task = asyncio.create_task(_auto_queue_scheduled_appointments())

    # Load pharmacy drug embeddings
    try:
        from services.pharmacy_service import load_embeddings
        loaded = load_embeddings()
        if not loaded:
            logger.warning("⚠️ No pharmacy embeddings cache. Run POST /pharmacy/embeddings/build")
    except Exception as e:
        logger.warning(f"Pharmacy embeddings load skipped: {e}")

    try:
        yield
    finally:
        logger.info("Shutting down...")
        cleanup_task.cancel()
        autoqueue_task.cancel()
        try:
            await asyncio.wait_for(cleanup_task, timeout=5)
        except Exception:
            pass
        try:
            await asyncio.wait_for(autoqueue_task, timeout=5)
        except Exception:
            pass
        _cleanup_old_temp_files(temp_dir)
        face_app_singleton.cleanup()
        DatabaseManager.dispose()
        logger.info("Shutdown complete")


# ── Create App ─────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    description=settings.APP_DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins (ngrok + local HTML files)
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Request logging middleware
app.add_middleware(RequestLoggingMiddleware)

# ── Rate Limit Dependency ──────────────────────────────────────
async def check_rate_limit(request: Request):
    if not rate_limiter.is_allowed(request.client.host):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

# ── Model Mapper (for chatbot) ─────────────────────────────────
class ModelMapper:
    _mapping: Optional[Dict[str, str]] = None

    @classmethod
    def get_mapping(cls):
        if cls._mapping is None:
            cls._mapping = {v: k for k, v in settings.chatbot.MODEL_DISPLAY_NAMES.items()}
        return cls._mapping

    @classmethod
    def get_model_id(cls, display_name: str) -> str:
        mapping = cls.get_mapping()
        if display_name in mapping:
            return mapping[display_name]
        for d, m in mapping.items():
            if d.lower() == display_name.lower():
                return m
        return mapping.get("GPT-4o Mini 🚀", settings.chatbot.OPENROUTER_MODEL)

# ============================================================================
# EXISTING ENDPOINTS (UNCHANGED SIGNATURES)
# ============================================================================

@app.get("/", summary="Root")
async def root():
    return {
        "name": settings.APP_TITLE, "version": settings.APP_VERSION,
        "status": "online",
        "endpoints": {
            "docs": "/docs", "health": "/health", "health_full": "/health/full",
            "check-face": "/check-face",
            "check-national-id": "/check-national-id/{national_id}",
            "register-patient": "/register-patient",
            "chatbot-consultation": "/chatbot/consultation",
            "chatbot-models": "/chatbot/models",
            "chatbot-doctors": "/chatbot/doctors/{specialty}",
            "auth-doctor-login": "/auth/doctor/login",
            "auth-manager-login": "/auth/manager/login",
            "appointments": "/appointments/specialties",
            "doctor-dashboard": "/doctor/profile",
            "manager-dashboard": "/manager/dashboard",
        },
    }


@app.post("/check-face", response_model=FaceCheckResponse, dependencies=[Depends(check_rate_limit)])
async def check_face(image: UploadFile = File(...)):
    logger.info(f"[check-face] Received: {image.filename}")
    try:
        validate_image_file(image)
    except HTTPException:
        raise

    try:
        face_app = face_app_singleton.get()
    except Exception:
        raise HTTPException(status_code=503, detail="Face recognition unavailable")

    suffix = Path(image.filename).suffix or ".jpg"
    temp_path = Path(settings.image.TEMP_UPLOAD_DIR) / f"{secrets.token_hex(16)}{suffix}"

    try:
        with open(temp_path, "wb") as f:
            shutil.copyfileobj(image.file, f)

        emb, quality_info = extract_face_embedding(str(temp_path), face_app)
        patients = load_embeddings_and_patients()
        matched, similarity = find_matching_patient(emb, patients)

        clean_quality = sanitize_value(quality_info)
        clean_sim = float(sanitize_value(similarity)) if similarity else 0.0

        if matched is None:
            return FaceCheckResponse(exists=False, similarity=clean_sim,
                                     message=f"No match (best: {clean_sim:.3f})", quality_info=clean_quality)

        dob = matched.get("date_of_birth")
        patient = PatientOut(
            id=matched["id"], full_name=matched["full_name"],
            national_id=matched.get("national_id"), gender=matched.get("gender"),
            date_of_birth=dob.isoformat() if dob else None,
            address=matched.get("address"), blood_type=matched.get("blood_type"),
            phone_number=matched.get("phone_number"),
            chronic_diseases=matched.get("chronic_diseases"),
            allergies=matched.get("allergies"),
            current_medications=matched.get("current_medications"),
            marital_status=matched.get("marital_status"), job=matched.get("job"),
            weight=matched.get("weight"), height=matched.get("height"),
            BMI=matched.get("BMI"),
        )
        return FaceCheckResponse(exists=True, similarity=clean_sim, patient=patient,
                                 message=f"Patient found ({clean_sim:.2%} match)", quality_info=clean_quality)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {e}")
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass


@app.get("/check-national-id/{national_id}", response_model=NationalIdCheckResponse, dependencies=[Depends(check_rate_limit)])
async def check_national_id(national_id: str):
    try:
        patient_data = get_patient_by_national_id(national_id)
        if not patient_data:
            return NationalIdCheckResponse(exists=False, message=f"No patient with national ID {national_id}")
        dob = patient_data.get("date_of_birth")
        # Calculate age
        age = None
        if dob:
            from datetime import date as date_cls
            today = date_cls.today()
            dob_date = dob if isinstance(dob, date_cls) else dob.date() if hasattr(dob, 'date') else None
            if dob_date:
                age = today.year - dob_date.year - ((today.month, today.day) < (dob_date.month, dob_date.day))
        patient = PatientOut(
            id=patient_data["id"], full_name=patient_data["full_name"],
            national_id=patient_data.get("national_id"), gender=patient_data.get("gender"),
            date_of_birth=dob.isoformat() if dob else None,
            address=patient_data.get("address"), blood_type=patient_data.get("blood_type"),
            phone_number=patient_data.get("phone_number"),
            chronic_diseases=patient_data.get("chronic_diseases"),
            allergies=patient_data.get("allergies"),
            current_medications=patient_data.get("current_medications"),
            marital_status=patient_data.get("marital_status"), job=patient_data.get("job"),
            weight=patient_data.get("weight"), height=patient_data.get("height"),
            BMI=patient_data.get("BMI"),
        )
        return NationalIdCheckResponse(exists=True, patient=patient, age=age, message=f"Patient found: {patient.full_name}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/register-patient", response_model=PatientRegistrationResponse, dependencies=[Depends(check_rate_limit)])
async def register_patient(request: Request, image: UploadFile = File(...), data: str = Form(...)):
    try:
        reg = PatientRegistration.model_validate_json(data)
    except ValidationError as e:
        safe_errors = []
        for err in e.errors():
            se = {k: sanitize_value(v) for k, v in err.items() if k != "ctx"}
            ctx = err.get("ctx")
            if ctx:
                se["ctx"] = {ck: str(cv) for ck, cv in ctx.items()} if isinstance(ctx, dict) else str(ctx)
            safe_errors.append(se)
        raise HTTPException(status_code=422, detail=safe_errors)

    try:
        validate_image_file(image)
    except HTTPException:
        raise

    temp_path = Path(settings.image.TEMP_UPLOAD_DIR) / f"{secrets.token_hex(16)}{Path(image.filename).suffix or '.jpg'}"
    try:
        with open(temp_path, "wb") as f:
            shutil.copyfileobj(image.file, f)

        face_app = face_app_singleton.get()
        emb, quality_info = extract_face_embedding(str(temp_path), face_app)
        patients = load_embeddings_and_patients()
        matched, similarity = find_matching_patient(emb, patients, threshold=0.6)

        if matched:
            raise HTTPException(status_code=400, detail=f"Face already registered: {matched.get('full_name')}")

        parsed_dob = reg.date_of_birth if not isinstance(reg.date_of_birth, str) else datetime.fromisoformat(reg.date_of_birth)

        new_id = save_new_patient(
            emb=emb, full_name=reg.full_name, national_id=reg.national_id,
            gender=reg.gender, date_of_birth=parsed_dob.isoformat(),
            phone_number=reg.phone_number, address=reg.address,
            blood_type=reg.blood_type, chronic_diseases=reg.chronic_diseases,
            allergies=reg.allergies, current_medications=reg.current_medications,
            marital_status=reg.marital_status, job=reg.job,
            weight=reg.weight, height=reg.height, BMI=reg.BMI,
        )
        return PatientRegistrationResponse(success=True, patient_id=new_id,
                                           message=f"Patient {reg.full_name} registered",
                                           quality_info=sanitize_value(quality_info))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass


# ── Chatbot Endpoints (UNCHANGED) ──────────────────────────────

@app.post("/chatbot/consultation", dependencies=[Depends(check_rate_limit)])
async def chatbot_consultation(request: ConsultationRequest):
    if not settings.chatbot.OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY not configured")

    model_display = request.model or "GPT-4o Mini 🚀"
    model_id = ModelMapper.get_model_id(model_display)

    try:
        chatbot = get_chatbot()
        result = chatbot.process_consultation(
            patient_id=request.patient_id, symptoms=request.symptoms,
            model_id=model_id, use_rag=request.use_rag, top_k=request.top_k,
            patient_age=request.patient_age, patient_gender=request.patient_gender,
            patient_weight=request.patient_weight, patient_height=request.patient_height,
            chronic_diseases=request.chronic_diseases, allergies=request.allergies,
            current_medications=request.current_medications,
        )
        if hasattr(result, "input_valid") and not result.input_valid:
            result.available_doctors = []
            result.queue_info = None
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Consultation failed: {e}")


@app.get("/chatbot/doctors/{specialty}", dependencies=[Depends(check_rate_limit)])
async def get_doctors_by_specialty(specialty: str):
    try:
        doctors = ChatbotDatabaseManager.get_available_doctors(specialty)
        if not doctors:
            return {"specialty": specialty, "specialty_ar": settings.chatbot.SPECIALTIES.get(specialty, specialty),
                    "available_doctors": [], "total_count": 0}
        return {"specialty": specialty, "specialty_ar": settings.chatbot.SPECIALTIES.get(specialty, specialty),
                "available_doctors": [d.model_dump() for d in doctors], "total_count": len(doctors)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/chatbot/models")
async def get_available_models():
    mapping = ModelMapper.get_mapping()
    return {"models": [{"display_name": d, "model_id": m} for d, m in mapping.items()],
            "default_model": "GPT-4o Mini 🚀"}


# ============================================================================
# INCLUDE NEW ROUTERS
# ============================================================================
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(patient_router)
app.include_router(appointment_router)
app.include_router(doctor_router)
app.include_router(manager_router)
app.include_router(medical_files_router)
app.include_router(pharmacy_router)

# Serve medical files as static files
from fastapi.staticfiles import StaticFiles
_med_dir = Path("medical_files")
_med_dir.mkdir(exist_ok=True)
app.mount("/medical-files", StaticFiles(directory="medical_files"), name="medical_files")

# Serve frontend HTML pages
_frontend_dir = Path("frontend")
_frontend_dir.mkdir(exist_ok=True)
app.mount("/frontend", StaticFiles(directory="frontend", html=True), name="frontend")


# ============================================================================
# WEBSOCKET - Real-time Queue Updates for Flutter App
# ============================================================================
from fastapi import WebSocket, WebSocketDisconnect
from services.appointment_service import get_patient_active_queue, get_queue_status

# Store active WebSocket connections: {patient_id: [websocket1, websocket2, ...]}
ws_connections: Dict[int, list] = {}


@app.websocket("/ws/patient/{patient_id}")
async def websocket_patient_queue(websocket: WebSocket, patient_id: int):
    """
    WebSocket endpoint for Flutter app real-time queue updates.
    Flutter connects to: ws://IP:8004/ws/patient/{patient_id}
    Sends queue_update messages every 10 seconds + on doctor actions.
    """
    await websocket.accept()

    # Register connection
    if patient_id not in ws_connections:
        ws_connections[patient_id] = []
    ws_connections[patient_id].append(websocket)
    logger.info(f"[WS] Patient {patient_id} connected. Total: {len(ws_connections)}")

    try:
        # Send initial status immediately
        await _send_queue_update(websocket, patient_id)

        # Keep connection alive and send periodic updates
        while True:
            try:
                # Wait for client message (ping/pong) or timeout
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                # Client can send "refresh" to force update
                if msg == "refresh":
                    await _send_queue_update(websocket, patient_id)
            except asyncio.TimeoutError:
                # No message from client in 10s - send periodic update
                await _send_queue_update(websocket, patient_id)
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"[WS] Error for patient {patient_id}: {e}")
    finally:
        # Remove connection
        if patient_id in ws_connections:
            ws_connections[patient_id] = [
                ws for ws in ws_connections[patient_id] if ws != websocket
            ]
            if not ws_connections[patient_id]:
                del ws_connections[patient_id]
        logger.info(f"[WS] Patient {patient_id} disconnected. Total: {len(ws_connections)}")


async def _send_queue_update(websocket: WebSocket, patient_id: int):
    """Build and send queue status update to a WebSocket client."""
    try:
        result = get_patient_active_queue(patient_id)
        if result:
            specialty = result.get("doctor_specialty", "")
            message = {
                "type": "queue_update",
                "queue_id": result.get("queue_id"),
                "doctor_id": result.get("doctor_id"),
                "doctor_name": result.get("doctor_name"),
                "specialty": specialty,
                "specialty_ar": result.get("doctor_specialty_ar", specialty),
                "position": result.get("queue_position"),
                "status": result.get("queue_status"),
                "estimated_wait_minutes": result.get("estimated_wait_minutes", 0),
                "patients_ahead": result.get("people_ahead", 0),
                "join_time": result.get("joined_at"),
                "floor_number": str(result.get("doctor_floor", "")),
                "room_number": result.get("doctor_room", ""),
                "your_turn": result.get("your_turn", False),
            }
            # Change type if it's your turn
            if result.get("your_turn"):
                message["type"] = "your_turn"
            elif result.get("queue_status") == "cancelled":
                message["type"] = "cancelled"
        else:
            message = {
                "type": "no_queue",
                "message": "لا يوجد حجز حالياً",
            }
        await websocket.send_json(message)
    except Exception as e:
        logger.error(f"[WS] Failed to send update to patient {patient_id}: {e}")


async def notify_patient_ws(patient_id: int, event_type: str = "queue_update"):
    """
    Call this from doctor_service when queue changes
    to push instant updates to connected Flutter clients.
    """
    if patient_id in ws_connections:
        for ws in ws_connections[patient_id]:
            try:
                await _send_queue_update(ws, patient_id)
            except Exception:
                pass


# ============================================================================
# RUN
# ============================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8004, log_level="info")
