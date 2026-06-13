"""
MediVerse - Medical Files Service
Upload, store, and retrieve medical files (X-rays, lab tests, MRI, etc.)
Files stored on disk at: /medical_files/patient_{id}/
Metadata stored in: medical_files table
"""

import os
import logging
import shutil
import secrets
from pathlib import Path
from datetime import datetime, date
from typing import Optional, Dict, List

from fastapi import UploadFile, HTTPException
from database.connection import DatabaseManager
from config.settings import settings

logger = logging.getLogger("mediverse")

# Base directory for medical files storage
MEDICAL_FILES_DIR = os.getenv("MEDICAL_FILES_DIR", "medical_files")
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".pdf", ".dcm"}
MAX_FILE_SIZE_MB = 50


def _ensure_patient_dir(patient_id: int) -> Path:
    """Create patient directory if not exists."""
    patient_dir = Path(MEDICAL_FILES_DIR) / f"patient_{patient_id}"
    patient_dir.mkdir(parents=True, exist_ok=True)
    return patient_dir


def upload_medical_file(
    patient_id: int,
    file: UploadFile,
    file_type: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    doctor_id: Optional[int] = None,
    consultation_id: Optional[int] = None,
    ai_analysis: Optional[str] = None,
    ai_model_used: Optional[str] = None,
    test_date: Optional[str] = None,
) -> Dict:
    """
    Upload a medical file (X-ray, lab test, etc.) for a patient.
    Stores file on disk and metadata in database.
    """
    # Validate file type
    valid_types = {"xray", "lab_test", "mri", "ct_scan", "ultrasound", "prescription", "report", "other"}
    if file_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid file_type. Must be one of: {valid_types}")

    # Validate file extension
    original_name = file.filename or "unnamed"
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type not allowed. Allowed: {ALLOWED_EXTENSIONS}")

    # Read file and check size
    file_content = file.file.read()
    file_size_kb = len(file_content) // 1024
    if file_size_kb > MAX_FILE_SIZE_MB * 1024:
        raise HTTPException(status_code=400, detail=f"File too large. Max {MAX_FILE_SIZE_MB}MB")

    # Generate unique filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_id = secrets.token_hex(4)
    safe_name = f"{file_type}_{timestamp}_{unique_id}{ext}"

    # Save to disk
    patient_dir = _ensure_patient_dir(patient_id)
    file_path = patient_dir / safe_name
    with open(file_path, "wb") as f:
        f.write(file_content)

    # Store relative path in DB
    relative_path = f"medical_files/patient_{patient_id}/{safe_name}"

    # Parse test_date
    parsed_test_date = None
    if test_date:
        try:
            parsed_test_date = test_date
        except:
            pass

    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO medical_files 
                (patient_id, uploaded_by_doctor, consultation_id, file_type, file_name,
                 file_path, file_size_kb, mime_type, title, description,
                 ai_analysis, ai_model_used, test_date)
                OUTPUT INSERTED.file_id
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                patient_id, doctor_id, consultation_id, file_type, original_name,
                relative_path, file_size_kb, file.content_type, title, description,
                ai_analysis, ai_model_used, parsed_test_date
            ))
            row = cursor.fetchone()
            file_id = row[0] if row else 0
            cursor.close()

        logger.info(f"Medical file uploaded: {file_type} for patient {patient_id} -> {relative_path}")
        return {
            "success": True,
            "file_id": file_id,
            "file_path": relative_path,
            "file_name": original_name,
            "file_type": file_type,
            "file_size_kb": file_size_kb,
            "message": "File uploaded successfully"
        }
    except Exception as e:
        # Cleanup file on DB error
        if file_path.exists():
            file_path.unlink()
        logger.error(f"Failed to save medical file metadata: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def get_patient_medical_files(
    patient_id: int,
    file_type: Optional[str] = None
) -> Dict:
    """Get all medical files for a patient, optionally filtered by type."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            if file_type:
                cursor.execute("""
                    SELECT file_id, file_type, file_name, file_path, file_size_kb,
                           mime_type, title, description, ai_analysis, ai_model_used,
                           test_date, upload_date, uploaded_by_doctor
                    FROM medical_files
                    WHERE patient_id = ? AND file_type = ? AND is_active = 1
                    ORDER BY upload_date DESC
                """, (patient_id, file_type))
            else:
                cursor.execute("""
                    SELECT file_id, file_type, file_name, file_path, file_size_kb,
                           mime_type, title, description, ai_analysis, ai_model_used,
                           test_date, upload_date, uploaded_by_doctor
                    FROM medical_files
                    WHERE patient_id = ? AND is_active = 1
                    ORDER BY upload_date DESC
                """, (patient_id,))

            files = []
            for r in cursor.fetchall():
                # Get doctor name if uploaded by a doctor
                doctor_name = None
                if r[12]:
                    cursor.execute("SELECT doctor_name FROM doctors WHERE doctor_id = ?", (r[12],))
                    doc_row = cursor.fetchone()
                    doctor_name = doc_row[0] if doc_row else None

                # Build clean URL: DB stores '/medical_files/patient_X/file.jpg'
                # Static mount is at /medical-files -> medical_files/
                raw_path = r[3] or ''
                # Remove leading /medical_files/ to get patient_X/file.jpg
                clean_path = raw_path.lstrip('/').removeprefix('medical_files/').lstrip('/')
                files.append({
                    "file_id": r[0],
                    "file_type": r[1],
                    "file_name": r[2],
                    "file_url": f"/medical-files/{clean_path}",
                    "file_size_kb": r[4],
                    "mime_type": r[5],
                    "title": r[6],
                    "description": r[7],
                    "ai_analysis": r[8],
                    "ai_model_used": r[9],
                    "test_date": r[10].isoformat() if r[10] else None,
                    "upload_date": r[11].isoformat() if r[11] else None,
                    "uploaded_by": doctor_name,
                })
            cursor.close()

        # Group by type for easy display
        by_type = {}
        for f in files:
            ft = f["file_type"]
            if ft not in by_type:
                by_type[ft] = []
            by_type[ft].append(f)

        return {
            "patient_id": patient_id,
            "total_files": len(files),
            "files": files,
            "by_type": by_type,
        }
    except Exception as e:
        logger.error(f"Failed to get medical files for patient {patient_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def get_medical_file(file_id: int) -> Optional[Dict]:
    """Get a single medical file metadata."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT f.file_id, f.patient_id, f.file_type, f.file_name, f.file_path,
                       f.file_size_kb, f.mime_type, f.title, f.description,
                       f.ai_analysis, f.ai_model_used, f.test_date, f.upload_date,
                       p.full_name AS patient_name,
                       d.doctor_name AS uploaded_by
                FROM medical_files f
                JOIN Patients p ON f.patient_id = p.id
                LEFT JOIN doctors d ON f.uploaded_by_doctor = d.doctor_id
                WHERE f.file_id = ? AND f.is_active = 1
            """, (file_id,))
            r = cursor.fetchone()
            cursor.close()

        if not r:
            return None
        raw_p = (r[4] or '').lstrip('/').removeprefix('medical_files/').lstrip('/')
        return {
            "file_id": r[0], "patient_id": r[1], "file_type": r[2],
            "file_name": r[3], "file_url": f"/medical-files/{raw_p}",
            "file_size_kb": r[5], "mime_type": r[6], "title": r[7],
            "description": r[8], "ai_analysis": r[9], "ai_model_used": r[10],
            "test_date": r[11].isoformat() if r[11] else None,
            "upload_date": r[12].isoformat() if r[12] else None,
            "patient_name": r[13], "uploaded_by": r[14],
        }
    except Exception as e:
        logger.error(f"Failed to get medical file {file_id}: {e}")
        return None


def delete_medical_file(file_id: int, doctor_id: Optional[int] = None) -> Dict:
    """Soft-delete a medical file."""
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE medical_files SET is_active = 0 WHERE file_id = ?
            """, (file_id,))
            cursor.close()
        return {"success": True, "message": "File deleted"}
    except Exception as e:
        logger.error(f"Failed to delete medical file {file_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
