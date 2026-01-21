import re
import logging
from typing import Any, Optional
from app.prompts import MEDICAL_TERMINOLOGY_CORRECTIONS

logger = logging.getLogger(__name__)

def fix_terms(text: str) -> str:
    """Fix common medical terminology errors in transcription"""
    pattern = re.compile("|".join(re.escape(k) for k in MEDICAL_TERMINOLOGY_CORRECTIONS.keys()), re.IGNORECASE)
    
    def replace_func(match):
        return MEDICAL_TERMINOLOGY_CORRECTIONS[match.group(0).lower()]
    
    return pattern.sub(replace_func, text)

def format_clinical_data(val: Any) -> str:
    """Recursively format clinical data into clean text"""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "Yes" if val else "No"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        # Clean up common artifacts
        s = val.strip()
        if s.lower() in ["n/a", "none", "not applicable", "unknown"]:
            return ""
        return s
    if isinstance(val, list):
        items = [format_clinical_data(i) for i in val if i]
        return ", ".join(items)
    if isinstance(val, dict):
        items = []
        for k, v in val.items():
            formatted_v = format_clinical_data(v)
            if formatted_v:
                # Format key: Patient Name -> Patient Name
                key_text = k.replace("_", " ").title()
                items.append(f"{key_text}: {formatted_v}")
        return "; ".join(items)
    return str(val)

def str_or_nd(val: Optional[str]) -> str:
    """Return value or empty string if empty"""
    return val if (val is not None and str(val).strip() != "") else ""

def to_mmddyyyy(s: Optional[str]) -> str:
    """Convert date string to MM/DD/YYYY format"""
    if not s: return ""
    # Simple regex to find dates
    match = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if match:
        return f"{match.group(2).zfill(2)}/{match.group(3).zfill(2)}/{match.group(1)}"
    return s

def to_yyyy_mm_dd(s: Optional[str]) -> str:
    """Convert date string to YYYY-MM-DD format"""
    if not s: return ""
    match = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', s)
    if match:
        return f"{match.group(3)}-{match.group(1).zfill(2)}-{match.group(2).zfill(2)}"
    return s
