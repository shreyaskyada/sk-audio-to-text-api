
import re
from app.prompts import MEDICAL_TERMINOLOGY_CORRECTIONS

def fix_terms(text: str) -> str:
    """
    Fix common medical terminology errors in transcription using a single-pass regex replacement.
    This prevents double-replacements where a corrected term is further incorrectly modified.
    """
    if not text:
        return text
    
    # Sort keys by length (longest first) to ensure best match
    sorted_corrections = sorted(MEDICAL_TERMINOLOGY_CORRECTIONS.items(), key=lambda x: len(x[0]), reverse=True)
    
    # Escape keys and join with | for regex
    pattern_string = "|".join([r'\b' + re.escape(k) + r'\b' for k, _ in sorted_corrections])
    pattern = re.compile(pattern_string, flags=re.IGNORECASE)
    
    # Case-insensitive mapping for the replacement function
    # Note: We need a lower-case map to find the correct replacement while ignoring case
    mapping = {k.lower(): v for k, v in MEDICAL_TERMINOLOGY_CORRECTIONS.items()}
    
    def replace_func(match):
        match_text = match.group(0).lower()
        return mapping.get(match_text, match.group(0))
    
    return pattern.sub(replace_func, text)
