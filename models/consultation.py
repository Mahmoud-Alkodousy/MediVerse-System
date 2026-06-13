"""
MediVerse - Consultation Pydantic Models
These are the models used by the chatbot service.
Kept compatible with existing frontend expectations.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, field_validator


class PatientInfo(BaseModel):
    """Patient info used internally by the chatbot."""
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

    @field_validator("bmi")
    def validate_bmi(cls, v, info):
        if v is None:
            weight = info.data.get("weight")
            height = info.data.get("height")
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
            return "سمنة"


class SeverityAssessment(BaseModel):
    level: int = Field(..., ge=1, le=10)
    label: Literal["minor", "moderate", "serious", "critical"]
    reasoning: str
    emergency_required: bool

    @field_validator("label")
    def validate_label_matches_level(cls, v, info):
        level = info.data.get("level", 5)
        expected = (
            "minor" if level <= 3
            else "moderate" if level <= 6
            else "serious" if level <= 8
            else "critical"
        )
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


class DoctorInfo(BaseModel):
    """Doctor info inside consultation result (kept compatible)."""
    id: int
    name: str
    specialty: str
    specialty_ar: str
    rating: float
    floor: int
    room: str
    current_patients: int
    estimated_wait_minutes: int


class RAGMatch(BaseModel):
    symptoms: str
    diagnosis: str
    specialty: str
    similarity: float
    first_aid: List[str] = Field(default_factory=list)


class ConsultationResult(BaseModel):
    consultation_id: int = Field(default=0)
    patient_id: int
    assessment: MedicalAssessment
    available_doctors: List[DoctorInfo]
    rag_matches: List[RAGMatch] = Field(default_factory=list)
    confidence: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    processing_time_ms: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    model_used: str = Field(default="")
    model_id: str = Field(default="")
    queue_info: Optional[Dict[str, Any]] = None
    input_valid: bool = Field(default=True)
    first_aid_source: str = Field(default="llm")
    rag_first_aid_used: bool = Field(default=False)


class ConsultationRequest(BaseModel):
    """Request model for chatbot consultation (kept compatible)."""
    patient_id: Optional[int] = Field(None)
    symptoms: str = Field(..., min_length=3)
    model: Optional[str] = Field(default="GPT-4o Mini 🚀")
    patient_age: Optional[int] = Field(None, ge=0, le=150)
    patient_gender: Optional[str] = Field(None, pattern=r'^(male|female|ذكر|أنثى)$')
    patient_weight: Optional[float] = Field(None, gt=0, lt=500)
    patient_height: Optional[float] = Field(None, gt=0, lt=300)
    chronic_diseases: Optional[List[str]] = None
    allergies: Optional[List[str]] = None
    current_medications: Optional[List[str]] = None
    use_rag: bool = Field(True)
    top_k: int = Field(5, ge=1, le=20)
