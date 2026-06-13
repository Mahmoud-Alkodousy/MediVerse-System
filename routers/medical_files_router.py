"""
MediVerse - Medical Files Router
Upload, analyze, and retrieve patient medical files (X-rays, lab tests, MRI, etc.)

Flow:
  1. Doctor uploads image → POST /medical-files/analyze → AI analyzes → returns result in modal
  2. Doctor clicks Save  → POST /medical-files/upload  → saves file + AI analysis to DB
  3. Next visit          → GET  /medical-files/patient/{id} → shows stored files

Endpoints:
  POST   /medical-files/analyze              - Upload image → AI analysis (no save)
  POST   /medical-files/upload               - Save file + metadata to DB
  GET    /medical-files/patient/{patient_id}  - Get all patient files
  GET    /medical-files/{file_id}            - Get single file metadata
  DELETE /medical-files/{file_id}            - Delete file (doctor JWT)
"""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException

from services.auth_service import get_current_user, require_doctor
from services.medical_files_service import (
    upload_medical_file,
    get_patient_medical_files,
    get_medical_file,
    delete_medical_file,
)
from services.medical_analysis_service import analyze_medical_image
from models.auth import CurrentUser

router = APIRouter(prefix="/medical-files", tags=["Medical Files"])


@router.post("/analyze")
async def analyze_image(
    file: UploadFile = File(...),
    file_type: str = Form("xray", description="xray, lab_test, mri, ct_scan, ultrasound, other"),
    patient_context: Optional[str] = Form(None, description="Patient age, symptoms, etc."),
):
    """
    Upload a medical image for AI analysis WITHOUT saving.
    Returns the AI analysis result to display in a modal.
    Doctor can then decide to save via POST /medical-files/upload.
    
    Response:
    {
        "success": true,
        "report_type": "Chest X-ray",
        "analysis": "No significant abnormalities...",
        "model_used": "Gemini 2.0 Flash (Medical Vision)",
        "model_id": "google/gemini-2.0-flash-001",
        "analyzed_at": "2026-02-07T14:30:00",
        "file_name": "xray_image.jpg",
        "file_size_kb": 245
    }
    """
    # Read file
    image_bytes = await file.read()
    file_size_kb = len(image_bytes) // 1024

    if file_size_kb > 50 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 50MB")

    mime_type = file.content_type or "image/jpeg"
    
    # Send to AI
    result = analyze_medical_image(
        image_bytes=image_bytes,
        file_type=file_type,
        mime_type=mime_type,
        additional_context=patient_context,
    )

    # Extract AI-suggested title from analysis text
    import re
    ai_title = None
    analysis_text = result.get("analysis", "")
    # Look for "**Suggested Title**: ..." pattern
    title_match = re.search(r'\*\*Suggested Title\*\*[:\s]*(.+?)(?:\n|$)', analysis_text)
    if title_match:
        ai_title = title_match.group(1).strip().strip('*').strip()
    if not ai_title:
        ai_title = result.get('report_type', file_type)

    return {
        **result,
        "suggested_title": f"{ai_title} — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "analyzed_at": datetime.now().isoformat(),
        "file_name": file.filename,
        "file_size_kb": file_size_kb,
    }


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    patient_id: int = Form(...),
    file_type: str = Form(..., description="xray, lab_test, mri, ct_scan, ultrasound, prescription, report, other"),
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    doctor_id: Optional[int] = Form(None),
    consultation_id: Optional[int] = Form(None),
    ai_analysis: Optional[str] = Form(None),
    ai_model_used: Optional[str] = Form(None),
    test_date: Optional[str] = Form(None),
):
    """
    Save a medical file + metadata to database.
    Called after doctor reviews AI analysis and clicks Save.
    """
    return upload_medical_file(
        patient_id=patient_id,
        file=file,
        file_type=file_type,
        title=title,
        description=description,
        doctor_id=doctor_id,
        consultation_id=consultation_id,
        ai_analysis=ai_analysis,
        ai_model_used=ai_model_used,
        test_date=test_date,
    )


@router.get("/patient/{patient_id}")
async def get_patient_files(patient_id: int, file_type: Optional[str] = None):
    """Get all medical files for a patient. Optionally filter by type."""
    return get_patient_medical_files(patient_id, file_type)


@router.get("/{file_id}")
async def get_file(file_id: int):
    """Get metadata for a single medical file."""
    result = get_medical_file(file_id)
    if not result:
        raise HTTPException(status_code=404, detail="File not found")
    return result


@router.delete("/{file_id}")
async def delete_file(file_id: int, user: CurrentUser = Depends(require_doctor)):
    """Delete a medical file (soft delete). Requires doctor JWT."""
    return delete_medical_file(file_id, doctor_id=user.user_id)


# ══════════════════════════════════════════════════════════════
# PRESCRIPTION ANALYSIS (Single API: OCR → Correction → Info → Interactions)
# ══════════════════════════════════════════════════════════════

# Prescription analysis moved to /pharmacy/prescription/analyze
