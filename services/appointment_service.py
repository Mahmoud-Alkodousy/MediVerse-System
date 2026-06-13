"""
MediVerse - Appointment & Queue Service
Handles direct booking, queue management, and waiting room logic.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from database.connection import DatabaseManager
from config.settings import settings
from models.appointment import (
    AppointmentOut, TimeSlot, QueueStatusResponse, NotificationOut,
)
from models.doctor import DoctorInfo, QueuePatientItem

logger = logging.getLogger("mediverse")


# =============================================================================
# SPECIALTIES
# =============================================================================

def get_all_specialties() -> List[Dict[str, str]]:
    """Return all specialties with Arabic names."""
    return [
        {"key": k, "name_ar": v}
        for k, v in settings.chatbot.SPECIALTIES.items()
    ]


# =============================================================================
# DOCTORS FOR BOOKING
# =============================================================================

def get_doctors_for_specialty(specialty: str) -> List[Dict]:
    """Get available doctors for a specialty (for dropdown in booking page)."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT doctor_id, doctor_name, specialty, room_number, floor_number,
                       rating, current_status, profile_image_url, estimated_wait_minutes,
                       current_patients_count, average_consultation_minutes,
                       years_of_experience, shift_start, shift_end, ISNULL(is_online, 0)
                FROM vw_AvailableDoctors
                WHERE specialty = ?
                ORDER BY is_online DESC, rating DESC, current_patients_count ASC
            """, (specialty,))
            doctors = []
            for row in cursor.fetchall():
                doctors.append({
                    "id": row[0],
                    "name": row[1],
                    "specialty": row[2],
                    "specialty_ar": settings.chatbot.SPECIALTIES.get(row[2], row[2]),
                    "room": row[3] or "N/A",
                    "floor": row[4] or 0,
                    "rating": float(row[5]) if row[5] else 0.0,
                    "current_status": row[6] or "available",
                    "profile_image_url": row[7],
                    "estimated_wait_minutes": row[8] or 0,
                    "current_patients": row[9] or 0,
                    "avg_consultation_minutes": row[10] or 15,
                    "years_of_experience": row[11],
                    "shift_start": str(row[12]) if row[12] else None,
                    "shift_end": str(row[13]) if row[13] else None,
                    "is_online": bool(row[14]),
                })
            cursor.close()
            return doctors
    except Exception as e:
        logger.error(f"Failed to get doctors for specialty {specialty}: {e}")
        return []


def get_doctor_time_slots(doctor_id: int, date_str: str) -> Dict:
    """Get available time slots for a doctor on a given date."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()

            # Get doctor info
            cursor.execute("""
                SELECT doctor_name, shift_start, shift_end, average_consultation_minutes
                FROM doctors WHERE doctor_id = ? AND is_active = 1
            """, (doctor_id,))
            doc = cursor.fetchone()
            if not doc:
                return {"error": "Doctor not found"}

            doctor_name = doc[0]
            shift_start = doc[1] or "09:00"
            shift_end = doc[2] or "17:00"
            slot_duration = doc[3] or 15

            # Get booked slots for that date
            cursor.execute("""
                SELECT appointment_time
                FROM appointments
                WHERE doctor_id = ? AND appointment_date = ? AND status NOT IN ('cancelled','no_show')
            """, (doctor_id, date_str))
            booked = {str(row[0])[:5] for row in cursor.fetchall()}
            cursor.close()

        # Extract time part (handles both TIME "09:00:00" and DATETIME "2025-01-01 09:00:00")
        def parse_time_part(val):
            s = str(val).strip()
            if " " in s:
                s = s.split(" ")[-1]  # get time part from datetime
            parts = s.split(":")[:2]
            return int(parts[0]), int(parts[1])

        start_h, start_m = parse_time_part(shift_start)
        end_h, end_m = parse_time_part(shift_end)

        slots = []
        current = datetime(2000, 1, 1, start_h, start_m)
        end_time = datetime(2000, 1, 1, end_h, end_m)

        # For today, mark past slots as unavailable
        from datetime import date as date_type
        is_today = date_str == str(date_type.today())
        now_h, now_m = 0, 0
        if is_today:
            from datetime import datetime as dt_now
            n = dt_now.now()
            now_h, now_m = n.hour, n.minute

        while current < end_time:
            time_str = current.strftime("%H:%M")
            is_past = is_today and (current.hour < now_h or (current.hour == now_h and current.minute <= now_m))
            slots.append({
                "time": time_str,
                "available": time_str not in booked and not is_past,
            })
            current += timedelta(minutes=slot_duration)

        return {
            "doctor_id": doctor_id,
            "doctor_name": doctor_name,
            "date": date_str,
            "shift_start": str(shift_start),
            "shift_end": str(shift_end),
            "slot_duration_minutes": slot_duration,
            "slots": slots,
        }
    except Exception as e:
        logger.error(f"Failed to get slots for doctor {doctor_id}: {e}")
        return {"error": str(e)}


# =============================================================================
# BOOK APPOINTMENT
# =============================================================================

def book_appointment(
    patient_id: int,
    doctor_id: int,
    appointment_date: str,
    appointment_time: str,
    appointment_type: str = "follow_up",
    notes: str = None,
) -> Dict:
    """Book a direct appointment (no chatbot)."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()

            # Check doctor exists
            cursor.execute("SELECT doctor_name, specialty FROM doctors WHERE doctor_id = ? AND is_active = 1", (doctor_id,))
            doc = cursor.fetchone()
            if not doc:
                return {"error": "Doctor not found or inactive"}

            # Check patient exists
            cursor.execute("SELECT full_name FROM Patients WHERE id = ? AND is_active = 1", (patient_id,))
            pat = cursor.fetchone()
            if not pat:
                return {"error": "Patient not found"}

            # Check slot availability
            cursor.execute("""
                SELECT COUNT(*) FROM appointments
                WHERE doctor_id = ? AND appointment_date = ? AND appointment_time = ?
                  AND status NOT IN ('cancelled','no_show')
            """, (doctor_id, appointment_date, appointment_time))
            if cursor.fetchone()[0] > 0:
                return {"error": "This time slot is already booked"}

            # Insert appointment
            cursor.execute("""
                INSERT INTO appointments (patient_id, doctor_id, appointment_date, appointment_time, 
                                          appointment_type, status, notes)
                OUTPUT INSERTED.appointment_id
                VALUES (?, ?, ?, ?, ?, 'scheduled', ?)
            """, (patient_id, doctor_id, appointment_date, appointment_time, appointment_type, notes))

            row = cursor.fetchone()
            appointment_id = row[0] if row else 0
            cursor.close()

        return {
            "success": True,
            "appointment_id": appointment_id,
            "message": f"تم الحجز بنجاح مع د. {doc[0]}",
            "doctor_name": doc[0],
            "specialty": doc[1],
        }
    except Exception as e:
        logger.error(f"Failed to book appointment: {e}")
        return {"error": str(e)}


def get_appointment(appointment_id: int) -> Optional[Dict]:
    """Get appointment details."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT a.appointment_id, a.patient_id, p.full_name, a.doctor_id,
                       d.doctor_name, d.specialty, d.room_number, d.floor_number,
                       a.appointment_date, a.appointment_time, a.appointment_type,
                       a.status, a.notes, a.created_at
                FROM appointments a
                JOIN Patients p ON a.patient_id = p.id
                JOIN doctors d ON a.doctor_id = d.doctor_id
                WHERE a.appointment_id = ?
            """, (appointment_id,))
            row = cursor.fetchone()
            cursor.close()

        if not row:
            return None

        return {
            "appointment_id": row[0],
            "patient_id": row[1],
            "patient_name": row[2],
            "doctor_id": row[3],
            "doctor_name": row[4],
            "specialty": row[5],
            "room_number": row[6],
            "floor_number": row[7],
            "appointment_date": str(row[8]),
            "appointment_time": str(row[9])[:5],
            "appointment_type": row[10],
            "status": row[11],
            "notes": row[12],
            "created_at": row[13].isoformat() if row[13] else None,
        }
    except Exception as e:
        logger.error(f"Failed to get appointment {appointment_id}: {e}")
        return None


def cancel_appointment(appointment_id: int, reason: str = None) -> Dict:
    """Cancel an appointment."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE appointments
                SET status = 'cancelled', cancellation_reason = ?
                WHERE appointment_id = ? AND status NOT IN ('completed','cancelled')
            """, (reason, appointment_id))
            affected = cursor.rowcount
            cursor.close()

        if affected == 0:
            return {"success": False, "message": "Appointment not found or already completed/cancelled"}
        return {"success": True, "message": "تم إلغاء الحجز بنجاح"}
    except Exception as e:
        logger.error(f"Failed to cancel appointment {appointment_id}: {e}")
        return {"success": False, "message": str(e)}


def get_patient_appointments(patient_id: int) -> List[Dict]:
    """Get all appointments for a patient."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT a.appointment_id, a.doctor_id, d.doctor_name, d.specialty,
                       d.room_number, d.floor_number, a.appointment_date,
                       a.appointment_time, a.appointment_type, a.status, a.created_at
                FROM appointments a
                JOIN doctors d ON a.doctor_id = d.doctor_id
                WHERE a.patient_id = ?
                ORDER BY a.appointment_date DESC, a.appointment_time DESC
            """, (patient_id,))
            results = []
            for row in cursor.fetchall():
                results.append({
                    "appointment_id": row[0],
                    "doctor_id": row[1],
                    "doctor_name": row[2],
                    "specialty": row[3],
                    "room_number": row[4],
                    "floor_number": row[5],
                    "appointment_date": str(row[6]),
                    "appointment_time": str(row[7])[:5],
                    "appointment_type": row[8],
                    "status": row[9],
                    "created_at": row[10].isoformat() if row[10] else None,
                })
            cursor.close()
            return results
    except Exception as e:
        logger.error(f"Failed to get appointments for patient {patient_id}: {e}")
        return []


# =============================================================================
# QUEUE MANAGEMENT
# =============================================================================

def join_queue(
    patient_id: int,
    doctor_id: int,
    consultation_id: int = None,
    appointment_id: int = None,
    severity_level: int = 5,
) -> Dict:
    """Add patient to a doctor's queue."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()

            # Auto-expire stale queue entries (older than today)
            cursor.execute("""
                UPDATE appointment_queue 
                SET queue_status = 'expired'
                WHERE patient_id = ? 
                  AND queue_status IN ('waiting','called')
                  AND CAST(joined_queue_at AS DATE) < CAST(GETDATE() AS DATE)
            """, (patient_id,))
            conn.commit()

            # Check if patient already in an active queue (today only)
            cursor.execute("""
                SELECT queue_id FROM appointment_queue
                WHERE patient_id = ? AND queue_status IN ('waiting','called')
                  AND CAST(joined_queue_at AS DATE) = CAST(GETDATE() AS DATE)
            """, (patient_id,))
            existing = cursor.fetchone()
            if existing:
                return {
                    "error": "Patient already in an active queue",
                    "existing_queue_id": existing[0],
                }

            # Calculate priority
            if severity_level >= 8:
                priority = 10
            elif severity_level >= 6:
                priority = 7
            elif severity_level >= 4:
                priority = 5
            else:
                priority = 3

            # Get next position
            cursor.execute("""
                SELECT ISNULL(MAX(queue_position), 0)
                FROM appointment_queue
                WHERE doctor_id = ? AND queue_status = 'waiting'
            """, (doctor_id,))
            last_pos = cursor.fetchone()[0]
            new_pos = last_pos + 1

            # Calculate estimated wait
            cursor.execute("""
                SELECT average_consultation_minutes FROM doctors WHERE doctor_id = ?
            """, (doctor_id,))
            avg_min = cursor.fetchone()
            avg_min = avg_min[0] if avg_min else 15

            # Count people ahead
            cursor.execute("""
                SELECT COUNT(*) FROM appointment_queue
                WHERE doctor_id = ? AND queue_status IN ('waiting','called','in_progress')
            """, (doctor_id,))
            people_ahead = cursor.fetchone()[0]
            estimated_wait = people_ahead * avg_min

            # Insert queue entry
            cursor.execute("""
                INSERT INTO appointment_queue (
                    patient_id, doctor_id, consultation_id, appointment_id,
                    severity_level, queue_position, queue_status,
                    joined_queue_at, initial_priority, current_priority,
                    estimated_wait_minutes
                )
                OUTPUT INSERTED.queue_id
                VALUES (?, ?, ?, ?, ?, ?, 'waiting', GETDATE(), ?, ?, ?)
            """, (
                patient_id, doctor_id, consultation_id, appointment_id,
                severity_level, new_pos, priority, priority, estimated_wait,
            ))
            row = cursor.fetchone()
            queue_id = row[0] if row else 0

            # Update doctor status if needed
            cursor.execute("""
                UPDATE doctors SET current_status = 'busy'
                WHERE doctor_id = ? AND current_status = 'available'
            """, (doctor_id,))

            cursor.close()

        return {
            "success": True,
            "queue_id": queue_id,
            "position": new_pos,
            "people_ahead": people_ahead,
            "estimated_wait_minutes": estimated_wait,
            "message": f"تم الانضمام للصف. ترتيبك: {new_pos}، الوقت المتوقع: {estimated_wait} دقيقة",
        }
    except Exception as e:
        logger.error(f"Failed to join queue: {e}")
        return {"error": str(e)}


def get_queue_status(queue_id: int) -> Optional[Dict]:
    """Get current queue status for a patient (used for polling)."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    q.queue_id, q.patient_id, q.doctor_id, q.queue_status,
                    q.queue_position, q.joined_queue_at, q.called_at,
                    q.estimated_wait_minutes, q.notification_sent, q.severity_level,
                    d.doctor_name, d.specialty, d.room_number, d.floor_number,
                    d.rating, d.current_status, d.profile_image_url,
                    d.average_consultation_minutes
                FROM appointment_queue q
                JOIN doctors d ON q.doctor_id = d.doctor_id
                WHERE q.queue_id = ?
            """, (queue_id,))
            row = cursor.fetchone()
            if not row:
                cursor.close()
                return None

            doctor_id = row[2]
            queue_status = row[3]
            my_position = row[4]

            # Count people ahead of me
            cursor.execute("""
                SELECT COUNT(*) FROM appointment_queue
                WHERE doctor_id = ? AND queue_status IN ('waiting','called','in_progress')
                  AND (current_priority > ? OR (current_priority = ? AND queue_position < ?))
            """, (doctor_id, row[9], row[9], my_position))
            # Simpler: count waiting before my position
            cursor.execute("""
                SELECT COUNT(*) FROM appointment_queue
                WHERE doctor_id = ? AND queue_status = 'waiting' AND queue_position < ?
            """, (doctor_id, my_position))
            people_ahead = cursor.fetchone()[0]

            # Current serving position
            cursor.execute("""
                SELECT TOP 1 queue_position FROM appointment_queue
                WHERE doctor_id = ? AND queue_status = 'in_progress'
            """, (doctor_id,))
            current_row = cursor.fetchone()
            current_serving = current_row[0] if current_row else None

            avg_min = row[17] or 15
            estimated_wait = people_ahead * avg_min

            your_turn = queue_status in ("called",)

            cursor.close()

            specialty = row[11] or ""
            return {
                "queue_id": row[0],
                "patient_id": row[1],
                "doctor_id": doctor_id,
                "doctor_name": row[10],
                "doctor_specialty": specialty,
                "doctor_specialty_ar": settings.chatbot.SPECIALTIES.get(specialty, specialty),
                "doctor_room": row[12] or "N/A",
                "doctor_floor": row[13] or 0,
                "doctor_rating": float(row[14]) if row[14] else 0.0,
                "doctor_image_url": row[16],
                "doctor_status": row[15] or "busy",
                "queue_status": queue_status,
                "queue_position": my_position,
                "people_ahead": people_ahead,
                "estimated_wait_minutes": estimated_wait,
                "current_serving_position": current_serving,
                "notification_sent": bool(row[8]),
                "your_turn": your_turn,
                "joined_at": row[5].isoformat() if row[5] else None,
                "called_at": row[6].isoformat() if row[6] else None,
            }
    except Exception as e:
        logger.error(f"Failed to get queue status for {queue_id}: {e}")
        return None


def cancel_queue(queue_id: int) -> Dict:
    """Cancel a queue entry (patient left)."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE appointment_queue
                SET queue_status = 'cancelled', completed_at = GETDATE()
                WHERE queue_id = ? AND queue_status IN ('waiting','called')
            """, (queue_id,))
            affected = cursor.rowcount
            cursor.close()

        if affected == 0:
            return {"success": False, "message": "Queue entry not found or already processed"}
        return {"success": True, "message": "تم إلغاء الدور بنجاح"}
    except Exception as e:
        logger.error(f"Failed to cancel queue {queue_id}: {e}")
        return {"success": False, "message": str(e)}


def get_patient_active_queue(patient_id: int) -> Optional[Dict]:
    """Get patient's active queue entry (if any). Only today's entries count."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            # Auto-expire stale entries from previous days
            cursor.execute("""
                UPDATE appointment_queue 
                SET queue_status = 'expired'
                WHERE patient_id = ? 
                  AND queue_status IN ('waiting','called','in_progress')
                  AND CAST(joined_queue_at AS DATE) < CAST(GETDATE() AS DATE)
            """, (patient_id,))
            conn.commit()

            cursor.execute("""
                SELECT queue_id FROM appointment_queue
                WHERE patient_id = ? AND queue_status IN ('waiting','called','in_progress')
                  AND CAST(joined_queue_at AS DATE) = CAST(GETDATE() AS DATE)
                ORDER BY joined_queue_at DESC
            """, (patient_id,))
            row = cursor.fetchone()
            cursor.close()

        if not row:
            return None
        return get_queue_status(row[0])
    except Exception as e:
        logger.error(f"Failed to get active queue for patient {patient_id}: {e}")
        return None


# =============================================================================
# NOTIFICATIONS
# =============================================================================

def get_patient_notifications(patient_id: int, unread_only: bool = True) -> Dict:
    """Get notifications for a patient."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT notification_id, title, message, notification_type,
                       is_read, created_at, data_json
                FROM notifications
                WHERE target_type = 'patient' AND target_id = ?
            """
            if unread_only:
                query += " AND is_read = 0"
            query += " ORDER BY created_at DESC"

            cursor.execute(query, (patient_id,))
            notifications = []
            for row in cursor.fetchall():
                data = None
                if row[6]:
                    try:
                        data = json.loads(row[6])
                    except Exception:
                        pass
                notifications.append({
                    "notification_id": row[0],
                    "title": row[1],
                    "message": row[2],
                    "notification_type": row[3],
                    "is_read": bool(row[4]),
                    "created_at": row[5].isoformat() if row[5] else None,
                    "data": data,
                })

            # Count unread
            cursor.execute("""
                SELECT COUNT(*) FROM notifications
                WHERE target_type = 'patient' AND target_id = ? AND is_read = 0
            """, (patient_id,))
            unread_count = cursor.fetchone()[0]
            cursor.close()

        return {
            "patient_id": patient_id,
            "unread_count": unread_count,
            "notifications": notifications,
        }
    except Exception as e:
        logger.error(f"Failed to get notifications for patient {patient_id}: {e}")
        return {"patient_id": patient_id, "unread_count": 0, "notifications": []}
