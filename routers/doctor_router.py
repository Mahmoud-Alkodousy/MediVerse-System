"""Doctor Dashboard Router - /doctor/* endpoints (JWT protected)"""

from fastapi import APIRouter, HTTPException, Depends
from models.auth import CurrentUser
from models.doctor import DoctorStatusUpdate, DoctorProfileUpdate, DoctorNoteCreate
from services.auth_service import require_doctor
from services import doctor_service as svc

router = APIRouter(prefix="/doctor", tags=["Doctor Dashboard"])

@router.get("/profile")
async def get_profile(user: CurrentUser = Depends(require_doctor)):
    result = svc.get_doctor_profile(user.user_id)
    if not result:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return result

@router.put("/profile")
async def update_profile(req: DoctorProfileUpdate, user: CurrentUser = Depends(require_doctor)):
    return svc.update_doctor_profile(user.user_id, req.model_dump(exclude_none=True))

@router.put("/status")
async def update_status(req: DoctorStatusUpdate, user: CurrentUser = Depends(require_doctor)):
    return svc.update_doctor_status(user.user_id, req.status)

@router.get("/queue")
async def get_queue(user: CurrentUser = Depends(require_doctor)):
    return svc.get_doctor_queue(user.user_id)

@router.post("/queue/next")
async def call_next(user: CurrentUser = Depends(require_doctor)):
    return svc.call_next_patient(user.user_id)

@router.post("/queue/{queue_id}/complete")
async def complete(queue_id: int, user: CurrentUser = Depends(require_doctor)):
    return svc.complete_patient(user.user_id, queue_id)

@router.post("/queue/{queue_id}/no-show")
async def no_show(queue_id: int, user: CurrentUser = Depends(require_doctor)):
    return svc.mark_no_show(user.user_id, queue_id)

@router.get("/patient/{patient_id}")
async def view_patient(patient_id: int, user: CurrentUser = Depends(require_doctor)):
    result = svc.get_patient_full_info(patient_id)
    if not result:
        raise HTTPException(status_code=404, detail="Patient not found")
    # Include medical files (reports, X-rays, lab tests)
    from services.medical_files_service import get_patient_medical_files
    try:
        files_data = get_patient_medical_files(patient_id)
        result["medical_files"] = files_data.get("files", [])
        result["medical_files_by_type"] = files_data.get("by_type", {})
        result["total_medical_files"] = files_data.get("total_files", 0)
    except Exception:
        result["medical_files"] = []
        result["medical_files_by_type"] = {}
        result["total_medical_files"] = 0
    return result

@router.post("/notes")
async def add_note(req: DoctorNoteCreate, user: CurrentUser = Depends(require_doctor)):
    return svc.add_doctor_note(user.user_id, req.model_dump())

@router.get("/stats")
async def get_stats(user: CurrentUser = Depends(require_doctor)):
    return svc.get_doctor_stats(user.user_id)


@router.get("/my-uploads")
async def get_my_uploads(user: CurrentUser = Depends(require_doctor)):
    """Get all medical files uploaded by this doctor (across all patients)."""
    from database.connection import DatabaseManager
    from utils.helpers import safe_isoformat
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT f.file_id, f.patient_id, p.full_name, f.file_type, f.file_name,
                       f.file_path, f.title, f.ai_analysis, f.ai_model_used,
                       f.test_date, f.upload_date, f.file_size_kb
                FROM medical_files f
                JOIN Patients p ON f.patient_id = p.id
                WHERE f.uploaded_by_doctor = ? AND f.is_active = 1
                ORDER BY f.upload_date DESC
            """, (user.user_id,))
            files = []
            for r in cursor.fetchall():
                files.append({
                    "file_id": r[0],
                    "patient_id": r[1],
                    "patient_name": r[2],
                    "file_type": r[3],
                    "file_name": r[4],
                    "file_url": f"/medical-files/{r[5]}",
                    "title": r[6],
                    "ai_analysis": r[7],
                    "ai_model_used": r[8],
                    "test_date": safe_isoformat(r[9]),
                    "upload_date": safe_isoformat(r[10]),
                    "file_size_kb": r[11],
                })
            cursor.close()
        return {"doctor_id": user.user_id, "total_uploads": len(files), "files": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
