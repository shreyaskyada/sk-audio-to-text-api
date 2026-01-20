import logging
import re
from datetime import datetime
from typing import Optional, Any, Dict, List
from app.database import get_database
from bson import ObjectId
from pydantic import BaseModel

logger = logging.getLogger(__name__)

def str_or_nd(val: Optional[str]) -> str:
    return val if (val is not None and str(val).strip() != "") else ""

def to_mmddyyyy(s: Optional[str]) -> Optional[str]:
    if not s: return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%m/%d/%Y")
        except:
            continue
    return s

def to_yyyy_mm_dd(s: Optional[str]) -> Optional[str]:
    if not s: return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except:
            continue
    return s

def format_clinical_data(val: Any) -> str:
    if val is None: return ""
    if isinstance(val, str): return val.strip()
    if isinstance(val, (int, float, bool)): return str(val)
    if isinstance(val, list):
        parts = [format_clinical_data(item) for item in val if item]
        return ", ".join(parts) if all(len(p) < 40 for p in parts) else "\n".join(parts)
    if isinstance(val, dict):
        text_keys = ["text", "content", "value", "summary", "description", "note"]
        for key in text_keys:
            if key in val and val[key]: return format_clinical_data(val[key])
        return " | ".join([f"{k}: {format_clinical_data(v)}" for k, v in val.items() if v and k.lower() not in ("id", "_id", "type")])
    return str(val).strip()

def pick_name(intake_doc: Optional[Dict[str, Any]], soap_doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract patient name from intake or SOAP document"""
    a = (intake_doc or {}).get("section_a") or {}
    name = (a.get("first_name") or "") + " " + (a.get("last_name") or "")
    if name.strip(): return name.strip()
    return (soap_doc or {}).get("patient_name")

def pick_doi(intake_doc: Optional[Dict[str, Any]], soap_doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract date of injury from intake or SOAP document"""
    c = (intake_doc or {}).get("section_c") or {}
    doi = c.get("date_of_injury") or (soap_doc or {}).get("date_of_injury")
    return to_mmddyyyy(doi)

def primary_secondary_dx(soap_doc: Optional[Dict[str, Any]]) -> tuple:
    """Extract primary, secondary, and additional diagnoses from SOAP document"""
    dxs = []
    root_dxs = (soap_doc or {}).get("diagnoses") or []
    if root_dxs and isinstance(root_dxs, list): dxs.extend(root_dxs)
    
    clinical_info = (soap_doc or {}).get("clinical_information")
    if isinstance(clinical_info, dict):
        assessment = clinical_info.get("assessment")
        if isinstance(assessment, dict):
            assessment_dxs = assessment.get("diagnoses") or []
            if assessment_dxs: dxs.extend(assessment_dxs)
    
    diagnoses = []
    seen = set()
    for dx in dxs:
        if not isinstance(dx, dict): continue
        name = dx.get("condition") or dx.get("diagnosis") or ""
        code = dx.get("icd10") or dx.get("ICD-10") or dx.get("icd_10") or dx.get("ICD10") or dx.get("icd10_code") or ""
        key = f"{name}-{code}".lower().strip()
        if key and key not in seen:
            seen.add(key)
            diagnoses.append(dx)
            
    primary = diagnoses[0] if len(diagnoses) > 0 else None
    secondary = diagnoses[1] if len(diagnoses) > 1 else None
    additional = diagnoses[2:] if len(diagnoses) > 2 else []
    
    return primary, secondary, additional

def extract_diagnosis_codes_from_soap_assessment(soap_doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract diagnosis codes from SOAP assessment"""
    result = {"primary_diagnosis_code": None, "secondary_diagnosis_code": None, "associated_diagnosis_codes": [], "planned_procedures_rfa_codes": []}
    if not soap_doc: return result
    p, s, addl = primary_secondary_dx(soap_doc)
    if p:
        code = p.get("icd10") or p.get("ICD-10") or p.get("icd_10") or p.get("ICD10") or p.get("icd10_code")
        if code: result["primary_diagnosis_code"] = str(code).strip()
    if s:
        code = s.get("icd10") or s.get("ICD-10") or s.get("icd_10") or s.get("ICD10") or s.get("icd10_code")
        if code: result["secondary_diagnosis_code"] = str(code).strip()
    return result

async def fetch_if_needed(payload_obj: Optional[BaseModel], oid: Optional[str], coll_name: str) -> Optional[Dict[str, Any]]:
    if payload_obj: return payload_obj.model_dump(exclude_none=True)
    if oid:
        db = get_database()
        if db is None: return None
        try:
            doc = await db[coll_name].find_one({"_id": ObjectId(oid)})
            if doc: doc["_id"] = str(doc["_id"])
            return doc
        except: return None
    return None

async def fetch_latest_document(coll_name: str) -> Optional[Dict[str, Any]]:
    db = get_database()
    if db is None: return None
    doc = await db[coll_name].find_one(sort=[("created_at", -1)])
    if doc: doc["_id"] = str(doc["_id"])
    return doc
