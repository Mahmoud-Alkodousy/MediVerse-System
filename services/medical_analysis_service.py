"""
MediVerse - Medical Image Analysis Service
Sends X-rays, lab tests, etc. to Vision AI models via OpenRouter.
Returns structured analysis results.
"""

import os
import base64
import logging
import requests
from typing import Optional, Dict
from pathlib import Path

logger = logging.getLogger("mediverse")

# ── Config ────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")

# Best vision models for medical image analysis (ordered by preference)
VISION_MODELS = {
    "xray": {
        "model_id": "qwen/qwen2.5-vl-72b-instruct",
        "display_name": "Qwen2.5-VL-72B (Medical Vision)",
        "fallback": "google/gemini-2.0-flash-001",
    },
    "lab_test": {
        "model_id": "qwen/qwen2.5-vl-72b-instruct",
        "display_name": "Qwen2.5-VL-72B (Lab OCR)",
        "fallback": "google/gemini-2.0-flash-001",
    },
    "mri": {
        "model_id": "qwen/qwen2.5-vl-72b-instruct",
        "display_name": "Qwen2.5-VL-72B (MRI Analysis)",
        "fallback": "google/gemini-2.0-flash-001",
    },
    "ct_scan": {
        "model_id": "qwen/qwen2.5-vl-72b-instruct",
        "display_name": "Qwen2.5-VL-72B (CT Analysis)",
        "fallback": "google/gemini-2.0-flash-001",
    },
    "default": {
        "model_id": "qwen/qwen2.5-vl-72b-instruct",
        "display_name": "Qwen2.5-VL-72B",
        "fallback": "google/gemini-2.0-flash-001",
    },
}

# ── Prompts ───────────────────────────────────────────────────

XRAY_PROMPT = """You are an expert radiologist AI assistant. Analyze this X-ray image and provide:

1. **Suggested Title**: A short descriptive title for this file (e.g. "Right Shoulder Fracture X-ray", "Chest X-ray Normal", "Lumbar Spine Degenerative Changes"). Max 6 words, be specific to what you see.
2. **Report Type**: What type of X-ray is this? (Chest, Hand, Spine, Knee, etc.)
3. **Findings**: Describe what you see in detail
4. **Abnormalities**: List any abnormalities detected (or "No significant abnormalities detected")
5. **Bone Density**: Assessment of bone density
6. **Recommendation**: Clinical recommendation

⚠️ IMPORTANT: This is for EDUCATIONAL purposes only. Always recommend professional medical consultation.

Respond in English. Be concise but thorough."""

LAB_TEST_PROMPT = """You are an expert laboratory medicine AI assistant. Analyze this lab test result image and provide:

1. **Suggested Title**: A short descriptive title for this file (e.g. "Kidney Function Panel", "Complete Blood Count CBC", "Hepatitis B PCR Test", "Lipid Profile Panel"). Max 6 words, be specific to the actual tests shown.
2. **Test Type**: What type of lab test is this? (CBC, Blood Chemistry, Urinalysis, etc.)
3. **Results Summary**: Extract and list all test values with their reference ranges
4. **Abnormal Values**: Highlight any values outside normal range with ⚠️
5. **Overall Assessment**: Brief clinical interpretation
6. **Recommendation**: Any follow-up tests recommended

⚠️ IMPORTANT: This is for EDUCATIONAL purposes only. Always recommend professional medical consultation.

Respond in English. Be concise and structured."""

MRI_CT_PROMPT = """You are an expert radiology AI assistant. Analyze this medical image and provide:

1. **Suggested Title**: A short descriptive title for this file (e.g. "Brain MRI Normal", "Abdominal CT with Contrast", "Knee MRI Ligament Tear"). Max 6 words, be specific.
2. **Scan Type**: What type of scan is this?
3. **Region**: Body region being examined
4. **Findings**: Detailed findings
5. **Abnormalities**: Any abnormalities detected
6. **Recommendation**: Clinical recommendation

⚠️ IMPORTANT: This is for EDUCATIONAL purposes only. Always recommend professional medical consultation.

Respond in English. Be concise but thorough."""

GENERIC_PROMPT = """You are a medical AI assistant. Analyze this medical image/document and provide:

1. **Suggested Title**: A short descriptive title for this file. Max 6 words, be specific to the content.
2. **Document Type**: What type of medical document/image is this?
3. **Key Findings**: Main observations
4. **Analysis**: Detailed analysis
5. **Recommendation**: Any recommendations

⚠️ IMPORTANT: This is for EDUCATIONAL purposes only.

Respond in English."""


def _get_prompt(file_type: str) -> str:
    """Get the appropriate analysis prompt based on file type."""
    prompts = {
        "xray": XRAY_PROMPT,
        "lab_test": LAB_TEST_PROMPT,
        "mri": MRI_CT_PROMPT,
        "ct_scan": MRI_CT_PROMPT,
    }
    return prompts.get(file_type, GENERIC_PROMPT)


def _get_model(file_type: str) -> Dict:
    """Get the best vision model for this file type."""
    return VISION_MODELS.get(file_type, VISION_MODELS["default"])


def _encode_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    """Encode image bytes to base64 data URL."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


def analyze_medical_image(
    image_bytes: bytes,
    file_type: str = "xray",
    mime_type: str = "image/jpeg",
    additional_context: Optional[str] = None,
) -> Dict:
    """
    Send a medical image to Vision AI for analysis.
    
    Args:
        image_bytes: Raw image bytes
        file_type: xray, lab_test, mri, ct_scan, etc.
        mime_type: image/jpeg, image/png, etc.
        additional_context: Extra info like patient age, symptoms
    
    Returns:
        {
            "success": True/False,
            "analysis": "AI analysis text",
            "model_used": "display name of model",
            "model_id": "openrouter model id",
            "report_type": "detected type",
            "error": "error message if failed"
        }
    """
    if not OPENROUTER_API_KEY:
        return {
            "success": False,
            "analysis": "OpenRouter API key not configured. Cannot analyze image.",
            "model_used": "N/A",
            "model_id": "N/A",
            "error": "OPENROUTER_API_KEY not set"
        }

    model_info = _get_model(file_type)
    model_id = model_info["model_id"]
    display_name = model_info["display_name"]
    prompt = _get_prompt(file_type)

    if additional_context:
        prompt += f"\n\nAdditional patient context: {additional_context}"

    # Encode image
    image_data_url = _encode_image(image_bytes, mime_type)

    # Build request with vision
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_url}
                }
            ]
        }
    ]

    try:
        response = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_id,
                "messages": messages,
                "max_tokens": 1500,
                "temperature": 0.3,
            },
            timeout=60,
        )

        if response.status_code == 200:
            data = response.json()
            analysis_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            if not analysis_text:
                analysis_text = "Analysis completed but no text was returned."

            # Try to detect report type from analysis
            report_type = file_type
            analysis_lower = analysis_text.lower()
            if "chest" in analysis_lower:
                report_type = "Chest X-ray"
            elif "hand" in analysis_lower or "wrist" in analysis_lower:
                report_type = "Hand/Wrist X-ray"
            elif "spine" in analysis_lower:
                report_type = "Spine X-ray"
            elif "knee" in analysis_lower:
                report_type = "Knee X-ray"
            elif "cbc" in analysis_lower or "complete blood" in analysis_lower:
                report_type = "Complete Blood Count (CBC)"
            elif "blood" in analysis_lower and "chemistry" in analysis_lower:
                report_type = "Blood Chemistry"
            elif "urinalysis" in analysis_lower:
                report_type = "Urinalysis"
            elif file_type == "xray":
                report_type = "X-ray"
            elif file_type == "lab_test":
                report_type = "Lab Test"

            logger.info(f"Medical image analyzed: {file_type} with {model_id}")
            return {
                "success": True,
                "analysis": analysis_text,
                "model_used": display_name,
                "model_id": model_id,
                "report_type": report_type,
            }
        else:
            # Try fallback model
            fallback_id = model_info.get("fallback")
            if fallback_id and fallback_id != model_id:
                logger.warning(f"Primary model failed ({response.status_code}), trying fallback {fallback_id}")
                response2 = requests.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": fallback_id,
                        "messages": messages,
                        "max_tokens": 1500,
                        "temperature": 0.3,
                    },
                    timeout=60,
                )
                if response2.status_code == 200:
                    data = response2.json()
                    analysis_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    return {
                        "success": True,
                        "analysis": analysis_text or "Analysis completed.",
                        "model_used": f"{fallback_id} (fallback)",
                        "model_id": fallback_id,
                        "report_type": file_type,
                    }

            error_msg = f"API error {response.status_code}: {response.text[:200]}"
            logger.error(f"Medical analysis failed: {error_msg}")
            return {
                "success": False,
                "analysis": "",
                "model_used": display_name,
                "model_id": model_id,
                "error": error_msg,
            }

    except requests.exceptions.Timeout:
        return {
            "success": False,
            "analysis": "",
            "model_used": display_name,
            "model_id": model_id,
            "error": "Analysis timed out. Please try again.",
        }
    except Exception as e:
        logger.error(f"Medical analysis error: {e}")
        return {
            "success": False,
            "analysis": "",
            "model_used": display_name,
            "model_id": model_id,
            "error": str(e),
        }
