"""Appointment & Queue Router - booking, waiting room, notifications"""

from fastapi import APIRouter, HTTPException
from models.appointment import AppointmentBookRequest, QueueJoinRequest
from services import appointment_service as svc

router = APIRouter(tags=["Appointments & Queue"])

@router.get("/appointments/specialties")
async def list_specialties():
    return {"specialties": svc.get_all_specialties()}

@router.get("/appointments/doctors")
async def doctors_for_booking(specialty: str):
    doctors = svc.get_doctors_for_specialty(specialty)
    sp_ar = next((s["name_ar"] for s in svc.get_all_specialties() if s["key"] == specialty), specialty)
    return {"specialty": specialty, "specialty_ar": sp_ar, "doctors": doctors}

@router.get("/appointments/doctors/{doctor_id}/slots")
async def doctor_slots(doctor_id: int, date: str):
    result = svc.get_doctor_time_slots(doctor_id, date)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result

@router.post("/appointments/book")
async def book_appointment(req: AppointmentBookRequest):
    result = svc.book_appointment(patient_id=req.patient_id, doctor_id=req.doctor_id,
        appointment_date=req.appointment_date, appointment_time=req.appointment_time,
        appointment_type=req.appointment_type, notes=req.notes)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@router.get("/appointments/{appointment_id}")
async def get_appointment(appointment_id: int):
    result = svc.get_appointment(appointment_id)
    if not result:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return result

@router.put("/appointments/{appointment_id}/cancel")
async def cancel_appointment(appointment_id: int, reason: str = None):
    return svc.cancel_appointment(appointment_id, reason)

@router.get("/patients/{patient_id}/appointments")
async def patient_appointments(patient_id: int):
    return {"patient_id": patient_id, "appointments": svc.get_patient_appointments(patient_id)}

@router.post("/queue/join")
async def join_queue(req: QueueJoinRequest):
    result = svc.join_queue(patient_id=req.patient_id, doctor_id=req.doctor_id,
        consultation_id=req.consultation_id, appointment_id=req.appointment_id,
        severity_level=req.severity_level)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@router.get("/queue/status/{queue_id}")
async def queue_status(queue_id: int):
    result = svc.get_queue_status(queue_id)
    if not result:
        raise HTTPException(status_code=404, detail="Queue entry not found")
    return result

@router.get("/queue/patient/{patient_id}/active")
async def patient_active_queue(patient_id: int):
    result = svc.get_patient_active_queue(patient_id)
    if not result:
        return {"patient_id": patient_id, "in_queue": False, "message": "لا يوجد حجز حالياً"}
    # Map fields for Flutter app compatibility
    return {
        **result,
        "in_queue": True,
        # Flutter-friendly aliases
        "specialty": result.get("doctor_specialty", ""),
        "specialty_ar": result.get("doctor_specialty_ar", ""),
        "position": result.get("queue_position"),
        "status": result.get("queue_status"),
        "patients_ahead": result.get("people_ahead", 0),
        "join_time": result.get("joined_at"),
        "floor_number": str(result.get("doctor_floor", "")),
        "room_number": result.get("doctor_room", ""),
    }

@router.put("/queue/{queue_id}/cancel")
async def cancel_queue(queue_id: int):
    return svc.cancel_queue(queue_id)

@router.get("/queue/notifications/{patient_id}")
async def patient_notifications(patient_id: int):
    return svc.get_patient_notifications(patient_id)
