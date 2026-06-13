"""Manager Dashboard Router - /manager/* endpoints (JWT protected)"""

import logging
from fastapi import APIRouter, HTTPException, Depends
from models.auth import CurrentUser
from models.doctor import DoctorCreate, DoctorManagerUpdate
from services.auth_service import require_manager, hash_password
from database.connection import DatabaseManager
from config.settings import settings
from utils.helpers import safe_isoformat

logger = logging.getLogger("mediverse")
router = APIRouter(prefix="/manager", tags=["Manager Dashboard"])

@router.get("/dashboard")
async def dashboard_overview(user: CurrentUser = Depends(require_manager)):
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM vw_ManagerDashboard")
            r = cursor.fetchone()
            cursor.close()
        if not r:
            return {}
        return {"total_patients": r[0], "total_doctors": r[1], "available_doctors": r[2],
                "total_waiting": r[3], "critical_waiting": r[4], "today_appointments": r[5],
                "today_consultations": r[6], "avg_wait_minutes": round(float(r[7] or 0), 1)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/doctors")
async def list_doctors(user: CurrentUser = Depends(require_manager)):
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT doctor_id, doctor_name, email, specialty, room_number, floor_number,
                       current_status, current_patients_count, rating, is_active, phone_number,
                       gender, date_of_birth, marital_status, work_schedule,
                       consultation_fee_egp, certifications, languages_spoken,
                       years_of_experience, ISNULL(is_online, 0) as is_online
                FROM doctors ORDER BY doctor_name
            """)
            doctors = []
            for r in cursor.fetchall():
                sp = r[3] or ""
                age = None
                if r[12]:
                    from utils.helpers import calculate_age
                    age = calculate_age(r[12])
                doctors.append({
                    "doctor_id": r[0], "doctor_name": r[1], "email": r[2],
                    "specialty": sp, "specialty_ar": settings.chatbot.SPECIALTIES.get(sp, sp),
                    "room_number": r[4], "floor_number": r[5],
                    "current_status": r[6], "current_patients_count": r[7],
                    "rating": float(r[8]) if r[8] else 0, "is_active": bool(r[9]),
                    "phone_number": r[10], "gender": r[11],
                    "date_of_birth": str(r[12]) if r[12] else None, "age": age,
                    "marital_status": r[13], "work_schedule": r[14],
                    "consultation_fee_egp": float(r[15]) if r[15] else None,
                    "certifications": r[16], "languages_spoken": r[17],
                    "years_of_experience": r[18],
                    "is_online": bool(r[19]),
                })
            cursor.close()
        return {"doctors": doctors, "total": len(doctors)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/doctors")
async def create_doctor(req: DoctorCreate, user: CurrentUser = Depends(require_manager)):
    try:
        pw_hash = hash_password(req.password)
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO doctors (
                    doctor_name, email, password_hash, national_id, phone_number,
                    specialty, room_number, floor_number, license_number,
                    years_of_experience, max_patients_per_day, average_consultation_minutes,
                    gender, date_of_birth, marital_status, work_schedule,
                    consultation_fee_egp, certifications, languages_spoken
                ) OUTPUT INSERTED.doctor_id VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                req.doctor_name, req.email, pw_hash, req.national_id, req.phone_number,
                req.specialty, req.room_number, req.floor_number, req.license_number,
                req.years_of_experience, req.max_patients_per_day, req.average_consultation_minutes,
                req.gender, req.date_of_birth, req.marital_status, req.work_schedule,
                req.consultation_fee_egp, req.certifications, req.languages_spoken
            ))
            row = cursor.fetchone()
            cursor.close()
        return {"success": True, "doctor_id": row[0] if row else 0, "message": "Doctor created"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/doctors/{doctor_id}")
async def update_doctor(doctor_id: int, req: DoctorManagerUpdate, user: CurrentUser = Depends(require_manager)):
    fields = {k:v for k,v in req.model_dump().items() if v is not None}
    if not fields:
        return {"success":False,"message":"Nothing to update"}
    try:
        sql = "UPDATE doctors SET " + ",".join(f"{k}=?" for k in fields) + " WHERE doctor_id=?"
        with DatabaseManager.get_connection() as conn:
            conn.cursor().execute(sql, tuple(list(fields.values())+[doctor_id]))
        return {"success":True,"message":"Doctor updated"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/doctors/{doctor_id}/activate")
async def toggle_doctor(doctor_id: int, active: bool = True, user: CurrentUser = Depends(require_manager)):
    try:
        with DatabaseManager.get_connection() as conn:
            conn.cursor().execute("UPDATE doctors SET is_active=? WHERE doctor_id=?", (1 if active else 0, doctor_id))
        return {"success":True,"message":f"Doctor {'activated' if active else 'deactivated'}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/patients")
async def list_patients(page:int=1, size:int=20, search:str=None, user:CurrentUser=Depends(require_manager)):
    try:
        offset=(page-1)*size
        with DatabaseManager.get_connection() as conn:
            cursor=conn.cursor()
            where,params="WHERE is_active=1",[]
            if search:
                where+=" AND (full_name LIKE ? OR national_id LIKE ? OR phone_number LIKE ?)"
                s=f"%{search}%"; params=[s,s,s]
            cursor.execute(f"SELECT COUNT(*) FROM Patients {where}", tuple(params))
            total=cursor.fetchone()[0]
            cursor.execute(f"SELECT id,full_name,national_id,gender,phone_number,date_of_birth,last_visit_date FROM Patients {where} ORDER BY id DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY", tuple(params+[offset,size]))
            patients=[{"id":r[0],"full_name":r[1],"national_id":r[2],"gender":r[3],"phone_number":r[4],"date_of_birth":str(r[5]) if r[5] else None,"last_visit_date":safe_isoformat(r[6])} for r in cursor.fetchall()]
            cursor.close()
        return {"total":total,"page":page,"page_size":size,"items":patients}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/queue/live")
async def live_queue(user: CurrentUser = Depends(require_manager)):
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT q.queue_id,p.full_name,d.doctor_name,d.specialty,q.severity_level,q.queue_status,q.joined_queue_at,q.estimated_wait_minutes FROM appointment_queue q JOIN Patients p ON q.patient_id=p.id JOIN doctors d ON q.doctor_id=d.doctor_id WHERE q.queue_status IN ('waiting','called','in_progress') ORDER BY q.severity_level DESC, q.joined_queue_at ASC")
            items=[{"queue_id":r[0],"patient_name":r[1],"doctor_name":r[2],"specialty":r[3],"severity_level":r[4],"queue_status":r[5],"waiting_since":safe_isoformat(r[6]),"estimated_wait":r[7]} for r in cursor.fetchall()]
            cursor.close()
        return {"total_in_queue":len(items),"items":items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/reports/daily")
async def daily_report(date:str=None, user:CurrentUser=Depends(require_manager)):
    try:
        with DatabaseManager.get_connection() as conn:
            cursor=conn.cursor()
            d = date or None
            if d:
                cursor.execute("SELECT (SELECT COUNT(*) FROM appointment_queue WHERE CAST(completed_at AS DATE)=? AND queue_status='completed'),(SELECT COUNT(*) FROM appointments WHERE appointment_date=? AND status!='cancelled'),(SELECT COUNT(*) FROM patient_consultations WHERE CAST(consultation_date AS DATE)=?),(SELECT COUNT(*) FROM appointment_queue WHERE CAST(completed_at AS DATE)=? AND queue_status='no_show'),(SELECT AVG(CAST(DATEDIFF(MINUTE,joined_queue_at,called_at) AS FLOAT)) FROM appointment_queue WHERE called_at IS NOT NULL AND CAST(joined_queue_at AS DATE)=?),(SELECT COUNT(*) FROM appointment_queue WHERE CAST(joined_queue_at AS DATE)=? AND severity_level>=8)", (d,d,d,d,d,d))
            else:
                cursor.execute("SELECT (SELECT COUNT(*) FROM appointment_queue WHERE CAST(completed_at AS DATE)=CAST(GETDATE() AS DATE) AND queue_status='completed'),(SELECT COUNT(*) FROM appointments WHERE appointment_date=CAST(GETDATE() AS DATE) AND status!='cancelled'),(SELECT COUNT(*) FROM patient_consultations WHERE CAST(consultation_date AS DATE)=CAST(GETDATE() AS DATE)),(SELECT COUNT(*) FROM appointment_queue WHERE CAST(completed_at AS DATE)=CAST(GETDATE() AS DATE) AND queue_status='no_show'),(SELECT AVG(CAST(DATEDIFF(MINUTE,joined_queue_at,called_at) AS FLOAT)) FROM appointment_queue WHERE called_at IS NOT NULL AND CAST(joined_queue_at AS DATE)=CAST(GETDATE() AS DATE)),(SELECT COUNT(*) FROM appointment_queue WHERE CAST(joined_queue_at AS DATE)=CAST(GETDATE() AS DATE) AND severity_level>=8)")
            r=cursor.fetchone(); cursor.close()
        return {"date":date or "today","total_patients_seen":r[0] or 0,"total_appointments":r[1] or 0,"total_consultations":r[2] or 0,"total_no_shows":r[3] or 0,"avg_wait_minutes":round(float(r[4] or 0),1),"critical_cases":r[5] or 0}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/reports/ai-accuracy")
async def ai_accuracy(user:CurrentUser=Depends(require_manager)):
    try:
        with DatabaseManager.get_connection() as conn:
            cursor=conn.cursor()
            cursor.execute("SELECT COUNT(*),SUM(CASE WHEN actual_diagnosis IS NOT NULL THEN 1 ELSE 0 END),SUM(CASE WHEN was_ai_correct=1 THEN 1 ELSE 0 END) FROM patient_consultations")
            r=cursor.fetchone(); cursor.close()
        total,verified,correct=r[0] or 0,r[1] or 0,r[2] or 0
        return {"total_consultations":total,"verified_count":verified,"correct_count":correct,"accuracy_percent":round((correct/verified*100) if verified>0 else 0,1)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reports/patient-flow")
async def patient_flow_today(user: CurrentUser = Depends(require_manager)):
    """Hourly patient flow for today (for line chart)."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DATEPART(HOUR, joined_queue_at) AS hr, COUNT(*) AS cnt
                FROM appointment_queue
                WHERE CAST(joined_queue_at AS DATE) = CAST(GETDATE() AS DATE)
                GROUP BY DATEPART(HOUR, joined_queue_at)
                ORDER BY hr
            """)
            rows = cursor.fetchall()
            cursor.close()
        # Fill all hours 8-22
        data = {r[0]: r[1] for r in rows}
        flow = [{"hour": f"{h}:00", "patients": data.get(h, 0)} for h in range(8, 23)]
        return {"flow": flow}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reports/today-revenue")
async def today_revenue(user: CurrentUser = Depends(require_manager)):
    """Today's revenue = SUM of consultation fees for completed queue entries today."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ISNULL(SUM(d.consultation_fee_egp), 0), COUNT(*)
                FROM appointment_queue q
                JOIN doctors d ON q.doctor_id = d.doctor_id
                WHERE CAST(q.completed_at AS DATE) = CAST(GETDATE() AS DATE)
                  AND q.queue_status = 'completed'
            """)
            row = cursor.fetchone()
            cursor.close()
        return {
            "total_revenue": float(row[0]) if row[0] else 0,
            "total_completed": row[1] or 0,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reports/common-diseases")
async def common_diseases_today(user: CurrentUser = Depends(require_manager)):
    """Top diseases/diagnoses today (for bar chart)."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT TOP 8 ai_preliminary_diagnosis, COUNT(*) AS cnt
                FROM patient_consultations
                WHERE CAST(consultation_date AS DATE) = CAST(GETDATE() AS DATE)
                  AND ai_preliminary_diagnosis IS NOT NULL
                  AND ai_preliminary_diagnosis != ''
                GROUP BY ai_preliminary_diagnosis
                ORDER BY cnt DESC
            """)
            rows = cursor.fetchall()
            cursor.close()
        diseases = [{"name": r[0], "count": r[1]} for r in rows]
        return {"diseases": diseases}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/logs")
async def system_logs(page:int=1, size:int=50, level:str=None, user:CurrentUser=Depends(require_manager)):
    try:
        offset=(page-1)*size
        with DatabaseManager.get_connection() as conn:
            cursor=conn.cursor()
            where,params="",[]
            if level: where="WHERE log_level=?"; params=[level]
            cursor.execute(f"SELECT COUNT(*) FROM system_logs {where}", tuple(params))
            total=cursor.fetchone()[0]
            cursor.execute(f"SELECT log_id,timestamp,log_level,component,action,message,user_id,execution_time_ms FROM system_logs {where} ORDER BY timestamp DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY", tuple(params+[offset,size]))
            logs=[{"log_id":r[0],"timestamp":safe_isoformat(r[1]),"log_level":r[2],"component":r[3],"action":r[4],"message":r[5],"user_id":r[6],"execution_time_ms":r[7]} for r in cursor.fetchall()]
            cursor.close()
        return {"total":total,"page":page,"page_size":size,"items":logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ════════════════════════════════════════════════════════════
#  API AUDIT LOGS - Full Request/Response Monitoring
# ════════════════════════════════════════════════════════════

@router.get("/api-logs")
async def get_api_logs(
    page: int = 1,
    size: int = 50,
    method: str = None,
    endpoint: str = None,
    status_min: int = None,
    status_max: int = None,
    user_id: int = None,
    client_ip: str = None,
    device_type: str = None,
    device_id: str = None,
    date_from: str = None,
    date_to: str = None,
    user: CurrentUser = Depends(require_manager)
):
    """Get API audit logs with filters."""
    try:
        offset = (page - 1) * size
        conditions, params = [], []

        if method:
            conditions.append("method = ?"); params.append(method.upper())
        if endpoint:
            conditions.append("endpoint LIKE ?"); params.append(f"%{endpoint}%")
        if status_min:
            conditions.append("status_code >= ?"); params.append(status_min)
        if status_max:
            conditions.append("status_code <= ?"); params.append(status_max)
        if user_id:
            conditions.append("user_id = ?"); params.append(user_id)
        if client_ip:
            conditions.append("client_ip LIKE ?"); params.append(f"%{client_ip}%")
        if device_type:
            conditions.append("device_type = ?"); params.append(device_type)
        if device_id:
            conditions.append("device_id = ?"); params.append(device_id)
        if date_from:
            conditions.append("CAST(timestamp AS DATE) >= ?"); params.append(date_from)
        if date_to:
            conditions.append("CAST(timestamp AS DATE) <= ?"); params.append(date_to)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(f"SELECT COUNT(*) FROM api_logs {where}", tuple(params))
            total = cursor.fetchone()[0]

            cursor.execute(f"""
                SELECT log_id, timestamp, method, endpoint, query_params,
                       status_code, duration_ms, client_ip, user_agent,
                       device_type, browser, os, user_id, user_role, user_name,
                       request_body, request_content_type,
                       response_body, response_size_bytes, error_detail, request_id, label,
                       device_id, device_name
                FROM api_logs {where}
                ORDER BY timestamp DESC
                OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
            """, tuple(params + [offset, size]))

            logs = []
            for r in cursor.fetchall():
                logs.append({
                    "log_id": r[0],
                    "timestamp": safe_isoformat(r[1]),
                    "method": r[2],
                    "endpoint": r[3],
                    "query_params": r[4],
                    "status_code": r[5],
                    "duration_ms": r[6],
                    "client_ip": r[7],
                    "user_agent": r[8],
                    "device_type": r[9],
                    "browser": r[10],
                    "os": r[11],
                    "user_id": r[12],
                    "user_role": r[13],
                    "user_name": r[14],
                    "request_body": r[15],
                    "request_content_type": r[16],
                    "response_body": r[17],
                    "response_size_bytes": r[18],
                    "error_detail": r[19],
                    "request_id": r[20],
                    "label": r[21],
                    "device_id": r[22],
                    "device_name": r[23],
                })
            cursor.close()

        return {"total": total, "page": page, "page_size": size, "items": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api-logs/stats")
async def api_logs_stats(user: CurrentUser = Depends(require_manager)):
    """Get API usage statistics."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    (SELECT COUNT(*) FROM api_logs WHERE CAST(timestamp AS DATE) = CAST(GETDATE() AS DATE)),
                    (SELECT COUNT(*) FROM api_logs WHERE CAST(timestamp AS DATE) = CAST(GETDATE() AS DATE) AND status_code >= 400),
                    (SELECT AVG(CAST(duration_ms AS FLOAT)) FROM api_logs WHERE CAST(timestamp AS DATE) = CAST(GETDATE() AS DATE)),
                    (SELECT COUNT(DISTINCT client_ip) FROM api_logs WHERE CAST(timestamp AS DATE) = CAST(GETDATE() AS DATE)),
                    (SELECT COUNT(*) FROM api_logs),
                    (SELECT TOP 1 endpoint FROM api_logs WHERE CAST(timestamp AS DATE) = CAST(GETDATE() AS DATE) GROUP BY endpoint ORDER BY COUNT(*) DESC),
                    (SELECT COUNT(*) FROM api_logs WHERE CAST(timestamp AS DATE) = CAST(GETDATE() AS DATE) AND status_code = 401)
            """)
            r = cursor.fetchone()

            # Top endpoints today
            cursor.execute("""
                SELECT TOP 10 method, endpoint, COUNT(*) as cnt,
                       AVG(CAST(duration_ms AS FLOAT)) as avg_ms,
                       SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as errors
                FROM api_logs
                WHERE CAST(timestamp AS DATE) = CAST(GETDATE() AS DATE)
                GROUP BY method, endpoint
                ORDER BY cnt DESC
            """)
            top_endpoints = [{"method": row[0], "endpoint": row[1], "count": row[2],
                              "avg_duration_ms": round(float(row[3] or 0), 1), "errors": row[4]}
                             for row in cursor.fetchall()]

            # Device breakdown today
            cursor.execute("""
                SELECT device_type, COUNT(*) as cnt
                FROM api_logs
                WHERE CAST(timestamp AS DATE) = CAST(GETDATE() AS DATE)
                GROUP BY device_type
            """)
            devices = {row[0]: row[1] for row in cursor.fetchall()}

            # Hourly distribution today
            cursor.execute("""
                SELECT DATEPART(HOUR, timestamp) as hr, COUNT(*) as cnt
                FROM api_logs
                WHERE CAST(timestamp AS DATE) = CAST(GETDATE() AS DATE)
                GROUP BY DATEPART(HOUR, timestamp)
                ORDER BY hr
            """)
            hourly = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.close()

        return {
            "today": {
                "total_requests": r[0] or 0,
                "errors": r[1] or 0,
                "avg_response_ms": round(float(r[2] or 0), 1),
                "unique_ips": r[3] or 0,
                "most_used_endpoint": r[5],
                "unauthorized_attempts": r[6] or 0,
            },
            "all_time_total": r[4] or 0,
            "top_endpoints": top_endpoints,
            "devices": devices,
            "hourly": hourly,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api-logs/{log_id}")
async def get_api_log_detail(log_id: int, user: CurrentUser = Depends(require_manager)):
    """Get a single log entry with full request/response bodies."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT log_id, timestamp, method, endpoint, query_params,
                       status_code, duration_ms, client_ip, user_agent,
                       device_type, browser, os, user_id, user_role, user_name,
                       request_body, request_content_type,
                       response_body, response_size_bytes, error_detail, request_id, label,
                       device_id, device_name
                FROM api_logs WHERE log_id = ?
            """, (log_id,))
            r = cursor.fetchone()
            cursor.close()
        if not r:
            raise HTTPException(status_code=404, detail="Log not found")
        keys = ["log_id","timestamp","method","endpoint","query_params",
                "status_code","duration_ms","client_ip","user_agent",
                "device_type","browser","os","user_id","user_role","user_name",
                "request_body","request_content_type",
                "response_body","response_size_bytes","error_detail","request_id","label",
                "device_id","device_name"]
        result = {}
        for i, k in enumerate(keys):
            v = r[i]
            result[k] = safe_isoformat(v) if hasattr(v, 'isoformat') else v
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api-logs/name-device")
async def name_device(device_id: str, name: str, user: CurrentUser = Depends(require_manager)):
    """Set a custom name for a device_id. Updates ALL logs from that device."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE api_logs SET device_name = ? WHERE device_id = ?", (name, device_id))
            updated = cursor.rowcount
            cursor.close()
        if updated == 0:
            raise HTTPException(status_code=404, detail="Device ID not found")
        return {"success": True, "device_id": device_id, "device_name": name, "logs_updated": updated}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api-logs/devices")
async def list_devices(user: CurrentUser = Depends(require_manager)):
    """Get all unique devices with their names and request counts."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT device_id, 
                       MAX(device_name) as device_name,
                       MAX(device_type) as device_type,
                       MAX(browser) as browser,
                       MAX(os) as os,
                       MAX(client_ip) as last_ip,
                       COUNT(*) as request_count,
                       MAX(timestamp) as last_seen
                FROM api_logs
                WHERE device_id IS NOT NULL
                GROUP BY device_id
                ORDER BY MAX(timestamp) DESC
            """)
            devices = []
            for r in cursor.fetchall():
                devices.append({
                    "device_id": r[0], "device_name": r[1],
                    "device_type": r[2], "browser": r[3], "os": r[4],
                    "last_ip": r[5], "request_count": r[6],
                    "last_seen": safe_isoformat(r[7]),
                })
            cursor.close()
        return {"devices": devices, "total": len(devices)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
