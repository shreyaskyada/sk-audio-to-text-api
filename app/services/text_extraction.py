
import re
from typing import Optional

def extract_weight_from_text(text: str) -> Optional[str]:
    """
    Helper to extract weight (lbs) from text using various patterns.
    Handles: "Avoid lifting more than 2 lbs", "Lift max 10lbs", "No lifting > 5 lbs"
    """
    if not text:
        return None
        
    flags = re.IGNORECASE | re.DOTALL
    
    # 1. "Avoid/No ... more than/over/greater than X lbs"
    # Matches: "Avoid any lifting more than 2 pounds", "No lifting over 10 lbs"
    match = re.search(r'(?:avoid|no|not).*?(?:lift|push|pull|carry).*?(?:more than|over|greater than|exceeding|>)\s*(\d+)\s*(?:lbs|pounds|lb)', text, flags)
    if match:
        return match.group(1)

    # 2. "Max/Maximum/Limit ... X lbs"
    # Matches: "Max lifting 15 lbs", "Lifting limit 20lbs", "limited to 5 lbs"
    match = re.search(r'(?:max|maximum|limit).*?(?:lift|push|pull|carry)?.*?(?:to|of|is)?\s*(\d+)\s*(?:lbs|pounds|lb)', text, flags)
    if match:
        return match.group(1)

    # 3. "Lifting ... <symbols> X lbs"
    # Matches: "Lifting < 10 lbs", "Lifting <= 5lbs"
    match = re.search(r'(?:lift|push|pull|carry).*?(?:<=|<|≤)\s*(\d+)\s*(?:lbs|pounds|lb)', text, flags)
    if match:
        return match.group(1)

    # 4. "No lifting X lbs" (Implies X is the limit or the object)
    # Matches: "No lifting 50 lbs" -> Usually means limit is lower, but often interpreted as limit. 
    # Better: "Lifting restriction: 10 lbs"
    match = re.search(r'(?:lift|push|pull|carry).*?restriction.*?\s*(\d+)\s*(?:lbs|pounds|lb)', text, flags)
    if match:
        return match.group(1)
        
    # Legacy patterns from PR1 generator
    
    # Pattern 1: Explicit "limited to" or symbols <= / < / ≤ / upto
    match = re.search(r'(?:lift|carry|push|pull).*?(?:limit.*?to|<=|<|≤|max|maximum|upto|up to)\s*(?:=|:)?\s*(?:<=|<|≤)?\s*(\d+)\s*(?:lbs|pounds|lb)', text, flags)
    if match:
        return match.group(1)
        
    # Pattern 2: "no lifting over 10 lbs" or "no lifting > 10 lbs"
    match = re.search(r'(?:no )?(?:lift|carry|push|pull).*?(?:over|>|more than|greater than)\s*(\d+)\s*(?:lbs|pounds|lb)', text, flags)
    if match:
        return match.group(1)
        
    # Pattern 3: "lifting restriction 10 lbs"
    match = re.search(r'(?:lift|carry|push|pull).*?restriction.*?\s*(\d+)\s*(?:lbs|pounds|lb)', text, flags)
    if match:
        return match.group(1)
        
    # Broad fallbacks
    match = re.search(r'(?:lift|carry|push|pull)\s+(?:of\s+)?(\d+)\s*(?:lbs|pounds|lb)', text, flags)
    if match:
        return match.group(1)

    match = re.search(r'(?:lift|carry|push|pull).{0,50}?\s(\d+)\s*(?:lbs|pounds|lb)', text, flags)
    if match:
        return match.group(1)
        
    return None

def extract_work_status_section(text: str) -> str:
    """Isolate work status section to avoid false positives"""
    if not text: return ""
    
    # 1. Look for the specific structured header from the image (PR1 style)
    match = re.search(r'Restrictions \(ONLY if Modified Duty\):\s*(.*?)(?:\n\n|\nEffective Date:|\nDuration:|\nSIGNATURE|$)', text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
        
    # 2. Look for standard headers
    match = re.search(r'(?:work status|restrictions|functional limitations)(.*?)(?=\n\n|\r\n\r\n|SIGNATURE|Provider Name:|Electronic Signature:|Subjective:|History:|$)', text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
        
    # 3. Simple fallback (Work Status Form style)
    match = re.search(r'(?:work status|restrictions|functional limitations)(.*)', text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
        
    return text

def extract_date_from_text(text: str) -> Optional[str]:
    """
    Extract a date (MM/DD/YYYY or YYYY-MM-DD) from text, looking for keywords like 'Effective Date'
    Handles cases where date is on the next line (re.DOTALL).
    """
    if not text: return None
    
    # Look for "Effective Date: MM/DD/YYYY" or similar
    match = re.search(r'(?:effective|start|date|apply from|from).*?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', text, re.IGNORECASE | re.DOTALL)
    if match:
        date_str = match.group(1)
        try:
            parts = re.split(r'[/-]', date_str)
            if len(parts) == 3:
                p0 = int(parts[0])
                p1 = int(parts[1])
                year = parts[2]
                
                # Assume MM/DD/YYYY unless p0 > 12
                if len(year) == 4:
                    if p0 > 12:
                         return f"{year}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
                    else:
                         return f"{year}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
        except:
            pass
            
    return None

def extract_body_parts_fallback(text: str) -> Optional[str]:
    """Fallback extraction for body parts from raw text"""
    if not text: return None
    
    flags = re.IGNORECASE | re.DOTALL
    
    match = re.search(r'(?:Diagnosis|Assessment|Body\s*Part|Injury\s*Location).*?:\s*([^\n\.]+)', text, flags)
    if match:
        val = match.group(1).replace("*", "").strip()
        if val and len(val) < 100: 
            return val
    
    match = re.search(r'(?:CC|Chief\s*Complaint).*?:\s*([^\n\.]+)', text, flags)
    if match:
        val = match.group(1).replace("*", "").strip()
        if val and len(val) < 100: 
            return val

    return None
