"""
MediVerse - Doctor Pydantic Models
"""

from datetime import datetime, time
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ── Doctor Info (public-facing) ────────────────────────────────

class DoctorInfo(BaseModel):
    """Doctor info returned by chatbot/appointment endpoints."""
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
    profile_image_url: Optional[str] = None
    years_of_experience: Optional[int] = None


class DoctorProfile(BaseModel):
    """Full doctor profile (for doctor dashboard)."""
    doctor_id: int
    doctor_name: str
    email: str
    national_id: Optional[str] = None
    phone_number: Optional[str] = None
    specialty: str
    specialty_ar: str = ""
    profile_image_url: Optional[str] = None
    license_number: Optional[str] = None
    years_of_experience: Optional[int] = None
    room_number: str
    floor_number: int
    current_status: str = "available"
    current_patients_count: int = 0
    max_patients_per_day: int = 20
    average_consultation_minutes: int = 15
    rating: float = 0.0
    total_ratings: int = 0
    shift_start: Optional[str] = None
    shift_end: Optional[str] = None
    working_days: Optional[str] = None
    is_active: bool = True


# ── Doctor Input Models ────────────────────────────────────────

class DoctorStatusUpdate(BaseModel):
    """Update doctor availability status."""
    status: str = Field(..., pattern=r'^(available|busy|on_break|unavailable)$')


class DoctorProfileUpdate(BaseModel):
    """Fields a doctor can update on their profile."""
    phone_number: Optional[str] = None
    profile_image_url: Optional[str] = None
    average_consultation_minutes: Optional[int] = Field(None, ge=5, le=120)
    shift_start: Optional[str] = None
    shift_end: Optional[str] = None
    working_days: Optional[str] = None


class DoctorCreate(BaseModel):
    """Manager creates a new doctor account."""
    doctor_name: str = Field(..., min_length=3, max_length=200, alias="full_name", description="Full name e.g. Dr. John Doe")
    email: str = Field(..., min_length=5, max_length=200)
    password: str = Field(..., min_length=6, max_length=100)
    gender: Optional[str] = Field(None, description="Male / Female")
    date_of_birth: Optional[str] = Field(None, description="mm/dd/yyyy")
    marital_status: Optional[str] = Field(None, description="Single / Married / Divorced / Widowed")
    specialty: str = Field(..., min_length=2, alias="specialization")
    work_schedule: Optional[str] = Field(None, description="e.g. Sat-Wed, 8 AM - 4 PM")
    floor_number: int = Field(..., ge=0, le=50)
    room_number: str = Field(..., min_length=1)
    phone_number: Optional[str] = Field(None, alias="phone")
    years_of_experience: Optional[int] = Field(None, ge=0)
    consultation_fee_egp: Optional[float] = Field(None, ge=0)
    certifications: Optional[str] = None
    languages_spoken: Optional[str] = None
    national_id: Optional[str] = Field(None, pattern=r'^\d{14}$')
    license_number: Optional[str] = None
    max_patients_per_day: int = Field(20, ge=1, le=100)
    average_consultation_minutes: int = Field(15, ge=5, le=120)

    class Config:
        populate_by_name = True


class DoctorManagerUpdate(BaseModel):
    """Manager updates doctor info."""
    doctor_name: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    specialty: Optional[str] = None
    room_number: Optional[str] = None
    floor_number: Optional[int] = None
    license_number: Optional[str] = None
    years_of_experience: Optional[int] = None
    max_patients_per_day: Optional[int] = None
    average_consultation_minutes: Optional[int] = None
    shift_start: Optional[str] = None
    shift_end: Optional[str] = None
    working_days: Optional[str] = None
    is_active: Optional[bool] = None


# ── Doctor Notes ───────────────────────────────────────────────

class DoctorNoteCreate(BaseModel):
    """Doctor adds notes for a patient after consultation."""
    patient_id: int
    consultation_id: Optional[int] = None
    queue_id: Optional[int] = None
    diagnosis: Optional[str] = None
    prescription: Optional[str] = None
    notes: Optional[str] = None
    follow_up_date: Optional[str] = None


class DoctorNoteOut(BaseModel):
    note_id: int
    doctor_id: int
    doctor_name: Optional[str] = None
    patient_id: int
    consultation_id: Optional[int] = None
    diagnosis: Optional[str] = None
    prescription: Optional[str] = None
    notes: Optional[str] = None
    follow_up_date: Optional[str] = None
    created_at: Optional[str] = None


# ── Doctor Stats ───────────────────────────────────────────────

class DoctorStats(BaseModel):
    patients_today: int = 0
    patients_waiting: int = 0
    avg_wait_minutes: float = 0.0
    total_consultations: int = 0
    avg_rating: float = 0.0
    completed_today: int = 0


# ── Queue Item (Doctor's view) ─────────────────────────────────

class QueuePatientItem(BaseModel):
    """Single patient in the doctor's queue."""
    queue_id: int
    patient_id: int
    patient_name: str
    patient_phone: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[str] = None
    blood_type: Optional[str] = None
    chronic_diseases: Optional[str] = None
    allergies: Optional[str] = None
    severity_level: int = 5
    queue_position: int
    queue_status: str = "waiting"
    joined_queue_at: Optional[str] = None
    called_at: Optional[str] = None
    estimated_wait_minutes: Optional[int] = None
    symptoms: Optional[str] = None
    ai_diagnosis: Optional[str] = None
    ai_severity: Optional[int] = None
    ai_specialty: Optional[str] = None


class DoctorQueueResponse(BaseModel):
    doctor_id: int
    doctor_name: str
    total_waiting: int
    current_patient: Optional[QueuePatientItem] = None
    queue: List[QueuePatientItem]


class NextPatientResponse(BaseModel):
    success: bool
    message: str
    patient: Optional[QueuePatientItem] = None
    remaining_in_queue: int = 0
