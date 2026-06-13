"""
MediVerse - Patient Pydantic Models
"""

from datetime import datetime, date
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, field_validator


# ── Output Models ──────────────────────────────────────────────

class PatientOut(BaseModel):
    """Patient data returned by API."""
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
    profile_image_url: Optional[str] = None
    emergency_contact: Optional[str] = None
    last_visit_date: Optional[str] = None
    created_at: Optional[str] = None


class PatientProfile(PatientOut):
    """Extended patient profile with computed fields."""
    age: Optional[int] = None
    bmi_category: Optional[str] = None
    days_since_last_visit: Optional[int] = None
    follow_up_suggested: bool = False


# ── Input Models ───────────────────────────────────────────────

class PatientRegistration(BaseModel):
    """Validated patient registration form."""
    full_name: str = Field(..., min_length=3, max_length=200)
    national_id: str = Field(..., pattern=r'^\d{14}$', description="14-digit national ID")
    gender: str = Field(..., pattern=r'^(ذكر|أنثى|male|female)$')
    date_of_birth: datetime
    phone_number: str = Field(..., pattern=r'^(01[0-9]{9})$')
    address: Optional[str] = Field(None, max_length=200)
    blood_type: Optional[str] = Field(None, pattern=r'^(A|B|AB|O)[+-]$')
    chronic_diseases: Optional[str] = None
    allergies: Optional[str] = None
    current_medications: Optional[str] = None
    marital_status: Optional[str] = None
    job: Optional[str] = None
    weight: Optional[float] = Field(None, gt=0, lt=500)
    height: Optional[float] = Field(None, gt=0, lt=300)
    BMI: Optional[float] = Field(None, gt=0, lt=100)
    emergency_contact: Optional[str] = None

    @field_validator("date_of_birth")
    def valid_age(cls, v: datetime) -> datetime:
        today = datetime.utcnow().date()
        dob = v.date() if hasattr(v, "date") else v
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if age < 0 or age > 150:
            raise ValueError("Invalid date_of_birth (age out of range)")
        return v


class PatientUpdate(BaseModel):
    """Fields that a patient can update on their profile."""
    phone_number: Optional[str] = Field(None, pattern=r'^(01[0-9]{9})$')
    address: Optional[str] = Field(None, max_length=200)
    blood_type: Optional[str] = Field(None, pattern=r'^(A|B|AB|O)[+-]$')
    chronic_diseases: Optional[str] = None
    allergies: Optional[str] = None
    current_medications: Optional[str] = None
    marital_status: Optional[str] = None
    job: Optional[str] = None
    weight: Optional[float] = Field(None, gt=0, lt=500)
    height: Optional[float] = Field(None, gt=0, lt=300)
    emergency_contact: Optional[str] = None


# ── Response Models ────────────────────────────────────────────

class FaceCheckResponse(BaseModel):
    exists: bool
    similarity: Optional[float] = None
    patient: Optional[PatientOut] = None
    message: str
    quality_info: Optional[Dict[str, Any]] = None


class NationalIdCheckResponse(BaseModel):
    exists: bool
    patient: Optional[PatientOut] = None
    age: Optional[int] = None
    message: str


class PatientRegistrationResponse(BaseModel):
    success: bool
    patient_id: Optional[int] = None
    message: str
    quality_info: Optional[Dict[str, Any]] = None


class PatientHistoryItem(BaseModel):
    consultation_id: int
    symptoms: Optional[str] = None
    diagnosis: Optional[str] = None
    severity: Optional[int] = None
    specialty: Optional[str] = None
    doctor_name: Optional[str] = None
    doctor_notes: Optional[str] = None
    date: Optional[str] = None
    model_used: Optional[str] = None


class PatientHistoryResponse(BaseModel):
    patient_id: int
    total: int
    consultations: List[PatientHistoryItem]


class LastVisitResponse(BaseModel):
    patient_id: int
    last_visit_date: Optional[str] = None
    days_since_last_visit: Optional[int] = None
    follow_up_suggested: bool = False
    message: str
