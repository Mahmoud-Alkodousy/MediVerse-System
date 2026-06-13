"""Patient Router - profile, history, last visit, chat history. Face/NationalID/Register stay in main.py for backward compat."""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException
from database.connection import DatabaseManager
from models.patient import PatientUpdate, PatientProfile, PatientHistoryItem
from utils.helpers import safe_isoformat, calculate_age, days_between, calculate_bmi

logger = logging.getLogger("mediverse")
router = APIRouter(tags=["Patient"])

@router.get("/patients/{patient_id}")
async def get_patient_profile(patient_id: int):
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, full_name, national_id, gender, date_of_birth, phone_number,
                       address, blood_type, chronic_diseases, allergies, current_medications,
                       marital_status, job, weight, height, BMI, profile_image_url,
                       emergency_contact, last_visit_date, created_at
                FROM Patients WHERE id = ? AND is_active = 1
            """, (patient_id,))
            r = cursor.fetchone()
            cursor.close()
        if not r:
            raise HTTPException(status_code=404, detail="Patient not found")
        age = calculate_age(r[4])
        days = days_between(r[18])
        return {
            "id": r[0], "full_name": r[1], "national_id": r[2], "gender": r[3],
            "date_of_birth": str(r[4]) if r[4] else None, "phone_number": r[5],
            "address": r[6], "blood_type": r[7], "chronic_diseases": r[8],
            "allergies": r[9], "current_medications": r[10], "marital_status": r[11],
            "job": r[12], "weight": r[13], "height": r[14], "BMI": r[15],
            "profile_image_url": r[16], "emergency_contact": r[17],
            "last_visit_date": safe_isoformat(r[18]), "created_at": safe_isoformat(r[19]),
            "age": age, "days_since_last_visit": days,
            "follow_up_suggested": days is not None and days >= 30,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/patients/{patient_id}")
async def update_patient(patient_id: int, req: PatientUpdate):
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        return {"success": False, "message": "Nothing to update"}
    # Auto-calculate BMI
    if "weight" in fields or "height" in fields:
        w = fields.get("weight")
        h = fields.get("height")
        if w and h:
            fields["BMI"] = calculate_bmi(w, h)
    try:
        sql = "UPDATE Patients SET " + ",".join(f"{k}=?" for k in fields) + ",updated_at=GETDATE() WHERE id=?"
        with DatabaseManager.get_connection() as conn:
            conn.cursor().execute(sql, tuple(list(fields.values()) + [patient_id]))
        return {"success": True, "message": "Profile updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/patients/{patient_id}/history")
async def patient_history(patient_id: int):
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT pc.consultation_id, pc.symptoms_reported, pc.ai_preliminary_diagnosis,
                       pc.ai_severity_assessment, pc.ai_specialty_suggested,
                       d.doctor_name, pc.doctor_notes, pc.consultation_date, pc.llm_model_used
                FROM patient_consultations pc
                LEFT JOIN doctors d ON pc.doctor_id = d.doctor_id
                WHERE pc.patient_id = ?
                ORDER BY pc.consultation_date DESC
            """, (patient_id,))
            items = [{"consultation_id": r[0], "symptoms": r[1], "diagnosis": r[2],
                      "severity": r[3], "specialty": r[4], "doctor_name": r[5],
                      "doctor_notes": r[6], "date": safe_isoformat(r[7]), "model_used": r[8]}
                     for r in cursor.fetchall()]
            cursor.close()
        return {"patient_id": patient_id, "total": len(items), "consultations": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/patients/{patient_id}/last-visit")
async def last_visit(patient_id: int):
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT last_visit_date FROM Patients WHERE id=?", (patient_id,))
            r = cursor.fetchone()
            cursor.close()
        if not r or not r[0]:
            return {"patient_id": patient_id, "last_visit_date": None, "days_since_last_visit": None,
                    "follow_up_suggested": False, "message": "No previous visits"}
        days = days_between(r[0])
        return {"patient_id": patient_id, "last_visit_date": safe_isoformat(r[0]),
                "days_since_last_visit": days, "follow_up_suggested": days is not None and days >= 30,
                "message": f"Last visit was {days} days ago" + (" - consider scheduling a follow-up" if days and days >= 30 else "")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# CHAT HISTORY
# ============================================================================

@router.get("/patients/{patient_id}/chat-sessions")
async def get_patient_chat_sessions(patient_id: int):
    """Get all chat sessions for a patient (list of conversations)."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT cs.session_id, cs.session_start, cs.session_end, cs.session_status,
                       cs.device_type,
                       (SELECT COUNT(*) FROM chat_messages cm WHERE cm.session_id = cs.session_id) as msg_count,
                       (SELECT TOP 1 cm2.message_text FROM chat_messages cm2 
                        WHERE cm2.session_id = cs.session_id AND cm2.sender_type = 'user'
                        ORDER BY cm2.timestamp ASC) as first_message
                FROM chat_sessions cs
                WHERE cs.patient_id = ?
                ORDER BY cs.session_start DESC
            """, (patient_id,))
            sessions = []
            for r in cursor.fetchall():
                sessions.append({
                    "session_id": r[0],
                    "session_start": safe_isoformat(r[1]),
                    "session_end": safe_isoformat(r[2]),
                    "status": r[3],
                    "device_type": r[4],
                    "message_count": r[5],
                    "preview": (r[6][:80] + "...") if r[6] and len(r[6]) > 80 else r[6],
                })
            cursor.close()
        return {"patient_id": patient_id, "total_sessions": len(sessions), "sessions": sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/patients/{patient_id}/chat-sessions/{session_id}")
async def get_chat_messages(patient_id: int, session_id: int):
    """Get all messages in a specific chat session."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            # Verify session belongs to patient
            cursor.execute(
                "SELECT session_id, session_start, session_status FROM chat_sessions WHERE session_id = ? AND patient_id = ?",
                (session_id, patient_id))
            session = cursor.fetchone()
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            cursor.execute("""
                SELECT message_id, sender_type, message_text, message_type,
                       llm_model, processing_time_ms, timestamp
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY timestamp ASC
            """, (session_id,))
            messages = []
            for r in cursor.fetchall():
                messages.append({
                    "message_id": r[0],
                    "sender": r[1],
                    "text": r[2],
                    "type": r[3],
                    "model": r[4],
                    "processing_time_ms": r[5],
                    "timestamp": safe_isoformat(r[6]),
                })
            cursor.close()
        return {
            "session_id": session_id,
            "patient_id": patient_id,
            "session_start": safe_isoformat(session[1]),
            "status": session[2],
            "total_messages": len(messages),
            "messages": messages,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
