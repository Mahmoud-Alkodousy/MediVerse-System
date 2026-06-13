"""
MediVerse - Utility Helpers
"""

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger("mediverse")


def sanitize_value(val: Any) -> Any:
    """
    Convert numpy types and non-JSON-serializable objects into plain Python types.
    """
    try:
        import numpy as _np
    except ImportError:
        _np = None

    if val is None or isinstance(val, (str, bool, int, float)):
        return val

    if _np is not None and isinstance(val, _np.generic):
        try:
            return val.item()
        except Exception:
            try:
                return float(val)
            except Exception:
                return str(val)

    if _np is not None and isinstance(val, _np.ndarray):
        try:
            return sanitize_value(val.tolist())
        except Exception:
            return [sanitize_value(x) for x in val]

    if isinstance(val, dict):
        return {str(k): sanitize_value(v) for k, v in val.items()}

    if isinstance(val, (list, tuple, set)):
        return [sanitize_value(x) for x in val]

    try:
        return float(val)
    except Exception:
        try:
            return int(val)
        except Exception:
            return str(val)


def calculate_age(date_of_birth) -> int | None:
    """Calculate age from date_of_birth."""
    if date_of_birth is None:
        return None
    try:
        if isinstance(date_of_birth, str):
            dob = datetime.fromisoformat(date_of_birth).date()
        elif hasattr(date_of_birth, "date"):
            dob = date_of_birth.date()
        else:
            dob = date_of_birth
        today = datetime.utcnow().date()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    except Exception:
        return None


def calculate_bmi(weight: float, height: float) -> float | None:
    """Calculate BMI. Height in cm, weight in kg."""
    if not weight or not height or height <= 0:
        return None
    height_m = height / 100.0
    return round(weight / (height_m ** 2), 1)


def days_between(date1, date2=None) -> int | None:
    """Days between two dates. If date2 is None, uses today."""
    if date1 is None:
        return None
    try:
        if isinstance(date1, str):
            d1 = datetime.fromisoformat(date1)
        else:
            d1 = date1
        d2 = date2 or datetime.utcnow()
        return abs((d2 - d1).days)
    except Exception:
        return None


def safe_str(val: Any) -> str | None:
    """Convert to string safely, return None for None."""
    if val is None:
        return None
    return str(val)


def safe_isoformat(val) -> str | None:
    """Convert datetime to ISO format string safely."""
    if val is None:
        return None
    try:
        return val.isoformat()
    except Exception:
        return str(val)
