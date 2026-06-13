"""
================================================================================
MediVerse Face Recognition Service
================================================================================
"""

import json
import logging
import traceback
import secrets
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, List, Dict, Any
import shutil
from collections import defaultdict
import time

import numpy as np
import pyodbc
from fastapi import HTTPException, UploadFile
from huggingface_hub import snapshot_download
from insightface.app import FaceAnalysis
from PIL import Image
from pydantic import BaseModel
from logging.handlers import RotatingFileHandler


# ============================================================================
# CONFIGURATION
# ============================================================================

class Config:
    """Enhanced application configuration."""
    
    # Database
    DATABASE_DRIVER = "ODBC Driver 17 for SQL Server"
    DATABASE_SERVER = "localhost"
    DATABASE_NAME = "MediVerse_System"
    DATABASE_USER = ""
    DATABASE_PASSWORD = ""
    DATABASE_POOL_SIZE = 10
    DATABASE_TIMEOUT = 30
    
    # Face Recognition
    FACE_SIMILARITY_THRESHOLD = 0.65
    FACE_MODEL_PATH = "models/auraface"
    MIN_FACE_SIZE = 80
    MAX_FACE_ANGLE = 30
    FACE_QUALITY_THRESHOLD = 0.5
    
    # Image Processing
    MAX_IMAGE_SIZE_MB = 10
    MAX_IMAGE_SIZE_BYTES = MAX_IMAGE_SIZE_MB * 1024 * 1024
    ALLOWED_IMAGE_FORMATS = ['.jpg', '.jpeg', '.png', '.bmp', '.webp']
    TEMP_UPLOAD_DIR = "temp_uploads"
    
    # Security (CORS)
    ALLOWED_ORIGINS = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5500"
    ]
    
    # Rate Limiting
    RATE_LIMIT_CALLS = 100
    RATE_LIMIT_PERIOD = 60
    
    # Logging
    LOG_LEVEL = "INFO"
    LOG_FILE = "mediverse_api.log"
    LOG_MAX_BYTES = 10485760
    LOG_BACKUP_COUNT = 5
    LOG_REQUESTS = True


# ============================================================================
# LOGGING SETUP
# ============================================================================

def setup_logging():
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
    )
    
    file_handler = RotatingFileHandler(
        Config.LOG_FILE,
        maxBytes=Config.LOG_MAX_BYTES,
        backupCount=Config.LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, Config.LOG_LEVEL.upper()))
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger


logger = setup_logging()


# ============================================================================
# RATE LIMITER
# ============================================================================

class RateLimiter:
    """Simple in-memory rate limiter."""
    
    def __init__(self, calls: int, period: int):
        self.calls = calls
        self.period = period
        self.requests = defaultdict(list)
    
    def is_allowed(self, key: str) -> bool:
        now = time.time()
        
        self.requests[key] = [
            req_time for req_time in self.requests[key]
            if now - req_time < self.period
        ]
        
        if len(self.requests[key]) >= self.calls:
            return False
        
        self.requests[key].append(now)
        return True


rate_limiter = RateLimiter(Config.RATE_LIMIT_CALLS, Config.RATE_LIMIT_PERIOD)


# ============================================================================
# DATABASE POOL
# ============================================================================

class DatabasePool:
    def __init__(self, connection_string: str, pool_size: int = 10):
        self.connection_string = connection_string
        self.pool_size = pool_size
        self.connections: List[pyodbc.Connection] = []
        self.in_use: set = set()
        self.created_count = 0
        logger.info(f"Database pool initialized with size: {pool_size}")
    
    def get_connection(self) -> pyodbc.Connection:
        for conn in self.connections:
            if conn not in self.in_use:
                try:
                    conn.timeout = Config.DATABASE_TIMEOUT
                    conn.execute("SELECT 1")
                    self.in_use.add(conn)
                    logger.debug(f"Reusing connection. Active: {len(self.in_use)}/{len(self.connections)}")
                    return conn
                except Exception as e:
                    logger.warning(f"Removing dead connection: {e}")
                    self.connections.remove(conn)
        
        if len(self.connections) < self.pool_size:
            try:
                conn = pyodbc.connect(
                    self.connection_string,
                    timeout=Config.DATABASE_TIMEOUT
                )
                self.connections.append(conn)
                self.in_use.add(conn)
                self.created_count += 1
                logger.info(f"Created new connection #{self.created_count}")
                return conn
            except pyodbc.Error as e:
                logger.error(f"Failed to create database connection: {e}")
                raise HTTPException(status_code=500, detail="Database connection failed")
        
        raise HTTPException(status_code=503, detail="No database connections available")
    
    def release_connection(self, conn: pyodbc.Connection):
        if conn in self.in_use:
            self.in_use.remove(conn)
            logger.debug(f"Released connection. Active: {len(self.in_use)}")
    
    def close_all(self):
        logger.info(f"Closing all {len(self.connections)} database connections")
        for conn in self.connections:
            try:
                conn.close()
            except Exception as e:
                logger.warning(f"Error closing connection: {e}")
        self.connections.clear()
        self.in_use.clear()


db_pool: Optional[DatabasePool] = None


@contextmanager
def get_db_connection():
    if db_pool is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    conn = db_pool.get_connection()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error, rolling back: {e}")
        raise
    finally:
        db_pool.release_connection(conn)


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class PatientOut(BaseModel):
    id: int
    full_name: Optional[str] = None
    national_id: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[str] = None
    address: Optional[str] = None
    blood_type: Optional[str] = None
    phone_number: Optional[str] = None
    chronic_diseases: Optional[str] = None
    allergies: Optional[str] = None
    current_medications: Optional[str] = None
    marital_status: Optional[str] = None
    job: Optional[str] = None
    weight: Optional[float] = None
    height: Optional[float] = None
    BMI: Optional[float] = None


class FaceCheckResponse(BaseModel):
    exists: bool
    similarity: Optional[float] = None
    patient: Optional[PatientOut] = None
    message: str
    quality_info: Optional[Dict[str, Any]] = None


class NationalIdCheckResponse(BaseModel):
    exists: bool
    patient: Optional[PatientOut] = None
    message: str


class PatientRegistrationResponse(BaseModel):
    success: bool
    patient_id: Optional[int] = None
    message: str
    quality_info: Optional[Dict[str, Any]] = None


# ============================================================================
# IMAGE PROCESSING
# ============================================================================

def validate_image_file(file: UploadFile) -> None:
    """Validate uploaded image file."""
    
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    
    if size > Config.MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Image size exceeds {Config.MAX_IMAGE_SIZE_MB}MB limit"
        )
    
    ext = Path(file.filename).suffix.lower()
    if ext not in Config.ALLOWED_IMAGE_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid image format. Allowed: {', '.join(Config.ALLOWED_IMAGE_FORMATS)}"
        )


def check_face_quality(face) -> Dict[str, Any]:
    """Check face detection quality."""
    bbox = face.bbox
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    
    quality_info = {
        "size": min(width, height),
        "quality_score": face.det_score,
        "is_acceptable": True,
        "warnings": []
    }
    
    if min(width, height) < Config.MIN_FACE_SIZE:
        quality_info["is_acceptable"] = False
        quality_info["warnings"].append(f"Face too small ({min(width, height)}px)")
    
    if face.det_score < Config.FACE_QUALITY_THRESHOLD:
        quality_info["warnings"].append(f"Low detection confidence ({face.det_score:.2f})")
    
    return quality_info


def extract_face_embedding(image_path: Path, face_app: FaceAnalysis) -> tuple[np.ndarray, Dict]:
    """Extract face embedding with quality checks."""
    try:
        logger.debug(f"Processing image: {image_path}")
        
        img = Image.open(image_path)
        
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        rgb = np.array(img)
        bgr = rgb[:, :, ::-1]
        
        faces = face_app.get(bgr)
        
        if len(faces) == 0:
            raise HTTPException(
                status_code=400,
                detail="No face detected. Ensure good lighting and face is clearly visible."
            )
        
        if len(faces) > 1:
            logger.warning(f"Multiple faces detected ({len(faces)})")
            raise HTTPException(
                status_code=400,
                detail=f"Multiple faces detected ({len(faces)}). Please provide image with single face."
            )
        
        face = faces[0]
        quality_info = check_face_quality(face)
        
        if not quality_info["is_acceptable"]:
            raise HTTPException(
                status_code=400,
                detail=f"Face quality check failed: {', '.join(quality_info['warnings'])}"
            )
        
        emb = np.array(face.normed_embedding, dtype=np.float32)
        
        logger.info(f"Successfully extracted embedding (quality: {quality_info['quality_score']:.2f})")
        return emb, quality_info
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to process image: {e}")
        raise HTTPException(status_code=400, detail=f"Image processing failed: {str(e)}")


# ============================================================================
# BUSINESS LOGIC
# ============================================================================

def load_embeddings_and_patients() -> List[Dict]:
    """Load patient embeddings from database."""
    logger.info("Loading patient embeddings from database")
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, full_name, national_id, gender, date_of_birth, address,
                       blood_type, phone_number, chronic_diseases, allergies,
                       current_medications, marital_status, job, weight, height, BMI,
                       face_embedding
                FROM Patients WITH (NOLOCK)
                WHERE face_embedding IS NOT NULL
                """
            )
            rows = cursor.fetchall()
            cursor.close()
        
        patients = []
        
        for row in rows:
            try:
                emb_list = json.loads(row.face_embedding)
                emb = np.array(emb_list, dtype=np.float32)
                
                if emb.shape[0] != 512:
                    continue
                
                patients.append({
                    "id": row.id,
                    "full_name": row.full_name,
                    "national_id": row.national_id,
                    "gender": row.gender,
                    "date_of_birth": row.date_of_birth,
                    "address": row.address,
                    "blood_type": row.blood_type,
                    "phone_number": row.phone_number,
                    "chronic_diseases": row.chronic_diseases,
                    "allergies": row.allergies,
                    "current_medications": row.current_medications,
                    "marital_status": row.marital_status,
                    "job": row.job,
                    "weight": row.weight,
                    "height": row.height,
                    "BMI": row.BMI,
                    "embedding": emb,
                })
            except Exception as e:
                logger.warning(f"Failed to parse patient {row.id}: {e}")
                continue
        
        logger.info(f"Loaded {len(patients)} valid patient embeddings")
        return patients
        
    except Exception as e:
        logger.error(f"Failed to load patient embeddings: {e}")
        raise HTTPException(status_code=500, detail="Failed to load patient data")


def find_matching_patient(
    new_emb: np.ndarray,
    patients: List[Dict],
    threshold: Optional[float] = None
) -> tuple[Optional[Dict], float]:
    """Find matching patient with similarity score."""
    if len(patients) == 0:
        return None, 0.0
    
    if threshold is None:
        threshold = Config.FACE_SIMILARITY_THRESHOLD
    
    new_emb = new_emb / np.linalg.norm(new_emb)
    
    best_patient = None
    best_sim = -1.0
    
    for p in patients:
        emb = p["embedding"] / np.linalg.norm(p["embedding"])
        sim = float(np.dot(new_emb, emb))
        
        if sim > best_sim:
            best_sim = sim
            best_patient = p
    
    if best_sim >= threshold:
        logger.info(f"Match found: Patient ID {best_patient['id']} (similarity: {best_sim:.3f})")
        return best_patient, best_sim
    else:
        logger.info(f"No match found. Best similarity: {best_sim:.3f}")
        return None, best_sim


def get_patient_by_national_id(national_id: str) -> Optional[Dict]:
    """Get patient by national ID."""
    logger.info(f"Searching for patient with national_id: {national_id}")
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, full_name, national_id, gender, date_of_birth, address,
                       blood_type, phone_number, chronic_diseases, allergies,
                       current_medications, marital_status, job, weight, height, BMI
                FROM Patients WITH (NOLOCK)
                WHERE national_id = ?
                """,
                (national_id,)
            )
            row = cursor.fetchone()
            cursor.close()
        
        if row:
            logger.info(f"Patient found: ID={row.id}, Name={row.full_name}")
            return {
                "id": row.id,
                "full_name": row.full_name,
                "national_id": row.national_id,
                "gender": row.gender,
                "date_of_birth": row.date_of_birth,
                "address": row.address,
                "blood_type": row.blood_type,
                "phone_number": row.phone_number,
                "chronic_diseases": row.chronic_diseases,
                "allergies": row.allergies,
                "current_medications": row.current_medications,
                "marital_status": row.marital_status,
                "job": row.job,
                "weight": row.weight,
                "height": row.height,
                "BMI": row.BMI,
            }
        else:
            logger.info(f"No patient found with national_id: {national_id}")
            return None
        
    except Exception as e:
        logger.error(f"Failed to get patient by national_id: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve patient data")


def save_new_patient(
    emb: np.ndarray,
    full_name: str,
    national_id: str,
    gender: str,
    date_of_birth: str,
    phone_number: str,
    address: Optional[str] = None,
    blood_type: Optional[str] = None,
    chronic_diseases: Optional[str] = None,
    allergies: Optional[str] = None,
    current_medications: Optional[str] = None,
    marital_status: Optional[str] = None,
    job: Optional[str] = None,
    weight: Optional[float] = None,
    height: Optional[float] = None,
    BMI: Optional[float] = None
) -> int:
    """Save new patient to database."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT id FROM Patients WHERE national_id = ?",
                (national_id,)
            )
            existing = cursor.fetchone()
            
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Patient with national ID {national_id} already exists"
                )
            
            emb_json = json.dumps(emb.tolist())
            
            cursor.execute(
                """
                INSERT INTO Patients
                (face_embedding, full_name, national_id, gender, date_of_birth,
                 address, blood_type, phone_number, chronic_diseases,
                 allergies, current_medications, marital_status, job, weight, height, BMI)
                OUTPUT INSERTED.id
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (emb_json, full_name, national_id, gender, date_of_birth,
                 address, blood_type, phone_number, chronic_diseases,
                 allergies, current_medications, marital_status, job, weight, height, BMI)
            )
            
            new_id = cursor.fetchone()[0]
            cursor.close()
        
        logger.info(f"Registered new patient: ID={new_id}")
        return new_id
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to save patient: {e}")
        raise HTTPException(status_code=500, detail="Failed to register patient")


# ============================================================================
# INITIALIZATION FUNCTIONS
# ============================================================================

def init_database():
    """Initialize database pool."""
    global db_pool
    
    if Config.DATABASE_USER:
        conn_str = (
            f"DRIVER={{{Config.DATABASE_DRIVER}}};"
            f"SERVER={Config.DATABASE_SERVER};"
            f"DATABASE={Config.DATABASE_NAME};"
            f"UID={Config.DATABASE_USER};"
            f"PWD={Config.DATABASE_PASSWORD};"
            "Encrypt=yes;"
        )
    else:
        conn_str = (
            f"DRIVER={{{Config.DATABASE_DRIVER}}};"
            f"SERVER={Config.DATABASE_SERVER};"
            f"DATABASE={Config.DATABASE_NAME};"
            "Trusted_Connection=yes;"
            "Encrypt=no;"
        )
    
    db_pool = DatabasePool(conn_str, pool_size=Config.DATABASE_POOL_SIZE)


def init_face_model() -> FaceAnalysis:
    """Initialize face recognition model."""
    model_path = Path(Config.FACE_MODEL_PATH)
    if not model_path.exists():
        logger.info("Downloading face recognition model...")
        snapshot_download("fal/AuraFace-v1", local_dir=Config.FACE_MODEL_PATH)
    
    face_app = FaceAnalysis(
        name="auraface",
        providers=["CPUExecutionProvider"],
        root="."
    )
    face_app.prepare(ctx_id=0, det_size=(640, 640))
    
    return face_app


def cleanup():
    """Cleanup resources."""
    if db_pool:
        db_pool.close_all()
    
    try:
        shutil.rmtree(Config.TEMP_UPLOAD_DIR, ignore_errors=True)
    except:
        pass