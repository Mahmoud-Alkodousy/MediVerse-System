"""
MediVerse - Manager Dashboard Pydantic Models
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class DashboardOverview(BaseModel):
    total_patients: int = 0
    total_doctors: int = 0
    available_doctors: int = 0
    total_waiting: int = 0
    critical_waiting: int = 0
    today_appointments: int = 0
    today_consultations: int = 0
    avg_wait_minutes: float = 0.0


class SpecialtyStats(BaseModel):
    specialty: str
    specialty_ar: str
    doctor_count: int = 0
    patient_count_today: int = 0
    avg_wait_minutes: float = 0.0
    avg_rating: float = 0.0


class AIAccuracyStats(BaseModel):
    total_consultations: int = 0
    verified_count: int = 0
    correct_count: int = 0
    accuracy_percent: float = 0.0
    by_specialty: List[Dict[str, Any]] = Field(default_factory=list)


class WaitTimeReport(BaseModel):
    avg_wait_minutes: float = 0.0
    max_wait_minutes: float = 0.0
    min_wait_minutes: float = 0.0
    by_hour: List[Dict[str, Any]] = Field(default_factory=list)
    by_specialty: List[Dict[str, Any]] = Field(default_factory=list)


class DailyReport(BaseModel):
    date: str
    total_patients_seen: int = 0
    total_appointments: int = 0
    total_consultations: int = 0
    total_no_shows: int = 0
    avg_wait_minutes: float = 0.0
    busiest_specialty: Optional[str] = None
    busiest_doctor: Optional[str] = None
    critical_cases: int = 0


class LiveQueueItem(BaseModel):
    queue_id: int
    patient_name: str
    doctor_name: str
    specialty: str
    severity_level: int
    queue_status: str
    waiting_since: Optional[str] = None
    estimated_wait: Optional[int] = None


class LiveQueueResponse(BaseModel):
    total_in_queue: int
    items: List[LiveQueueItem]


class SystemLogEntry(BaseModel):
    log_id: int
    timestamp: Optional[str] = None
    log_level: str
    component: Optional[str] = None
    action: Optional[str] = None
    message: Optional[str] = None
    user_id: Optional[int] = None
    execution_time_ms: Optional[int] = None


class PaginatedResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[Any]
