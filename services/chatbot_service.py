# hospital_chatbot_improved.py
"""
Hospital Chatbot - Final integrated version with DB fixes (no connection leaks),
corrected column names, safe index access, and maintenance utilities.

Notes:
- All database access uses `with ImprovedDatabaseManager.get_connection() as conn:` 
  to ensure automatic commit/rollback and proper connection closing.
- Column names for patient snapshots use lowercase: chronic_diseases, allergies, current_medications.
- Safe index access for rows that may or may not contain estimated_wait_minutes.
- Maintenance, integrity, and scheduler utilities included.
- Added input validation for symptoms (is_valid_symptoms) and `input_valid` flag in ConsultationResult.
"""

import os
import re
import json
import time
import logging
from typing import List, Optional, Dict, Literal, Any
from datetime import datetime, timedelta
from functools import lru_cache, wraps
from dataclasses import dataclass
from contextlib import contextmanager
from collections import defaultdict
from threading import Thread

import numpy as np
import pyodbc
import requests
from pydantic import BaseModel, Field, field_validator, validator
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

# Optional third-party imports
try:
    from sqlalchemy import create_engine, pool
except Exception:
    create_engine = None
try:
    import redis
except Exception:
    redis = None
try:
    from tenacity import retry, stop_after_attempt, wait_exponential
except Exception:
    retry = None
try:
    import bleach
except Exception:
    bleach = None
try:
    import aiohttp
except Exception:
    aiohttp = None
try:
    import schedule
except Exception:
    schedule = None

load_dotenv()

# -------------------------
# Logging Configuration
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# -------------------------
# Configuration Class
# -------------------------
class ChatbotConfig:
    DB_SERVER = os.getenv("DATABASE_SERVER", "localhost")
    DB_NAME = os.getenv("DATABASE_NAME", "MediVerse_System")
    DB_USER = os.getenv("DATABASE_USER", "")
    DB_PASSWORD = os.getenv("DATABASE_PASSWORD", "")
    DB_DRIVER = os.getenv("DATABASE_DRIVER", "ODBC Driver 17 for SQL Server")

    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    OPENROUTER_URL = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")

    EMBED_MODEL = os.getenv("EMBED_MODEL_NAME", "paraphrase-multilingual-MiniLM-L12-v2")

    RAG_THRESHOLD = float(os.getenv("RAG_THRESHOLD", 0.55))
    RAG_TOP_K = int(os.getenv("RAG_TOP_K", 5))

    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

    MODEL_DISPLAY_NAMES = {
        "openai/gpt-4o": "GPT-4o 🧠",
        "openai/gpt-4o-mini": "GPT-4o Mini 🚀",
        "google/gemini-pro-1.5": "Gemini Pro 1.5 💎",
        "anthropic/claude-3-haiku": "Claude 3 Haiku ⚡",
        "anthropic/claude-3.5-sonnet": "Claude 3.5 Sonnet 🎭",
        "meta-llama/llama-3.1-70b-instruct": "Llama 3.1 70B 🦙",
        "mistralai/mixtral-8x7b-instruct": "Mixtral 8x7B 🌟"
    }

    SPECIALTIES = {
        "General Practitioner": "طب أسرة / ممارس عام",
        "Family Medicine": "طب الأسرة",
        "Neurology": "طب المخ والأعصاب",
        "Cardiology": "أمراض القلب",
        "Orthopedics": "طب العظام",
        "Pulmonology": "أمراض الصدر",
        "Pediatrics": "طب الأطفال",
        "Emergency Medicine": "طب الطوارئ",
        "Ophthalmology": "طب العيون",
        "Dermatology": "الأمراض الجلدية",
        "Internal Medicine": "الباطنة",
        "Psychiatry": "الطب النفسي",
    }

config = ChatbotConfig()

# -------------------------
# Pydantic Data Models
# -------------------------
class PatientInfo(BaseModel):
    id: int
    name: str
    age: Optional[int] = None
    gender: Optional[str] = None
    marital_status: Optional[str] = None
    job: Optional[str] = None
    weight: Optional[float] = None
    height: Optional[float] = None
    bmi: Optional[float] = None
    chronic_diseases: List[str] = Field(default_factory=list)
    allergies: List[str] = Field(default_factory=list)
    current_medications: List[str] = Field(default_factory=list)

    @field_validator('bmi')
    def validate_bmi(cls, v, info):
        if v is None:
            weight = info.data.get('weight')
            height = info.data.get('height')
            if weight and height and height > 0:
                height_m = height / 100.0
                return round(weight / (height_m ** 2), 1)
        return v

    def get_bmi_category(self) -> str:
        if self.bmi is None:
            return "غير محدد"
        if self.bmi < 18.5:
            return "نحيف"
        elif self.bmi < 25:
            return "وزن طبيعي"
        elif self.bmi < 30:
            return "زيادة وزن"
        else:
            return "يسمنة"

class SeverityAssessment(BaseModel):
    level: int = Field(..., ge=1, le=10, description="1=minor, 10=critical")
    label: Literal["minor", "moderate", "serious", "critical"]
    reasoning: str
    emergency_required: bool

    @field_validator('label')
    def validate_label_matches_level(cls, v, info):
        level = info.data.get('level', 5)
        expected = "minor" if level <= 3 else "moderate" if level <= 6 else "serious" if level <= 8 else "critical"
        if v != expected:
            return expected
        return v

class MedicalAssessment(BaseModel):
    preliminary_diagnosis: str
    severity: SeverityAssessment
    first_aid: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    specialty_required: str
    alternative_specialties: List[str] = Field(default_factory=list)

    @field_validator('specialty_required', 'alternative_specialties')
    def validate_specialty(cls, v):
        allowed = list(config.SPECIALTIES.keys())
        if isinstance(v, str):
            if v and v not in allowed:
                logger.warning(f"Unknown specialty: {v}")
                return "General Practitioner"
            return v
        elif isinstance(v, list):
            return [s for s in v if s in allowed]
        return v

class DoctorInfo(BaseModel):
    id: int
    name: str
    specialty: str
    specialty_ar: str
    rating: float
    floor: int
    room: str
    current_patients: int
    estimated_wait_minutes: int
    current_status: str = "available"

class RAGMatch(BaseModel):
    symptoms: str
    diagnosis: str
    specialty: str
    similarity: float
    first_aid: List[str] = Field(default_factory=list)

class ConsultationResult(BaseModel):
    consultation_id: int = Field(default=0, description="Database ID of saved consultation (0 if save failed)")
    patient_id: int
    assessment: MedicalAssessment
    available_doctors: List[DoctorInfo]
    rag_matches: List[RAGMatch] = Field(default_factory=list)
    confidence: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    processing_time_ms: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    model_used: str = Field(default="", description="Display name of AI model used")
    model_id: str = Field(default="", description="Internal model ID")
    queue_info: Optional[Dict[str, Any]] = None
    input_valid: bool = Field(default=True, description="Whether the provided symptoms text is valid (True) or not (False)")
    first_aid_source: str = Field(
        default="llm", 
        description="rag|llm|hybrid"
    )
    rag_first_aid_used: bool = Field(
        default=False,
        description="هل تم استخدام first_aid من الراج"
    )

# -------------------------
# Improved Database Manager (SQLAlchemy pooling)
# -------------------------
class ImprovedDatabaseManager:
    _engine = None

    @classmethod
    def get_engine(cls):
        if create_engine is None:
            raise RuntimeError("SQLAlchemy is required for ImprovedDatabaseManager")
        if cls._engine is None:
            user_pass = ""
            if config.DB_USER and config.DB_PASSWORD:
                user_pass = f"{config.DB_USER}:{config.DB_PASSWORD}@"
            conn_tpl = f"mssql+pyodbc://{user_pass}{config.DB_SERVER}/{config.DB_NAME}"
            driver = config.DB_DRIVER.replace(" ", "+")
            connection_string = f"{conn_tpl}?driver={driver}"
            cls._engine = create_engine(
                connection_string,
                poolclass=pool.QueuePool,
                pool_size=10,
                max_overflow=20,
                pool_timeout=30,
                pool_recycle=3600,
                echo=False
            )
        return cls._engine

    @classmethod
    @contextmanager
    def get_connection(cls):
        """
        Yield raw DB-API connection (pyodbc connection object) and commit/rollback automatically.
        Use as: with ImprovedDatabaseManager.get_connection() as conn:
        """
        engine = cls.get_engine()
        conn = engine.raw_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @classmethod
    def acquire_connection(cls):
        """Return raw connection (use carefully). Prefer get_connection context manager."""
        engine = cls.get_engine()
        return engine.raw_connection()

# -------------------------
# Backwards-compatible Database Wrapper (with fixed methods)
# -------------------------
class ChatbotDatabaseManager:
    """
    Wrapper exposing DB helper methods. All methods use ImprovedDatabaseManager.get_connection()
    to ensure no connection leaks.
    """

    @staticmethod
    def fetch_patient(patient_id: int) -> Optional[PatientInfo]:
        """
        FIXED: Use lowercase column names and proper connection handling
        """
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, full_name, date_of_birth, gender,
                           marital_status, job, weight, height, BMI,
                           chronic_diseases, allergies, current_medications
                    FROM Patients WHERE id = ?
                """, (patient_id,))
                row = cursor.fetchone()
                if not row:
                    return None

                age = None
                if row[2]:
                    dob = row[2] if isinstance(row[2], datetime) else row[2]
                    today = datetime.utcnow()
                    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

                return PatientInfo(
                    id=row[0],
                    name=row[1],
                    age=age,
                    gender=row[3],
                    marital_status=row[4],
                    job=row[5],
                    weight=float(row[6]) if row[6] else None,
                    height=float(row[7]) if row[7] else None,
                    bmi=float(row[8]) if row[8] else None,
                    chronic_diseases=(row[9] or "").split(" | ") if row[9] else [],
                    allergies=(row[10] or "").split(" | ") if row[10] else [],
                    current_medications=(row[11] or "").split(" | ") if row[11] else []
                )
        except Exception as e:
            logger.error(f"Failed to fetch patient {patient_id}: {e}")
            return None

    @staticmethod
    def save_consultation(
        patient_id: int,
        symptoms: str,
        assessment: MedicalAssessment,
        patient: PatientInfo,
        rag_matches: List[RAGMatch],
        model_id: str,
        processing_time_ms: int,
        session_id: Optional[int] = None,
        ai_confidence_score: float = 0.0,
        actual_diagnosis: Optional[str] = None,
        was_ai_correct: Optional[bool] = False,
        doctor_id: Optional[int] = None,
        doctor_notes: Optional[str] = None
    ) -> int:
        """
        FIXED: Use context manager for proper connection handling
        """
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                age_at_consultation = patient.age if patient.age is not None else 0
                chronic_snapshot = " | ".join(patient.chronic_diseases)
                meds_snapshot = " | ".join(patient.current_medications)
                allergies_snapshot = " | ".join(patient.allergies)
                first_aid_json = json.dumps(assessment.first_aid, ensure_ascii=False)
                rag_json = json.dumps([m.model_dump() for m in rag_matches], ensure_ascii=False)

                try:
                    cursor.execute("""
                        EXEC sp_InsertPatientConsultation
                            @patient_id = ?,
                            @session_id = ?,
                            @symptoms_reported = ?,
                            @patient_age_at_consultation = ?,
                            @chronic_diseases_snapshot = ?,
                            @current_meds_snapshot = ?,
                            @allergies_snapshot = ?,
                            @ai_preliminary_diagnosis = ?,
                            @ai_confidence_score = ?,
                            @ai_severity_assessment = ?,
                            @ai_specialty_suggested = ?,
                            @ai_first_aid_given = ?,
                            @rag_top_matches = ?,
                            @actual_diagnosis = ?,
                            @was_ai_correct = ?,
                            @doctor_id = ?,
                            @doctor_notes = ?,
                            @llm_model_used = ?,
                            @processing_time_ms = ?
                    """, (
                        patient_id, session_id, symptoms, age_at_consultation,
                        chronic_snapshot, meds_snapshot, allergies_snapshot,
                        assessment.preliminary_diagnosis,
                        float(ai_confidence_score or 0.0),
                        assessment.severity.level,
                        assessment.specialty_required,
                        first_aid_json, rag_json,
                        actual_diagnosis,
                        1 if was_ai_correct else 0,
                        doctor_id, doctor_notes,
                        model_id, processing_time_ms
                    ))
                    row = cursor.fetchone()
                    if row and row[0] is not None:
                        return int(row[0])
                except Exception as sp_error:
                    logger.warning(f"Stored procedure failed, trying direct insert: {sp_error}")
                    cursor.execute("""
                        INSERT INTO patient_consultations (
                            patient_id, session_id, symptoms_reported, patient_age_at_consultation,
                            chronic_diseases_snapshot, current_meds_snapshot, allergies_snapshot,
                            ai_preliminary_diagnosis, ai_confidence_score, ai_severity_assessment,
                            ai_specialty_suggested, ai_first_aid_given, rag_top_matches,
                            actual_diagnosis, was_ai_correct, doctor_id, doctor_notes, 
                            consultation_date, llm_model_used, processing_time_ms
                        ) OUTPUT INSERTED.consultation_id
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, GETDATE(), ?, ?)
                    """, (
                        patient_id, session_id, symptoms, age_at_consultation,
                        chronic_snapshot, meds_snapshot, allergies_snapshot,
                        assessment.preliminary_diagnosis,
                        float(ai_confidence_score or 0.0),
                        assessment.severity.level,
                        assessment.specialty_required,
                        first_aid_json, rag_json,
                        actual_diagnosis,
                        1 if was_ai_correct else 0,
                        doctor_id, doctor_notes,
                        model_id, processing_time_ms
                    ))
                    row = cursor.fetchone()
                    if row and row[0] is not None:
                        return int(row[0])
                return 0
        except Exception as e:
            logger.error(f"Failed to save consultation: {e}")
            return 0

    @staticmethod
    def get_available_doctors(specialty: str, alternatives: List[str] = None) -> List[DoctorInfo]:
        """
        FIXED: Safer index access and context manager
        """
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                specialties = [specialty] + (alternatives or [])
                placeholders = ",".join(["?"] * len(specialties))

                cursor.execute(f"""
                    SELECT doctor_id, doctor_name, specialty, room_number, 
                           floor_number, current_patients_count, 
                           average_consultation_minutes, rating,
                           estimated_wait_minutes, current_status
                    FROM vw_AvailableDoctors
                    WHERE specialty IN ({placeholders})
                      AND current_status IN ('available', 'busy', 'on_break')
                      AND ISNULL(is_online, 0) = 1
                    ORDER BY 
                        CASE WHEN specialty = ? THEN 0 ELSE 1 END,
                        rating DESC,
                        current_patients_count ASC
                """, tuple(specialties) + (specialty,))

                doctors = []
                for row in cursor.fetchall():
                    # Safe index access
                    doctors.append(DoctorInfo(
                        id=row[0],
                        name=row[1],
                        specialty=row[2],
                        specialty_ar=config.SPECIALTIES.get(row[2], row[2]),
                        room=row[3] or "N/A",
                        floor=row[4] or 0,
                        current_patients=row[5] or 0,
                        estimated_wait_minutes=(row[8] if len(row) > 8 and row[8] else 0),
                        rating=float(row[7]) if row[7] else 0.0,
                        current_status=row[9] or "available"
                    ))
                return doctors
        except Exception as e:
            logger.error(f"Failed to get doctors: {e}")
            return []

# -------------------------
# Cache Manager (Redis)
# -------------------------
class CacheManager:
    """Redis-backed caching for patient records."""
    def __init__(self):
        self.redis_client = None
        if redis is None:
            logger.warning("redis package not available; cache disabled")
            return
        try:
            client = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, decode_responses=True)
            client.ping()
            self.redis_client = client
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}")
            self.redis_client = None

    def get_patient_cache(self, patient_id: int) -> Optional[dict]:
        if not self.redis_client:
            return None
        key = f"patient:{patient_id}"
        try:
            data = self.redis_client.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            logger.warning(f"Redis get error: {e}")
            return None

    def set_patient_cache(self, patient_id: int, data: dict, ttl: int = 3600):
        if not self.redis_client:
            return
        key = f"patient:{patient_id}"
        try:
            self.redis_client.setex(key, ttl, json.dumps(data, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"Redis set error: {e}")

cache_mgr = CacheManager()

# -------------------------
# Input Sanitization (SecurePatientInfo)
# -------------------------
class SecurePatientInfo(PatientInfo):
    """Sanitize string fields to avoid injection and problematic characters."""
    @validator('name')
    def sanitize_name(cls, v):
        if v:
            cleaned = v
            if bleach:
                cleaned = bleach.clean(cleaned, strip=True)
            cleaned = re.sub(r'[^\w\s\u0600-\u06FF\-]', '', cleaned)
            return cleaned
        return v

    @validator('chronic_diseases', 'allergies', 'current_medications', each_item=True)
    def sanitize_list_items(cls, v):
        if v:
            cleaned = v
            if bleach:
                cleaned = bleach.clean(cleaned, strip=True)
            cleaned = re.sub(r'[<>\"\'&]', '', cleaned)
            return cleaned
        return v

# -------------------------
# RAG Service (Embedding + Search) with pagination
# -------------------------
class RAGService:
    _loaded_model = None

    def __init__(self):
        if RAGService._loaded_model is None:
            RAGService._loaded_model = self._load_model()
        self.model = RAGService._loaded_model

    @staticmethod
    def _load_model():
        try:
            model = SentenceTransformer(config.EMBED_MODEL)
            return model
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            return None

    def embed(self, text: str) -> np.ndarray:
        if not self.model:
            return np.zeros(384, dtype=np.float32)
        try:
            emb = self.model.encode([text], show_progress_bar=False)[0]
            return np.asarray(emb, dtype=np.float32)
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            return np.zeros((384,), dtype=np.float32)

    def search(self, query: str, top_k: int = 5) -> List[RAGMatch]:
        """جلب first_aid من قاعدة البيانات"""
        query_emb = self.embed(query)
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                # ✅ إضافة first_aid_steps للـ SELECT
                cursor.execute("""
                    SELECT symptoms_text, probable_diagnosis, specialty_required, 
                           symptoms_embedding_bytes, first_aid_steps
                    FROM symptoms_knowledge_base
                """)
                results: List[RAGMatch] = []
                for row in cursor.fetchall():
                    try:
                        emb = np.frombuffer(row[3], dtype=np.float32)
                        if emb.size == 0:
                            continue
                        qnorm = np.linalg.norm(query_emb) + 1e-8
                        enx = np.linalg.norm(emb) + 1e-8
                        sim = np.dot(query_emb, emb) / (qnorm * enx)
                        
                        if sim >= config.RAG_THRESHOLD:
                            # ✅ تحويل first_aid من JSON string
                            first_aid_list = []
                            if row[4]:  # first_aid_steps column
                                try:
                                    first_aid_list = json.loads(row[4])
                                except:
                                    first_aid_list = []
                            
                            results.append(RAGMatch(
                                symptoms=row[0], 
                                diagnosis=row[1], 
                                specialty=row[2], 
                                similarity=float(sim),
                                first_aid=first_aid_list  # ✅ من الراج
                            ))
                    except Exception:
                        continue
                results.sort(key=lambda x: x.similarity, reverse=True)
                return results[:top_k]
        except Exception as e:
            logger.error(f"RAG search failed: {e}")
            return []

    def search_paginated(self, query: str, page: int = 1, page_size: int = 100, top_k: int = 5) -> List[RAGMatch]:
        """FIXED: Use context manager"""
        query_emb = self.embed(query)
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                offset = (page - 1) * page_size
                cursor.execute("""
                    SELECT symptoms_text, probable_diagnosis, specialty_required, symptoms_embedding_bytes
                    FROM symptoms_knowledge_base
                    ORDER BY id
                    OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
                """, (offset, page_size))
                results: List[RAGMatch] = []
                for row in cursor.fetchall():
                    try:
                        emb = np.frombuffer(row[3], dtype=np.float32)
                        if emb.size == 0:
                            continue
                        qnorm = np.linalg.norm(query_emb) + 1e-8
                        enx = np.linalg.norm(emb) + 1e-8
                        sim = np.dot(query_emb, emb) / (qnorm * enx)
                        if sim >= config.RAG_THRESHOLD:
                            results.append(RAGMatch(symptoms=row[0], diagnosis=row[1], specialty=row[2], similarity=float(sim)))
                    except Exception as e:
                        logger.warning(f"Failed to process embedding row: {e}")
                        continue
                results.sort(key=lambda x: x.similarity, reverse=True)
                return results[:top_k]
        except Exception as e:
            logger.error(f"RAG paginated search failed: {e}")
            return []

# -------------------------
# Metrics & Monitoring
# -------------------------
@dataclass
class PerformanceMetrics:
    operation: str
    duration_ms: float
    success: bool
    error: Optional[str] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()

class MetricsCollector:
    def __init__(self):
        self.metrics: List[PerformanceMetrics] = []

    def track_operation(self, operation: str):
        class OperationTracker:
            def __init__(self, collector, op_name):
                self.collector = collector
                self.operation = op_name
                self.start_time = None

            def __enter__(self):
                self.start_time = time.time()
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                duration = (time.time() - self.start_time) * 1000
                metric = PerformanceMetrics(
                    operation=self.operation,
                    duration_ms=duration,
                    success=exc_type is None,
                    error=str(exc_val) if exc_val else None
                )
                self.collector.metrics.append(metric)
                return False
        return OperationTracker(self, operation)

    def get_stats(self, operation: Optional[str] = None) -> Dict:
        relevant = [m for m in self.metrics if operation is None or m.operation == operation]
        if not relevant:
            return {}
        durations = [m.duration_ms for m in relevant]
        successes = [m.success for m in relevant]
        return {
            "count": len(relevant),
            "success_rate": sum(successes) / len(successes),
            "avg_duration_ms": sum(durations) / len(durations),
            "min_duration_ms": min(durations),
            "max_duration_ms": max(durations),
            "p95_duration_ms": float(np.percentile(durations, 95))
        }

metrics = MetricsCollector()

# -------------------------
# Improved LLM Service (retry + robust parsing)
# -------------------------
class ImprovedLLMService:
    @staticmethod
    def _retry_decorator():
        if retry is None:
            def _identity_decorator(func):
                @wraps(func)
                def wrapper(*args, **kwargs):
                    return func(*args, **kwargs)
                return wrapper
            return _identity_decorator
        return retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))

    @staticmethod
    @_retry_decorator()
    def call_llm_with_retry(prompt: str, api_key: str, model_id: str) -> str:
        try:
            response = requests.post(
                config.OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model_id,
                    "messages": [
                        {"role": "system", "content": LLMService.SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.0,
                    "max_tokens": 800
                },
                timeout=60
            )
            if response.status_code == 429:
                raise Exception("Rate limit exceeded")
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except requests.exceptions.Timeout:
            logger.error("LLM request timeout")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"LLM request failed: {e}")
            raise

    @staticmethod
    def parse_response_robust(response: str) -> Dict:
        try:
            data = json.loads(response.strip())
            return ImprovedLLMService.validate_response(data)
        except json.JSONDecodeError:
            import re
            matches = re.findall(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if matches:
                try:
                    data = json.loads(matches[0])
                    return ImprovedLLMService.validate_response(data)
                except Exception:
                    pass
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                    return ImprovedLLMService.validate_response(data)
                except Exception:
                    pass
            logger.error(f"Failed to parse LLM response (first 200 chars): {response[:200]}")
            return ImprovedLLMService.get_default_response()

    @staticmethod
    def validate_response(data: Dict) -> Dict:
        required = ["preliminary_diagnosis", "severity_level", "specialty_required"]
        for field in required:
            if field not in data:
                raise ValueError(f"Missing required field: {field}")
        try:
            data["severity_level"] = max(1, min(10, int(data.get("severity_level", 5))))
        except Exception:
            data["severity_level"] = 5
        if data.get("specialty_required") not in config.SPECIALTIES:
            logger.warning(f"Invalid specialty: {data.get('specialty_required')}")
            data["specialty_required"] = "General Practitioner"
        return data

    @staticmethod
    def get_default_response() -> Dict:
        return {
            "input_valid": True,
            "preliminary_diagnosis": "يرجى مراجعة طبيب عام لتقييم الحالة",
            "severity_level": 5,
            "severity_reasoning": "لم نتمكن من تحليل الأعراض بدقة",
            "first_aid": ["اتصل بخدمة الطوارئ إذا كانت الأعراض شديدة"],
            "warnings": ["⚠️ هذا التقييم افتراضي - يرجى استشارة طبيب"],
            "specialty_required": "General Practitioner",
            "alternative_specialties": []
        }

# -------------------------
# Async LLM Service (optional)
# -------------------------
class AsyncLLMService:
    """Async LLM caller using aiohttp if installed."""
    @staticmethod
    async def call_llm_async(prompt: str, api_key: str, model_id: str) -> str:
        if aiohttp is None:
            raise RuntimeError("aiohttp not installed")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                config.OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model_id,
                    "messages": [
                        {"role": "system", "content": LLMService.SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.0,
                    "max_tokens": 800
                },
                timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status != 200:
                    raise Exception(f"LLM API error: {response.status}")
                data = await response.json()
                return data["choices"][0]["message"]["content"]

# -------------------------
# Rate Limiter
# -------------------------
class RateLimiter:
    """In-memory rate limiter for simple usage. Replace with Redis for distributed systems."""
    def __init__(self):
        self.requests = defaultdict(list)
        self.max_requests = 100
        self.window = timedelta(hours=1)

    def is_allowed(self, identifier: str) -> bool:
        now = datetime.utcnow()
        self.requests[identifier] = [t for t in self.requests[identifier] if now - t < self.window]
        if len(self.requests[identifier]) >= self.max_requests:
            return False
        self.requests[identifier].append(now)
        return True

    def get_remaining(self, identifier: str) -> int:
        return max(0, self.max_requests - len(self.requests[identifier]))

rate_limiter = RateLimiter()

# -------------------------
# LLM Prompt Builder
# -------------------------
class LLMService:
    SYSTEM_PROMPT = """You are a medical AI assistant. Analyze symptoms considering ALL patient context.

FIRST: Validate if input is medical symptoms:
- If input is greeting (مرحباً, hello, hi), conversation, or non-medical → return validation error
- If input is too vague or nonsensical → return validation error

For VALID medical symptoms, return this JSON:

{
  "input_valid": true,
  "preliminary_diagnosis": "diagnosis in Arabic",
  "severity_level": 5,
  "severity_reasoning": "reasoning in Arabic",
  "first_aid": ["step 1 in Arabic", "step 2"],
  "warnings": ["warning 1 in Arabic"],
  "specialty_required": "specialty name in English from list",
  "alternative_specialties": ["alt specialty"]
}

For INVALID input (not medical symptoms), return this JSON:

{
  "input_valid": false,
  "validation_message": "explain in Arabic why input is invalid",
  "preliminary_diagnosis": "الرجاء إدخال وصف واضح للأعراض التي تعاني منها",
  "severity_level": 1,
  "severity_reasoning": "لا يمكن التقييم بدون أعراض واضحة",
  "first_aid": [],
  "warnings": ["الرجاء وصف الأعراض بوضوح (مثل: ألم، حمى، سعال، إلخ)"],
  "specialty_required": "General Practitioner",
  "alternative_specialties": []
}

Examples of INVALID inputs:
- "مرحباً" → not symptoms
- "hello" → greeting
- "ساعدني" → too vague
- "123456" → nonsense
- "كيف حالك؟" → conversation

Examples of VALID inputs:
- "ألم في الصدر" → valid
- "طفح جلدي مع حكة" → valid  
- "صداع منذ يومين" → valid
- "حمى وسعال" → valid

Allowed specialties (use EXACTLY these names):
["General Practitioner", "Family Medicine", "Neurology", "Cardiology", 
 "Orthopedics", "Pulmonology", "Pediatrics", "Emergency Medicine",
 "Ophthalmology", "Dermatology", "Internal Medicine", "Psychiatry"]

CRITICAL SPECIALTY SELECTION RULES:

1. Emergency Medicine ONLY for immediate life-threatening conditions:
   - Active external bleeding (trauma, injury)
   - Loss of consciousness or altered mental state
   - Severe trauma or accidents
   - Acute cardiac emergency with chest pain radiating to arm/jaw
   - Severe respiratory distress (unable to breathe, choking)
   - Stroke symptoms (facial droop, arm weakness, slurred speech)
   - Anaphylaxis or severe allergic reaction

2. Internal Medicine for GI/abdominal emergencies (even if severe):
   - Abdominal pain (even severe) with vomiting
   - Gastrointestinal bleeding (blood in vomit/stool, black stool)
   - Jaundice (yellowing of skin/eyes)
   - Pancreatitis symptoms
   - Peptic ulcer with complications
   - Liver or gallbladder issues
   
   → PRIMARY: Internal Medicine
   → ALTERNATIVE: Emergency Medicine (only if also life-threatening)

3. Organ system determines specialty, NOT severity alone:
   - Severe abdominal pain → Internal Medicine (NOT Emergency)
   - Severe chest pain (cardiac) → Emergency Medicine
   - Severe joint pain → Orthopedics (NOT Emergency)

Important:
- All explanations in Arabic
- Severity level: 1-10 (1=minor, 10=critical)
- Consider: age, gender, BMI, job risks, chronic diseases, allergies, medications
- BMI categories: <18.5=underweight, 18.5-24.9=normal, 25-29.9=overweight, >=30=obese
- If severity >= 8, mention emergency in reasoning BUT choose correct specialty
- Do NOT default to Emergency Medicine just because severity is high"""

    @staticmethod
    def build_prompt(patient: PatientInfo, symptoms: str, rag_context: str, 
                     rag_first_aid: List[str] = None) -> str:
        """
        بناء الـ prompt الكامل للـ LLM مع إضافة first_aid من الراج
        """
        # بناء معلومات المريض
        profile_parts = [
            f"- Name: {patient.name}", 
            f"- Age: {patient.age or 'unknown'}", 
            f"- Gender: {patient.gender or 'unknown'}"
        ]
        
        if patient.weight and patient.height:
            profile_parts.extend([
                f"- Weight: {patient.weight} kg", 
                f"- Height: {patient.height} cm", 
                f"- BMI: {patient.bmi or 'N/A'} ({patient.get_bmi_category()})"
            ])
        
        if patient.marital_status:
            profile_parts.append(f"- Marital Status: {patient.marital_status}")
        if patient.job:
            profile_parts.append(f"- Job: {patient.job}")
        
        profile_parts.extend([
            f"- Chronic Diseases: {', '.join(patient.chronic_diseases) or 'none'}", 
            f"- Allergies: {', '.join(patient.allergies) or 'none'}", 
            f"- Current Medications: {', '.join(patient.current_medications) or 'none'}"
        ])
        
        patient_profile = "\n".join(profile_parts)
        
        # ✅ إضافة first_aid من الراج للـ prompt
        first_aid_context = ""
        if rag_first_aid:
            first_aid_context = f"""

📋 Existing First Aid Steps from Similar Cases (HIGH PRIORITY - use these as reference):
{chr(10).join(f"  {i+1}. {step}" for i, step in enumerate(rag_first_aid))}

IMPORTANT INSTRUCTIONS for First Aid:
- These steps are from medically validated similar cases
- Use them as your PRIMARY reference
- You can:
  ✅ Use them as-is if they perfectly match
  ✅ Adapt them based on patient's specific conditions (age, chronic diseases, allergies)
  ✅ Add additional relevant steps if needed
  ✅ Reorder based on urgency
  ❌ Do NOT contradict them without medical reason
  ❌ Do NOT ignore them - they're proven effective

- If patient has allergies or drug interactions, modify steps to avoid contraindicated treatments
- If patient is elderly/child, adjust dosages or contraindicated steps
"""
        else:
            first_aid_context = """

⚠️ No existing first aid steps found in database. 
Generate appropriate first aid steps based on:
- Medical best practices
- Patient's specific conditions
- Drug interactions and allergies
- Age-appropriate interventions
"""
        
        return f"""
Patient Information:
{patient_profile}

Current Symptoms:
{symptoms}

Similar Cases from Database (RAG):
{rag_context or 'No similar cases found'}
{first_aid_context}

Provide assessment considering:
1. Patient's BMI and physical condition
2. Age and gender-specific risks
3. Job-related health risks
4. Chronic diseases interactions
5. Medication contraindications
6. Allergy considerations
7. ✅ Use database first_aid steps if available (high priority)
8. ✅ Modify first_aid based on patient's unique situation

Return JSON only (no markdown, no explanations outside JSON).
"""

# -------------------------
# Health Check Utilities
# -------------------------
class HealthCheck:
    @staticmethod
    def check_database() -> bool:
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                _ = cursor.fetchone()
                return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False

    @staticmethod
    def check_embedding_model() -> bool:
        try:
            rag = RAGService()
            test_emb = rag.embed("test")
            return test_emb is not None and len(test_emb) > 0
        except Exception as e:
            logger.error(f"Embedding model health check failed: {e}")
            return False

    @staticmethod
    def get_status() -> Dict:
        db_ok = HealthCheck.check_database()
        emb_ok = HealthCheck.check_embedding_model()
        return {
            "status": "healthy" if (db_ok and emb_ok) else "unhealthy",
            "database": db_ok,
            "embedding_model": emb_ok,
            "timestamp": datetime.utcnow().isoformat()
        }

# -------------------------
# Conversation & Session Management (fixed)
# -------------------------
class ConversationManager:
    @staticmethod
    def create_session(patient_id: int, device_info: Dict) -> int:
        """FIXED: Use context manager"""
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO chat_sessions (patient_id, session_start, session_status, device_type, ip_address, user_agent)
                    OUTPUT INSERTED.session_id
                    VALUES (?, GETDATE(), 'active', ?, ?, ?)
                """, (
                    patient_id,
                    device_info.get('device_type'),
                    device_info.get('ip_address'),
                    device_info.get('user_agent')
                ))
                row = cursor.fetchone()
                return int(row[0]) if row else 0
        except Exception as e:
            logger.error(f"Failed to create session: {e}")
            return 0

    @staticmethod
    def save_message(session_id: int, sender_type: str, message_text: str, 
                    message_type: str = 'text', llm_model: str = None, 
                    rag_sources: List[Dict] = None, processing_time_ms: int = None) -> int:
        """FIXED: Use context manager"""
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO chat_messages (session_id, sender_type, message_text, message_type, 
                                              llm_model, rag_sources, processing_time_ms, timestamp)
                    OUTPUT INSERTED.message_id
                    VALUES (?, ?, ?, ?, ?, ?, ?, GETDATE())
                """, (
                    session_id, sender_type, message_text, message_type,
                    llm_model,
                    json.dumps(rag_sources, ensure_ascii=False) if rag_sources else None,
                    processing_time_ms
                ))
                row = cursor.fetchone()
                return int(row[0]) if row else 0
        except Exception as e:
            logger.error(f"Failed to save message: {e}")
            return 0

    @staticmethod
    def get_conversation_history(session_id: int) -> List[Dict]:
        """FIXED: Use context manager"""
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT message_id, sender_type, message_text, message_type, timestamp
                    FROM chat_messages
                    WHERE session_id = ?
                    ORDER BY timestamp ASC
                """, (session_id,))
                messages = []
                for row in cursor.fetchall():
                    messages.append({
                        "message_id": row[0],
                        "sender": row[1],
                        "text": row[2],
                        "type": row[3],
                        "timestamp": row[4]
                    })
                return messages
        except Exception as e:
            logger.error(f"Failed to get conversation history: {e}")
            return []

# -------------------------
# Drug & Allergy Safety Checker (fixed)
# -------------------------
class DrugSafetyChecker:
    """Check drug-drug interactions and allergy cross-reactivity using DB tables."""

    @staticmethod
    def check_interactions(medications: List[str]) -> List[Dict]:
        """FIXED: Use context manager"""
        interactions = []
        if not medications:
            return interactions

        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                for i, med1 in enumerate(medications):
                    for med2 in medications[i+1:]:
                        med1_hash = abs(hash(med1)) % (10 ** 8)
                        med2_hash = abs(hash(med2)) % (10 ** 8)

                        cursor.execute("""
                            SELECT interaction_severity, description, clinical_effects, management
                            FROM drug_interactions
                            WHERE (drug1_hash = ? OR drug2_hash = ?)
                              AND (drug1_name LIKE ? OR drug2_name LIKE ? 
                                   OR drug1_name LIKE ? OR drug2_name LIKE ?)
                        """, (med1_hash, med1_hash, f"%{med1}%", f"%{med1}%", f"%{med2}%", f"%{med2}%"))

                        row = cursor.fetchone()
                        if row:
                            interactions.append({
                                "drug1": med1,
                                "drug2": med2,
                                "severity": row[0],
                                "description": row[1],
                                "clinical_effects": row[2],
                                "management": row[3]
                            })
                return interactions
        except Exception as e:
            logger.error(f"Failed to check drug interactions: {e}")
            return []

    @staticmethod
    def check_allergies(patient_allergies: List[str], suggested_treatment: str) -> List[Dict]:
        """FIXED: Use context manager"""
        warnings = []
        if not patient_allergies:
            return warnings

        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                for allergy in patient_allergies:
                    allergy_hash = abs(hash(allergy)) % (10 ** 8)

                    cursor.execute("""
                        SELECT cross_reactive_substances, safe_alternatives, 
                               emergency_treatment, severity_range
                        FROM allergy_database
                        WHERE allergen_hash = ? AND allergen_name LIKE ?
                    """, (allergy_hash, f"%{allergy}%"))

                    row = cursor.fetchone()
                    if row:
                        warnings.append({
                            "allergen": allergy,
                            "cross_reactive": row[0],
                            "alternatives": row[1],
                            "emergency_protocol": row[2],
                            "severity": row[3]
                        })
                return warnings
        except Exception as e:
            logger.error(f"Failed to check allergies: {e}")
            return []

# -------------------------
# Queue Manager (Appointment Queue) (fixed)
# -------------------------
class QueueManager:
    @staticmethod
    def add_to_queue(patient_id: int, doctor_id: Optional[int], consultation_id: int, severity_level: int) -> Dict:
        """FIXED: Use context manager"""
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                priority = QueueManager._calculate_priority(severity_level)

                if doctor_id:
                    cursor.execute("""
                        SELECT MAX(queue_position) 
                        FROM appointment_queue 
                        WHERE doctor_id = ? AND queue_status = 'waiting'
                    """, (doctor_id,))
                else:
                    cursor.execute("""
                        SELECT MAX(queue_position) 
                        FROM appointment_queue 
                        WHERE queue_status = 'waiting'
                    """)

                last_position = cursor.fetchone()[0] or 0
                new_position = last_position + 1

                cursor.execute("""
                    INSERT INTO appointment_queue (
                        patient_id, doctor_id, consultation_id, severity_level, 
                        queue_position, queue_status, joined_queue_at, 
                        initial_priority, current_priority
                    ) OUTPUT INSERTED.queue_id, INSERTED.estimated_wait_minutes
                    VALUES (?, ?, ?, ?, ?, 'waiting', GETDATE(), ?, ?)
                """, (patient_id, doctor_id, consultation_id, severity_level, new_position, priority, priority))

                row = cursor.fetchone()
                return {
                    "queue_id": int(row[0]) if row and row[0] is not None else 0,
                    "position": new_position,
                    "estimated_wait_minutes": int(row[1]) if row and row[1] is not None else None
                }
        except Exception as e:
            logger.error(f"Failed to add to queue: {e}")
            return {"queue_id": 0, "position": 0, "estimated_wait_minutes": None}

    @staticmethod
    def _calculate_priority(severity: int) -> int:
        if severity >= 8:
            return 10
        elif severity >= 6:
            return 7
        elif severity >= 4:
            return 5
        else:
            return 3

# -------------------------
# System Logging & Access Audit (fixed)
# -------------------------
class SystemLogger:
    @staticmethod
    def log_event(log_level: str, component: str, action: str, message: str, user_id: int = None, session_id: int = None, error_details: str = None, execution_time_ms: int = None):
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO system_logs (
                        timestamp, log_level, component, action, user_id, session_id, message, error_details, ip_address, execution_time_ms
                    ) VALUES (GETDATE(), ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (log_level, component, action, user_id, session_id, message, error_details, None, execution_time_ms))
        except Exception as e:
            logger.error(f"Failed to write to system_logs: {e}")

class PatientAccessLogger:
    @staticmethod
    def log_access(patient_id: int, access_type: str, ip_address: str):
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO PatientAccessLog (patient_id, access_type, accessed_at, ip_address)
                    VALUES (?, ?, GETDATE(), ?)
                """, (patient_id, access_type, ip_address))
        except Exception as e:
            logger.error(f"Failed to log patient access: {e}")

# -------------------------
# Main HospitalChatbot Orchestrator (unchanged logic, DB calls already use context managers)
# -------------------------
class HospitalChatbot:
    def __init__(self):
        self.rag = RAGService()
        self.db = ChatbotDatabaseManager()
        self.cache = cache_mgr
        self.metrics = metrics
        self.rate_limiter = rate_limiter

    def process_consultation(
        self,
        patient_id: Optional[int] = None,  # ✅ جعله Optional
        symptoms: str = "",
        model_id: Optional[str] = None,
        use_rag: bool = True,
        top_k: int = 5,
        request_identifier: Optional[str] = None,
        session_id: Optional[int] = None,
        device_info: Optional[Dict] = None,
        rag_page: int = 1,
        # ✅ إضافة بيانات مباشرة للمريض (اختياري)
        patient_age: Optional[int] = None,
        patient_gender: Optional[str] = None,
        patient_weight: Optional[float] = None,
        patient_height: Optional[float] = None,
        chronic_diseases: Optional[List[str]] = None,
        allergies: Optional[List[str]] = None,
        current_medications: Optional[List[str]] = None
    ) -> ConsultationResult:
        start_time = time.time()
        warnings: List[str] = []

        # Rate limiting check
        if request_identifier:
            if not self.rate_limiter.is_allowed(request_identifier):
                raise PermissionError("Rate limit exceeded for identifier")

        if not model_id:
            model_id = config.OPENROUTER_MODEL

        # ===================================
        # ✅ 1) Fetch/Create patient
        # ===================================
        patient = None
        
        if patient_id:
            # السيناريو العادي: عندنا patient_id
            cached = None
            try:
                cached = self.cache.get_patient_cache(patient_id) if self.cache else None
            except Exception as e:
                logger.warning(f"Cache get error: {e}")
            if cached:
                try:
                    patient = PatientInfo(**cached)
                except Exception:
                    patient = None
            if not patient:
                with self.metrics.track_operation("fetch_patient"):
                    patient = self.db.fetch_patient(patient_id)
                if not patient:
                    raise ValueError(f"Patient {patient_id} not found")
                try:
                    self.cache.set_patient_cache(patient_id, patient.model_dump(), ttl=3600)
                except Exception:
                    pass
        else:
            # ✅ سيناريو جديد: مفيش patient_id - نبني مريض مؤقت
            logger.info("No patient_id provided - creating anonymous patient profile")
            
            # حساب BMI إذا توفر الوزن والطول
            bmi = None
            if patient_weight and patient_height and patient_height > 0:
                height_m = patient_height / 100.0
                bmi = round(patient_weight / (height_m ** 2), 1)
            
            patient = PatientInfo(
                id=0,  # ID مؤقت
                name="مريض مجهول",  # اسم افتراضي
                age=patient_age,
                gender=patient_gender,
                marital_status=None,
                job=None,
                weight=patient_weight,
                height=patient_height,
                bmi=bmi,
                chronic_diseases=chronic_diseases or [],
                allergies=allergies or [],
                current_medications=current_medications or []
            )
            
            # لن نحفظ session أو access log لأنه مريض مجهول
            session_id = None
            warnings.append("ℹ️ استشارة بدون سجل مريض - لن يتم حفظ البيانات في قاعدة البيانات")

        # 2) Create session if device_info provided and session_id not given
        if patient_id and not session_id and device_info:
            session_id = ConversationManager.create_session(patient_id, device_info)

        # 3) Log patient access (audit) - فقط إذا كان عندنا patient_id
        if patient_id:
            try:
                PatientAccessLogger.log_access(patient_id, 'consultation_read', device_info.get('ip_address') if device_info else None)
            except Exception:
                pass

        # 4) Save user message - فقط إذا كان عندنا session
        if session_id:
            try:
                ConversationManager.save_message(session_id=session_id, sender_type='user', message_text=symptoms, message_type='symptom_report')
            except Exception:
                pass

        # 5) Sanitize patient info
        try:
            patient = SecurePatientInfo(**patient.model_dump())
        except Exception:
            logger.warning("Patient sanitization failed; using original record")

        # 6) RAG search (نفس الكود)
        rag_matches: List[RAGMatch] = []
        rag_confidence = 0.0
        rag_context = ""
        rag_first_aid = []
        
        if use_rag:
            with self.metrics.track_operation("rag_search"):
                rag_matches = self.rag.search_paginated(symptoms, page=rag_page, page_size=200, top_k=top_k)
                if not rag_matches:
                    rag_matches = self.rag.search(symptoms, top_k=top_k)
            
            if rag_matches:
                top_sims = [m.similarity for m in rag_matches[:3]]
                if top_sims:
                    rag_confidence = sum(top_sims) / len(top_sims)
                rag_context = "\n".join([f"- {m.symptoms} => {m.diagnosis} (specialty: {m.specialty}, confidence: {m.similarity:.2f})" for m in rag_matches])
                
                for match in rag_matches[:2]:
                    if match.similarity > 0.75:
                        rag_first_aid.extend(match.first_aid)
                
                rag_first_aid = list(dict.fromkeys(rag_first_aid))
                
                if rag_confidence < 0.65:
                    warnings.append("⚠️ Low confidence in similar cases. Diagnosis relies more on general medical knowledge.")
            else:
                warnings.append("⚠️ No similar cases found in database. Diagnosis based on AI medical knowledge only.")

        # 7) Build prompt and call LLM
        prompt = LLMService.build_prompt(patient, symptoms, rag_context, rag_first_aid)
        with self.metrics.track_operation("llm_call"):
            try:
                llm_raw = ImprovedLLMService.call_llm_with_retry(prompt, config.OPENROUTER_API_KEY, model_id)
            except Exception as e:
                logger.error(f"LLM call failed after retries: {e}")
                parsed = ImprovedLLMService.get_default_response()
            else:
                parsed = ImprovedLLMService.parse_response_robust(llm_raw)

        # التحقق من صحة المدخلات
        if not parsed.get("input_valid", True):
            processing_time_ms = int((time.time() - start_time) * 1000)
            
            validation_msg = parsed.get("validation_message", "المدخل غير صالح كأعراض طبية")
            
            severity = SeverityAssessment(
                level=1,
                label="minor",
                reasoning=parsed.get("severity_reasoning", "لا يمكن التقييم"),
                emergency_required=False
            )
            
            assessment = MedicalAssessment(
                preliminary_diagnosis=parsed.get("preliminary_diagnosis", "الرجاء إدخال أعراض واضحة"),
                severity=severity,
                first_aid=parsed.get("first_aid", []),
                warnings=parsed.get("warnings", []),
                specialty_required=parsed.get("specialty_required", "General Practitioner"),
                alternative_specialties=parsed.get("alternative_specialties", [])
            )
            
            result = ConsultationResult(
                consultation_id=0,
                patient_id=patient.id,
                assessment=assessment,
                available_doctors=[],
                rag_matches=[],
                confidence={
                    "overall": 0.0,
                    "rag_confidence": 0.0,
                    "diagnosis_confidence": 0.0,
                    "source": "ai_validation",
                    "validation_message": validation_msg
                },
                warnings=[validation_msg],
                processing_time_ms=processing_time_ms,
                model_used=config.MODEL_DISPLAY_NAMES.get(model_id, model_id),
                model_id=model_id,
                queue_info=None,
                input_valid=False
            )
            return result

        # 8) دمج first_aid
        llm_first_aid = parsed.get("first_aid", [])
        first_aid_source = "llm"
        
        if rag_first_aid and rag_confidence > 0.75:
            final_first_aid = rag_first_aid + [
                f for f in llm_first_aid if f not in rag_first_aid
            ]
            first_aid_source = "rag" if not llm_first_aid else "hybrid"
        else:
            final_first_aid = llm_first_aid + [
                f for f in rag_first_aid if f not in llm_first_aid
            ]
            first_aid_source = "llm" if not rag_first_aid else "hybrid"
        
        # 9) Build assessment
        severity = SeverityAssessment(
            level=parsed.get("severity_level", 5),
            label="moderate",
            reasoning=parsed.get("severity_reasoning", ""),
            emergency_required=parsed.get("severity_level", 5) >= 8
        )
        
        assessment = MedicalAssessment(
            preliminary_diagnosis=parsed.get("preliminary_diagnosis", ""),
            severity=severity,
            first_aid=final_first_aid[:5],
            warnings=parsed.get("warnings", []),
            specialty_required=parsed.get("specialty_required", "General Practitioner"),
            alternative_specialties=parsed.get("alternative_specialties", [])
        )

        # 10) Drug interactions and allergy checks
        try:
            drug_warnings = DrugSafetyChecker.check_interactions(patient.current_medications)
            allergy_warnings = DrugSafetyChecker.check_allergies(patient.allergies, assessment.preliminary_diagnosis)
        except Exception as e:
            logger.error(f"Safety checks failed: {e}")
            drug_warnings = []
            allergy_warnings = []
        
        for dw in drug_warnings:
            if dw.get("severity") and dw.get("severity").lower() in ("major", "severe", "high"):
                warnings.append(f"⚠️ Drug interaction: {dw['drug1']} + {dw['drug2']} severity {dw['severity']}")
        for aw in allergy_warnings:
            warnings.append(f"⚠️ Allergy cross-reactivity: {aw.get('allergen')} -> {aw.get('cross_reactive')}")

        # 11) Diagnosis confidence
        diagnosis_confidence = self._calculate_diagnosis_confidence(assessment, rag_matches, patient)
        overall_confidence = (rag_confidence * 0.6 + diagnosis_confidence * 0.4)
        if rag_confidence >= 0.8:
            source = "rag_based"
        elif rag_confidence >= 0.5:
            source = "hybrid"
        else:
            source = "llm_knowledge"

        # Get available doctors
        with self.metrics.track_operation("get_doctors"):
            doctors = self.db.get_available_doctors(assessment.specialty_required, assessment.alternative_specialties)

        same_spec_doctors = [d for d in doctors if d.specialty == assessment.specialty_required]

        queue_info = None
        consultation_id = 0

        # ✅ حفظ الاستشارة فقط إذا كان عندنا patient_id
        if patient_id:
            if not same_spec_doctors:
                # No doctors available
                with self.metrics.track_operation("save_consultation_prequeue"):
                    consultation_id_raw = self.db.save_consultation(
                        patient_id=patient_id,
                        symptoms=symptoms,
                        assessment=assessment,
                        patient=patient,
                        rag_matches=rag_matches,
                        model_id=model_id,
                        processing_time_ms=int((time.time() - start_time) * 1000),
                        session_id=session_id,
                        ai_confidence_score=round(overall_confidence * 100, 2)
                    )
                consultation_id = consultation_id_raw or 0

                try:
                    queue_added = QueueManager.add_to_queue(
                        patient_id=patient_id,
                        doctor_id=None,
                        consultation_id=consultation_id,
                        severity_level=assessment.severity.level
                    )
                    queue_info = {
                        "queue_id": queue_added.get("queue_id"),
                        "position": queue_added.get("position"),
                        "estimated_wait_minutes": queue_added.get("estimated_wait_minutes"),
                        "status": "waiting",
                        "specialty_required": assessment.specialty_required,
                    }
                except Exception as e:
                    logger.error(f"Failed to add to appointment_queue: {e}")
                    queue_info = {"queue_error": str(e)}
            else:
                # Doctors available - save consultation
                with self.metrics.track_operation("save_consultation"):
                    consultation_id_raw = self.db.save_consultation(
                        patient_id=patient_id,
                        symptoms=symptoms,
                        assessment=assessment,
                        patient=patient,
                        rag_matches=rag_matches,
                        model_id=model_id,
                        processing_time_ms=int((time.time() - start_time) * 1000),
                        session_id=session_id,
                        ai_confidence_score=round(overall_confidence * 100, 2)
                    )
                consultation_id = consultation_id_raw or 0
                if consultation_id == 0:
                    warnings.append("⚠️ لم يتم حفظ الاستشارة في قاعدة البيانات، لكن تم توليد التقييم الطبي بنجاح.")
        else:
            # ✅ بدون patient_id - لا نحفظ شيء
            warnings.append("ℹ️ استشارة مؤقتة - لم يتم حفظها في قاعدة البيانات")

        processing_time_ms = int((time.time() - start_time) * 1000)

        # Save bot response - فقط إذا كان عندنا session
        if session_id:
            try:
                ConversationManager.save_message(
                    session_id=session_id,
                    sender_type='bot',
                    message_text=assessment.preliminary_diagnosis,
                    message_type='diagnosis',
                    llm_model=model_id,
                    rag_sources=[m.model_dump() for m in rag_matches],
                    processing_time_ms=processing_time_ms
                )
            except Exception:
                pass

        # System logging - فقط إذا كان عندنا patient_id
        if patient_id:
            try:
                SystemLogger.log_event(
                    log_level='INFO',
                    component='HospitalChatbot',
                    action='consultation_completed',
                    message=f'Consultation for patient {patient_id}',
                    user_id=patient_id,
                    session_id=session_id,
                    execution_time_ms=processing_time_ms
                )
            except Exception:
                pass

        result = ConsultationResult(
            consultation_id=consultation_id,
            patient_id=patient.id,
            assessment=assessment,
            available_doctors=same_spec_doctors if same_spec_doctors else [],
            rag_matches=rag_matches,
            confidence={
                "overall": round(overall_confidence, 2),
                "rag_confidence": round(rag_confidence, 2),
                "diagnosis_confidence": round(diagnosis_confidence, 2),
                "source": source,
                "rag_matches_count": len(rag_matches),
                "first_aid_source": first_aid_source
            },
            warnings=warnings,
            processing_time_ms=processing_time_ms,
            model_used=config.MODEL_DISPLAY_NAMES.get(model_id, model_id),
            model_id=model_id,
            queue_info=queue_info,
            input_valid=True
        )
        return result

    def _calculate_diagnosis_confidence(self, assessment: MedicalAssessment, rag_matches: List[RAGMatch], patient: PatientInfo) -> float:
        confidence = 0.5
        if rag_matches:
            for match in rag_matches[:3]:
                if match.specialty == assessment.specialty_required:
                    confidence += 0.1
                if match.diagnosis in assessment.preliminary_diagnosis:
                    confidence += 0.15
        if 3 <= assessment.severity.level <= 7:
            confidence += 0.1
        if assessment.specialty_required != "General Practitioner":
            confidence += 0.05
        return min(confidence, 1.0)

# -------------------------
# Maintenance & Integrity (fixed)
# -------------------------
class DatabaseMaintenance:
    @staticmethod
    def check_index_fragmentation() -> List[Dict]:
        """FIXED: Use context manager"""
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT 
                        OBJECT_NAME(ips.object_id) AS TableName,
                        i.name AS IndexName,
                        ips.avg_fragmentation_in_percent,
                        ips.page_count
                    FROM sys.dm_db_index_physical_stats(
                        DB_ID(), NULL, NULL, NULL, 'LIMITED'
                    ) AS ips
                    INNER JOIN sys.indexes AS i 
                        ON ips.object_id = i.object_id 
                        AND ips.index_id = i.index_id
                    WHERE ips.avg_fragmentation_in_percent > 10
                      AND ips.page_count > 100
                    ORDER BY ips.avg_fragmentation_in_percent DESC;
                """)
                results = []
                for row in cursor.fetchall():
                    results.append({
                        "table": row[0],
                        "index": row[1],
                        "fragmentation": round(row[2], 2),
                        "pages": row[3]
                    })
                return results
        except Exception as e:
            logger.error(f"Failed to check index fragmentation: {e}")
            return []

    @staticmethod
    def rebuild_fragmented_indexes(threshold: float = 30.0):
        """FIXED: Use context manager"""
        fragmented = DatabaseMaintenance.check_index_fragmentation()
        rebuilt = []

        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                for idx in fragmented:
                    if idx["fragmentation"] >= threshold:
                        try:
                            sql = f"ALTER INDEX [{idx['index']}] ON [{idx['table']}] REBUILD;"
                            cursor.execute(sql)
                            rebuilt.append(idx)
                            logger.info(f"Rebuilt index: {idx['table']}.{idx['index']}")
                        except Exception as e:
                            logger.error(f"Failed to rebuild {idx['index']}: {e}")
                return rebuilt
        except Exception as e:
            logger.error(f"Failed to rebuild indexes: {e}")
            return []

    @staticmethod
    def update_statistics_all():
        """FIXED: Use context manager"""
        tables = [
            'Patients', 'doctors', 'patient_consultations',
            'appointment_queue', 'chat_sessions', 'chat_messages',
            'symptoms_knowledge_base', 'drug_interactions', 'allergy_database'
        ]

        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                for table in tables:
                    try:
                        cursor.execute(f"UPDATE STATISTICS [{table}] WITH FULLSCAN;")
                        logger.info(f"Updated statistics for {table}")
                    except Exception as e:
                        logger.error(f"Failed to update statistics for {table}: {e}")
        except Exception as e:
            logger.error(f"Failed to update statistics: {e}")

    @staticmethod
    def get_database_size() -> Dict:
        """FIXED: Use context manager"""
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT 
                        SUM(size) * 8 / 1024 AS TotalSizeMB,
                        SUM(CASE WHEN type = 0 THEN size END) * 8 / 1024 AS DataSizeMB,
                        SUM(CASE WHEN type = 1 THEN size END) * 8 / 1024 AS LogSizeMB
                    FROM sys.database_files;
                """)
                row = cursor.fetchone()
                return {
                    "total_mb": row[0] or 0,
                    "data_mb": row[1] or 0,
                    "log_mb": row[2] or 0
                }
        except Exception as e:
            logger.error(f"Failed to get database size: {e}")
            return {"total_mb": 0, "data_mb": 0, "log_mb": 0}

class DataIntegrityChecker:
    @staticmethod
    def check_all() -> Dict:
        results = {
            "orphaned_records": DataIntegrityChecker.find_orphaned_records(),
            "invalid_references": DataIntegrityChecker.find_invalid_references(),
            "constraint_violations": DataIntegrityChecker.check_constraints(),
            "duplicate_records": DataIntegrityChecker.find_duplicates()
        }
        return results

    @staticmethod
    def find_orphaned_records() -> List[Dict]:
        checks = []
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT COUNT(*) 
                    FROM patient_consultations pc
                    LEFT JOIN chat_sessions cs ON pc.session_id = cs.session_id
                    WHERE pc.session_id IS NOT NULL AND cs.session_id IS NULL;
                """)
                count = cursor.fetchone()[0]
                if count > 0:
                    checks.append({
                        "table": "patient_consultations",
                        "issue": "orphaned session_id",
                        "count": count
                    })
            return checks
        except Exception as e:
            logger.error(f"Failed to find orphaned records: {e}")
            return checks

    @staticmethod
    def find_invalid_references() -> List[Dict]:
        issues = []
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, weight, height, BMI
                    FROM Patients
                    WHERE weight IS NOT NULL 
                      AND height IS NOT NULL
                      AND BMI IS NOT NULL
                      AND ABS(BMI - (weight / POWER(height/100, 2))) > 0.5;
                """)
                for row in cursor.fetchall():
                    issues.append({
                        "type": "invalid_bmi",
                        "patient_id": row[0],
                        "stored_bmi": row[3],
                        "calculated_bmi": round(row[1] / ((row[2]/100) ** 2), 1)
                    })
            return issues
        except Exception as e:
            logger.error(f"Failed to find invalid references: {e}")
            return issues

    @staticmethod
    def check_constraints() -> List[Dict]:
        return []

    @staticmethod
    def find_duplicates() -> List[Dict]:
        duplicates = []
        try:
            with ImprovedDatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT national_id, COUNT(*) as count
                    FROM Patients
                    WHERE is_active = 1
                    GROUP BY national_id
                    HAVING COUNT(*) > 1;
                """)
                for row in cursor.fetchall():
                    duplicates.append({
                        "type": "duplicate_national_id",
                        "value": row[0],
                        "count": row[1]
                    })
            return duplicates
        except Exception as e:
            logger.error(f"Failed to find duplicates: {e}")
            return duplicates

class MaintenanceScheduler:
    @staticmethod
    def start_background_tasks():
        """
        Start background maintenance tasks using `schedule`. If schedule not installed,
        this function logs a warning and exits.
        """
        if schedule is None:
            logger.warning("schedule package not installed; maintenance scheduler disabled")
            return
        try:
            schedule.every().day.at("03:00").do(DatabaseMaintenance.update_statistics_all)
            schedule.every().sunday.at("02:00").do(DatabaseMaintenance.rebuild_fragmented_indexes)
            schedule.every().hour.do(DataIntegrityChecker.check_all)
        except Exception as e:
            logger.error(f"Failed to schedule tasks: {e}")
            return

        def run_scheduler():
            while True:
                try:
                    schedule.run_pending()
                except Exception as e:
                    logger.error(f"Scheduler runtime error: {e}")
                time.sleep(60)

        thread = Thread(target=run_scheduler, daemon=True)
        thread.start()
        logger.info("Maintenance scheduler started")

# -------------------------
# Singleton Helper
# -------------------------
chatbot_instance: Optional[HospitalChatbot] = None

def get_chatbot() -> HospitalChatbot:
    global chatbot_instance
    if chatbot_instance is None:
        chatbot_instance = HospitalChatbot()
    return chatbot_instance

# -------------------------
# Environment Config Helper (DEV/PROD toggles)
# -------------------------
from enum import Enum

class Environment(Enum):
    DEVELOPMENT = "dev"
    STAGING = "staging"
    PRODUCTION = "prod"

class EnvironmentConfig:
    def __init__(self):
        self.env = Environment(os.getenv("ENVIRONMENT", "dev"))

    @property
    def log_level(self):
        return logging.DEBUG if self.env == Environment.DEVELOPMENT else logging.INFO

    @property
    def enable_debug(self):
        return self.env != Environment.PRODUCTION

    @property
    def llm_timeout(self):
        return 60 if self.env == Environment.PRODUCTION else 30

# -------------------------
# Healthcheck Function (exposed for web frameworks)
# -------------------------
def healthcheck_status() -> Dict:
    return HealthCheck.get_status()

# -------------------------
# Entrypoint for running maintenance in standalone mode
# -------------------------
if __name__ == "__main__":
    try:
        MaintenanceScheduler.start_background_tasks()
    except Exception as e:
        logger.error(f"Failed to start maintenance scheduler: {e}")
    chatbot = get_chatbot()
    logger.info("Hospital chatbot instance created and ready.")