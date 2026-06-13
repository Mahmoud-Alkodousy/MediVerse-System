"""
MediVerse - Appointment & Queue Pydantic Models
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ── Appointment (Direct Booking) ───────────────────────────────

class AppointmentBookRequest(BaseModel):
    """Patient books directly with a doctor (no chatbot)."""
    patient_id: int
    doctor_id: int
    appointment_date: str = Field(..., description="YYYY-MM-DD")
    appointment_time: str = Field(..., description="HH:MM")
    appointment_type: str = Field("new", pattern=r'^(new|follow_up|emergency|scheduled)$')
    notes: Optional[str] = None


class AppointmentOut(BaseModel):
    appointment_id: int
    patient_id: int
    patient_name: Optional[str] = None
    doctor_id: int
    doctor_name: Optional[str] = None
    specialty: Optional[str] = None
    room_number: Optional[str] = None
    floor_number: Optional[int] = None
    appointment_date: str
    appointment_time: str
    appointment_type: str
    status: str
    notes: Optional[str] = None
    created_at: Optional[str] = None


class TimeSlot(BaseModel):
    time: str           # "09:00"
    available: bool


class DoctorSlotsResponse(BaseModel):
    doctor_id: int
    doctor_name: str
    date: str
    slots: List[TimeSlot]


class SpecialtyListResponse(BaseModel):
    specialties: List[Dict[str, str]]   # [{key: "Cardiology", name_ar: "أمراض القلب"}]


class DoctorListForBooking(BaseModel):
    """Doctors available for booking in a specialty."""
    specialty: str
    specialty_ar: str
    doctors: list   # List of DoctorInfo


# ── Queue (Waiting Room) ──────────────────────────────────────

class QueueJoinRequest(BaseModel):
    """Patient joins a doctor's queue."""
    patient_id: int
    doctor_id: int
    consultation_id: Optional[int] = None
    appointment_id: Optional[int] = None
    severity_level: int = Field(5, ge=1, le=10)


class QueueStatusResponse(BaseModel):
    """Patient polls this to track their position."""
    queue_id: int
    patient_id: int
    doctor_id: int
    doctor_name: str
    doctor_specialty: str
    doctor_specialty_ar: str
    doctor_room: str
    doctor_floor: int
    doctor_rating: float
    doctor_image_url: Optional[str] = None
    doctor_status: str              # 'available' / 'busy'
    queue_status: str               # 'waiting' / 'called' / 'in_progress'
    queue_position: int
    people_ahead: int
    estimated_wait_minutes: int
    current_serving_position: Optional[int] = None
    notification_sent: bool = False
    your_turn: bool = False
    joined_at: Optional[str] = None
    called_at: Optional[str] = None


class QueueCancelResponse(BaseModel):
    success: bool
    message: str


# ── Notifications ──────────────────────────────────────────────

class NotificationOut(BaseModel):
    notification_id: int
    title: str
    message: str
    notification_type: str
    is_read: bool = False
    created_at: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class NotificationListResponse(BaseModel):
    patient_id: int
    unread_count: int
    notifications: List[NotificationOut]
