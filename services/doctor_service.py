"""
MediVerse - Doctor Service
Business logic for the doctor dashboard.
"""

import json
import logging
from typing import Optional, Dict
from database.connection import DatabaseManager
from config.settings import settings
from utils.helpers import safe_isoformat

logger = logging.getLogger("mediverse")


def get_doctor_profile(doctor_id: int) -> Optional[Dict]:
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT doctor_id, doctor_name, email, national_id, phone_number,
                       specialty, profile_image_url, license_number, years_of_experience,
                       room_number, floor_number, current_status, current_patients_count,
                       max_patients_per_day, average_consultation_minutes, rating,
                       total_ratings, shift_start, shift_end, working_days, is_active
                FROM doctors WHERE doctor_id = ?
            """, (doctor_id,))
            r = cursor.fetchone()
            cursor.close()
        if not r:
            return None
        sp = r[5] or ""
        return {
            "doctor_id": r[0], "doctor_name": r[1], "email": r[2], "national_id": r[3],
            "phone_number": r[4], "specialty": sp,
            "specialty_ar": settings.chatbot.SPECIALTIES.get(sp, sp),
            "profile_image_url": r[6], "license_number": r[7], "years_of_experience": r[8],
            "room_number": r[9], "floor_number": r[10], "current_status": r[11] or "available",
            "current_patients_count": r[12] or 0, "max_patients_per_day": r[13] or 20,
            "average_consultation_minutes": r[14] or 15,
            "rating": float(r[15]) if r[15] else 0.0, "total_ratings": r[16] or 0,
            "shift_start": str(r[17]) if r[17] else None,
            "shift_end": str(r[18]) if r[18] else None,
            "working_days": r[19], "is_active": bool(r[20]) if r[20] is not None else True,
        }
    except Exception as e:
        logger.error(f"get_doctor_profile error: {e}")
        return None


def update_doctor_profile(doctor_id: int, updates: Dict) -> Dict:
    allowed = {"phone_number", "profile_image_url", "average_consultation_minutes",
               "shift_start", "shift_end", "working_days"}
    fields = {k: v for k, v in updates.items() if k in allowed and v is not None}
    if not fields:
        return {"success": False, "message": "No valid fields"}
    try:
        sql = "UPDATE doctors SET " + ", ".join(f"{k}=?" for k in fields) + " WHERE doctor_id=?"
        with DatabaseManager.get_connection() as conn:
            conn.cursor().execute(sql, tuple(list(fields.values()) + [doctor_id]))
        return {"success": True, "message": "Profile updated"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def update_doctor_status(doctor_id: int, status: str) -> Dict:
    try:
        with DatabaseManager.get_connection() as conn:
            conn.cursor().execute("UPDATE doctors SET current_status=? WHERE doctor_id=?", (status, doctor_id))
        return {"success": True, "message": f"Status → {status}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def get_doctor_queue(doctor_id: int) -> Dict:
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT doctor_name FROM doctors WHERE doctor_id=?", (doctor_id,))
            doc = cursor.fetchone()
            doctor_name = doc[0] if doc else "Unknown"
            cursor.execute("""
                SELECT q.queue_id, q.patient_id, p.full_name, p.phone_number,
                       p.gender, p.date_of_birth, p.blood_type, p.chronic_diseases,
                       p.allergies, q.severity_level, q.queue_position, q.queue_status,
                       q.joined_queue_at, q.called_at, q.estimated_wait_minutes,
                       q.consultation_id, pc.symptoms_reported, pc.ai_preliminary_diagnosis,
                       pc.ai_severity_assessment, pc.ai_specialty_suggested
                FROM appointment_queue q JOIN Patients p ON q.patient_id=p.id
                LEFT JOIN patient_consultations pc ON q.consultation_id=pc.consultation_id
                WHERE q.doctor_id=? AND q.queue_status IN ('waiting','called','in_progress')
                ORDER BY CASE q.queue_status WHEN 'in_progress' THEN 0 WHEN 'called' THEN 1 ELSE 2 END,
                         q.current_priority DESC, q.queue_position ASC
            """, (doctor_id,))
            queue, current_patient, total_waiting = [], None, 0
            for r in cursor.fetchall():
                item = {"queue_id": r[0], "patient_id": r[1], "patient_name": r[2],
                        "patient_phone": r[3], "gender": r[4],
                        "date_of_birth": str(r[5]) if r[5] else None,
                        "blood_type": r[6], "chronic_diseases": r[7], "allergies": r[8],
                        "severity_level": r[9] or 5, "queue_position": r[10],
                        "queue_status": r[11], "joined_queue_at": safe_isoformat(r[12]),
                        "called_at": safe_isoformat(r[13]), "estimated_wait_minutes": r[14],
                        "symptoms": r[16], "ai_diagnosis": r[17], "ai_severity": r[18], "ai_specialty": r[19]}
                queue.append(item)
                if r[11] in ("in_progress", "called"):
                    current_patient = item
                if r[11] == "waiting":
                    total_waiting += 1
            cursor.close()
        return {"doctor_id": doctor_id, "doctor_name": doctor_name,
                "total_waiting": total_waiting, "current_patient": current_patient, "queue": queue}
    except Exception as e:
        logger.error(f"get_doctor_queue error: {e}")
        return {"doctor_id": doctor_id, "doctor_name": "", "total_waiting": 0, "current_patient": None, "queue": []}


def call_next_patient(doctor_id: int) -> Dict:
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            # Complete current
            cursor.execute("UPDATE appointment_queue SET queue_status='completed', completed_at=GETDATE() WHERE doctor_id=? AND queue_status='in_progress'", (doctor_id,))
            # Move called → in_progress
            cursor.execute("SELECT queue_id, patient_id FROM appointment_queue WHERE doctor_id=? AND queue_status='called'", (doctor_id,))
            called = cursor.fetchone()
            if called:
                cursor.execute("UPDATE appointment_queue SET queue_status='in_progress', started_at=GETDATE() WHERE queue_id=?", (called[0],))
                cursor.close()
                remaining = _count_w(conn, doctor_id)
                patient = _get_qp(conn, called[0])
                return {"success": True, "message": "المريض يدخل الآن", "patient": patient, "remaining_in_queue": remaining}
            # Find next waiting
            cursor.execute("SELECT TOP 1 queue_id, patient_id FROM appointment_queue WHERE doctor_id=? AND queue_status='waiting' ORDER BY current_priority DESC, queue_position ASC", (doctor_id,))
            nxt = cursor.fetchone()
            if not nxt:
                cursor.execute("UPDATE doctors SET current_status='available' WHERE doctor_id=?", (doctor_id,))
                cursor.close()
                return {"success": True, "message": "لا يوجد مرضى في الانتظار", "patient": None, "remaining_in_queue": 0}
            cursor.execute("UPDATE appointment_queue SET queue_status='called', called_at=GETDATE(), notification_sent=1 WHERE queue_id=?", (nxt[0],))
            cursor.execute("INSERT INTO notifications (target_type,target_id,title,message,notification_type,data_json) VALUES ('patient',?,N'دورك الآن! 🎉',N'يرجى التوجه إلى الطبيب الآن','queue_turn',?)", (nxt[1], json.dumps({"queue_id": nxt[0], "doctor_id": doctor_id})))
            cursor.close()
            remaining = _count_w(conn, doctor_id)
            patient = _get_qp(conn, nxt[0])
        return {"success": True, "message": "تم استدعاء المريض التالي", "patient": patient, "remaining_in_queue": remaining}
    except Exception as e:
        logger.error(f"call_next_patient error: {e}")
        return {"success": False, "message": str(e), "patient": None, "remaining_in_queue": 0}


def complete_patient(doctor_id: int, queue_id: int) -> Dict:
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE appointment_queue SET queue_status='completed', completed_at=GETDATE() WHERE queue_id=? AND doctor_id=? AND queue_status IN ('called','in_progress')", (queue_id, doctor_id))
            cursor.execute("UPDATE Patients SET last_visit_date=GETDATE(), updated_at=GETDATE() WHERE id=(SELECT patient_id FROM appointment_queue WHERE queue_id=?)", (queue_id,))
            cursor.close()
        return {"success": True, "message": "تم إنهاء الكشف"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def mark_no_show(doctor_id: int, queue_id: int) -> Dict:
    try:
        with DatabaseManager.get_connection() as conn:
            conn.cursor().execute("UPDATE appointment_queue SET queue_status='no_show', completed_at=GETDATE() WHERE queue_id=? AND doctor_id=? AND queue_status IN ('called','waiting')", (queue_id, doctor_id))
        return {"success": True, "message": "تم تسجيل عدم الحضور"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def add_doctor_note(doctor_id: int, data: Dict) -> Dict:
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO doctor_notes (doctor_id, patient_id, consultation_id, queue_id, diagnosis, prescription, notes, follow_up_date)
                OUTPUT INSERTED.note_id VALUES (?,?,?,?,?,?,?,?)
            """, (doctor_id, data.get("patient_id"), data.get("consultation_id"), data.get("queue_id"),
                  data.get("diagnosis"), data.get("prescription"), data.get("notes"), data.get("follow_up_date")))
            row = cursor.fetchone()
            note_id = row[0] if row else 0
            if data.get("consultation_id") and data.get("diagnosis"):
                cursor.execute("UPDATE patient_consultations SET actual_diagnosis=?, doctor_notes=?, doctor_id=? WHERE consultation_id=?",
                               (data["diagnosis"], data.get("notes"), doctor_id, data["consultation_id"]))
            cursor.close()
        return {"success": True, "note_id": note_id, "message": "تم حفظ الملاحظات"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def get_doctor_stats(doctor_id: int) -> Dict:
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM appointment_queue WHERE doctor_id=? AND queue_status='waiting'", (doctor_id,))
            waiting = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM appointment_queue WHERE doctor_id=? AND queue_status='completed' AND CAST(completed_at AS DATE)=CAST(GETDATE() AS DATE)", (doctor_id,))
            completed = cursor.fetchone()[0]
            cursor.execute("SELECT AVG(DATEDIFF(MINUTE,joined_queue_at,called_at)) FROM appointment_queue WHERE doctor_id=? AND called_at IS NOT NULL AND CAST(joined_queue_at AS DATE)=CAST(GETDATE() AS DATE)", (doctor_id,))
            avg_w = cursor.fetchone()[0] or 0
            cursor.execute("SELECT COUNT(*) FROM patient_consultations WHERE doctor_id=?", (doctor_id,))
            total_c = cursor.fetchone()[0]
            cursor.execute("SELECT rating FROM doctors WHERE doctor_id=?", (doctor_id,))
            rat = cursor.fetchone()
            cursor.close()
        return {"patients_today": completed, "patients_waiting": waiting, "avg_wait_minutes": round(avg_w, 1),
                "total_consultations": total_c, "avg_rating": float(rat[0]) if rat and rat[0] else 0.0, "completed_today": completed}
    except Exception as e:
        return {"patients_today": 0, "patients_waiting": 0, "avg_wait_minutes": 0,
                "total_consultations": 0, "avg_rating": 0, "completed_today": 0}


def get_patient_full_info(patient_id: int) -> Optional[Dict]:
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id,full_name,national_id,gender,date_of_birth,phone_number,address,blood_type,chronic_diseases,allergies,current_medications,marital_status,job,weight,height,BMI,last_visit_date FROM Patients WHERE id=?", (patient_id,))
            p = cursor.fetchone()
            if not p:
                return None
            cursor.execute("SELECT TOP 10 consultation_id,symptoms_reported,ai_preliminary_diagnosis,ai_severity_assessment,ai_specialty_suggested,doctor_notes,actual_diagnosis,consultation_date,llm_model_used FROM patient_consultations WHERE patient_id=? ORDER BY consultation_date DESC", (patient_id,))
            consults = [{"consultation_id": r[0], "symptoms": r[1], "ai_diagnosis": r[2], "severity": r[3], "specialty": r[4], "doctor_notes": r[5], "actual_diagnosis": r[6], "date": safe_isoformat(r[7]), "model_used": r[8]} for r in cursor.fetchall()]
            cursor.execute("SELECT TOP 10 dn.note_id,dn.diagnosis,dn.prescription,dn.notes,dn.follow_up_date,dn.created_at,d.doctor_name FROM doctor_notes dn JOIN doctors d ON dn.doctor_id=d.doctor_id WHERE dn.patient_id=? ORDER BY dn.created_at DESC", (patient_id,))
            notes = [{"note_id": r[0], "diagnosis": r[1], "prescription": r[2], "notes": r[3], "follow_up_date": str(r[4]) if r[4] else None, "created_at": safe_isoformat(r[5]), "doctor_name": r[6]} for r in cursor.fetchall()]
            cursor.close()
        return {
            "patient": {"id": p[0], "full_name": p[1], "national_id": p[2], "gender": p[3], "date_of_birth": str(p[4]) if p[4] else None, "phone_number": p[5], "address": p[6], "blood_type": p[7], "chronic_diseases": p[8], "allergies": p[9], "current_medications": p[10], "marital_status": p[11], "job": p[12], "weight": p[13], "height": p[14], "BMI": p[15], "last_visit_date": safe_isoformat(p[16])},
            "consultations": consults, "doctor_notes": notes}
    except Exception as e:
        logger.error(f"get_patient_full_info error: {e}")
        return None


def _count_w(conn, doctor_id):
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM appointment_queue WHERE doctor_id=? AND queue_status='waiting'", (doctor_id,))
    return c.fetchone()[0]

def _get_qp(conn, queue_id):
    c = conn.cursor()
    c.execute("SELECT q.queue_id,q.patient_id,p.full_name,p.phone_number,q.severity_level,q.queue_position,q.queue_status,pc.symptoms_reported,pc.ai_preliminary_diagnosis,pc.ai_severity_assessment FROM appointment_queue q JOIN Patients p ON q.patient_id=p.id LEFT JOIN patient_consultations pc ON q.consultation_id=pc.consultation_id WHERE q.queue_id=?", (queue_id,))
    r = c.fetchone()
    if not r:
        return None
    return {"queue_id": r[0], "patient_id": r[1], "patient_name": r[2], "patient_phone": r[3], "severity_level": r[4], "queue_position": r[5], "queue_status": r[6], "symptoms": r[7], "ai_diagnosis": r[8], "ai_severity": r[9]}
