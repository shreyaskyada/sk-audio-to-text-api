"""
PR-1 Generator API endpoints
Generates PR-1 form data structure from intake, follow-up, and SOAP note data
"""
import logging
import os
import json
import tempfile
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from datetime import datetime
from typing import Optional, Dict, Any, List
from bson import ObjectId
from pydantic import BaseModel
from pypdf import PdfReader
from openai import OpenAI
import httpx
from dotenv import load_dotenv

from app.models.pr1_models import (
    PR1GenerateRequest,
    IntakeFormForPR1,
    FollowUpFormForPR1,
    SOAPNoteForPR1,
    SOAPDiagnosis,
    SOAPRFAItem
)
from app.mongodb import get_database

# Load environment variables (in case this module is imported before main.py loads them)
load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter()

# Configuration - using existing collections
COLL_INTAKE = "intake_forms"
COLL_FOLLOWUP = "followup_intake_forms"
COLL_SOAP = "soap_notes"


def get_openai_api_key() -> Optional[str]:
    """Get OpenAI API key from environment (lazy loading)"""
    return os.getenv('OPENAI_API_KEY')


def create_openai_client():
    """Create OpenAI client for GPT API calls - matches the pattern from main.py"""
    openai_api_key = get_openai_api_key()
    
    if not openai_api_key:
        error_msg = "OpenAI API key not configured. Please set OPENAI_API_KEY environment variable."
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)
    
    try:
        # Create httpx client without proxy (matching main.py pattern)
        http_client = httpx.Client(
            timeout=120.0,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
        )
        
        # Create OpenAI client (matching main.py pattern)
        client = OpenAI(
            api_key=openai_api_key,
            max_retries=2,
            timeout=120.0,
            http_client=http_client
        )
        
        return client
    except Exception as e:
        error_msg = f"Failed to create OpenAI client: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


# ============================================
# UTILITY FUNCTIONS
# ============================================

def str_or_nd(val: Optional[str]) -> str:
    """Return value or '[Not documented]' if empty"""
    return val if (val is not None and str(val).strip() != "") else "[Not documented]"


def to_mmddyyyy(s: Optional[str]) -> Optional[str]:
    """Convert date string to MM/DD/YYYY format"""
    if not s:
        return None
    
    # Accept common formats and normalize to MM/DD/YYYY for PR-1
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%m/%d/%Y")
        except Exception:
            continue
    
    return s  # leave as-is if unknown format


async def fetch_if_needed(
    payload_obj: Optional[BaseModel],
    oid: Optional[str],
    coll_name: str
) -> Optional[Dict[str, Any]]:
    """Fetch document from MongoDB if ID provided, otherwise use payload object"""
    if payload_obj:
        return payload_obj.model_dump(exclude_none=True)
    
    if oid:
        try:
            db = get_database()
            if db is None:
                raise HTTPException(status_code=503, detail="Database connection not available")
            
            doc = await db[coll_name].find_one({"_id": ObjectId(oid)})
            if not doc:
                logger.warning(f"Document not found in {coll_name}: {oid}")
                return None
            
            # Convert ObjectId to string for JSON serialization
            doc["_id"] = str(doc["_id"])
            return doc
        except Exception as e:
            logger.error(f"Error fetching document from {coll_name}: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid {coll_name} id: {str(e)}")
    
    return None


async def fetch_latest_document(coll_name: str) -> Optional[Dict[str, Any]]:
    """Fetch the latest document from MongoDB collection (sorted by created_at descending)"""
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection not available")
        
        # Find the latest document (sorted by created_at descending, limit 1)
        latest_doc = await db[coll_name].find_one(
            sort=[("created_at", -1)]
        )
        
        if not latest_doc:
            logger.warning(f"No documents found in {coll_name}")
            return None
        
        # Convert ObjectId to string for JSON serialization
        latest_doc["_id"] = str(latest_doc["_id"])
        logger.info(f"✅ Fetched latest document from {coll_name} with ID: {latest_doc['_id']}")
        return latest_doc
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching latest document from {coll_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch latest document from {coll_name}: {str(e)}"
        )


def pick_name(intake_doc: Optional[Dict[str, Any]], soap_doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract patient name from intake or SOAP document"""
    a = (intake_doc or {}).get("section_a") or {}
    soap_doc = soap_doc or {}
    # Try intake section_a full_name, then SOAP patient_name, then SOAP patient_info.name
    nm = a.get("full_name") or soap_doc.get("patient_name")
    if not nm and soap_doc.get("patient_info"):
        patient_info = soap_doc.get("patient_info") or {}
        if isinstance(patient_info, dict):
            nm = patient_info.get("name")
    return nm


def pick_dob(intake_doc: Optional[Dict[str, Any]], soap_doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract date of birth from intake or SOAP document"""
    a = (intake_doc or {}).get("section_a") or {}
    soap_doc = soap_doc or {}
    # Try intake section_a date_of_birth, then SOAP dob, then SOAP patient_info
    dob = a.get("date_of_birth") or soap_doc.get("dob")
    if not dob and soap_doc.get("patient_info"):
        patient_info = soap_doc.get("patient_info") or {}
        if isinstance(patient_info, dict):
            # Date of birth might not be in patient_info, but check anyway
            pass
    return to_mmddyyyy(dob)


def pick_employer(intake_doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract employer name from intake document"""
    b = (intake_doc or {}).get("section_b") or {}
    return b.get("employer_name")


def pick_doi(intake_doc: Optional[Dict[str, Any]], soap_doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract date of injury from intake or SOAP document"""
    c = (intake_doc or {}).get("section_c") or {}
    doi = c.get("date_of_injury") or (soap_doc or {}).get("date_of_injury")
    return to_mmddyyyy(doi)


def primary_secondary_dx(soap_doc: Optional[Dict[str, Any]]) -> tuple:
    """Extract primary, secondary, and additional diagnoses from SOAP document
    
    **How Diagnoses are Filled:**
    1. Extracts diagnoses from multiple locations:
       - Root level: `diagnoses` array
       - Nested: `clinical_information.assessment.diagnoses` array
    2. Deduplicates based on condition name + ICD-10 code
    3. Assigns by position:
       - First diagnosis → Primary Diagnosis
       - Second diagnosis → Secondary Diagnosis
       - Third+ diagnoses → Additional Diagnoses
    4. Normalizes field names (handles icd10, ICD-10, ICD10 variations)
    
    **Returns:** (primary, secondary, additional) tuple
    """
    # Check multiple possible locations for diagnoses
    dxs = []
    
    # Priority 1: Root level diagnoses
    root_dxs = (soap_doc or {}).get("diagnoses") or []
    if root_dxs:
        dxs.extend(root_dxs)
        logger.info(f"Found {len(root_dxs)} diagnosis(es) at root level")
    
    # Priority 2: Nested in clinical_information.assessment.diagnoses
    clinical_info = (soap_doc or {}).get("clinical_information")
    if isinstance(clinical_info, dict):
        assessment = clinical_info.get("assessment")
        if isinstance(assessment, dict):
            assessment_dxs = assessment.get("diagnoses") or []
            if assessment_dxs:
                dxs.extend(assessment_dxs)
                logger.info(f"Found {len(assessment_dxs)} diagnosis(es) in clinical_information.assessment.diagnoses")
    
    # Handle both list of dicts and list of objects
    diagnoses = []
    for dx in dxs:
        if isinstance(dx, dict):
            diagnoses.append(dx)
        else:
            # If it's a Pydantic model, convert to dict
            diagnoses.append(dx.model_dump(exclude_none=True) if hasattr(dx, 'model_dump') else dx)
    
    # Normalize to dicts - handle field name variations
    def norm(d):
        if not d:
            return None
        if isinstance(d, dict):
            # Handle ICD-10 code field variations: icd10, ICD-10, icd_10, ICD10
            icd10_code = (
                d.get("icd10") or 
                d.get("ICD-10") or 
                d.get("icd_10") or
                d.get("ICD10") or
                d.get("icd10_code")
            )
            return {
                "condition": d.get("condition"),
                "icd10": icd10_code,
                "notes": d.get("notes")
            }
        return d
    
    # Normalize all diagnoses first
    normalized_diagnoses = [norm(d) for d in diagnoses if norm(d)]
    
    # Deduplicate: Remove duplicates based on condition + ICD-10 code
    seen = set()
    unique_diagnoses = []
    for dx in normalized_diagnoses:
        if dx:
            # Create a unique key from condition and ICD-10 code
            condition = dx.get("condition", "").strip().lower()
            icd10 = dx.get("icd10", "").strip().lower()
            key = f"{condition}|{icd10}"
            
            if key not in seen and condition:  # Only add if condition exists
                seen.add(key)
                unique_diagnoses.append(dx)
            else:
                logger.info(f"Skipping duplicate diagnosis: {dx.get('condition')} ({dx.get('icd10')})")
    
    # Assign by position: first = primary, second = secondary, rest = additional
    primary = unique_diagnoses[0] if len(unique_diagnoses) > 0 else None
    secondary = unique_diagnoses[1] if len(unique_diagnoses) > 1 else None
    additional = unique_diagnoses[2:] if len(unique_diagnoses) > 2 else []
    
    # Log diagnosis assignment
    if primary:
        logger.info(f"✓ Primary Diagnosis: {primary.get('condition')} ({primary.get('icd10')})")
    if secondary:
        logger.info(f"✓ Secondary Diagnosis: {secondary.get('condition')} ({secondary.get('icd10')})")
    if additional:
        logger.info(f"✓ Additional Diagnoses: {len(additional)} diagnosis(es)")
    
    return primary, secondary, additional


def calc_checkboxes(
    soap_doc: Optional[Dict[str, Any]],
    follow_doc: Optional[Dict[str, Any]],
    flags: Optional[Dict[str, bool]]
) -> Dict[str, bool]:
    """Calculate PR-1 page 1 checkboxes based on data and flags"""
    f = flags or {}
    s = soap_doc or {}
    fb = (follow_doc or {}).get("section_b") or {}
    
    return {
        "request_for_authorization": bool(s.get("rfa_items")) or f.get("request_for_authorization", False),
        "progress_report": f.get("progress_report", True),  # default True for routine visit
        "response_to_request_for_information": f.get("response_to_request_for_information", False),
        "expedited_request_for_authorization": f.get("expedited_request_for_authorization", False),
        "change_in_work_status": f.get("change_in_work_status", False) or bool(fb.get("work_status_perception")),
        "change_in_patient_condition": f.get("change_in_patient_condition", False) or bool(s.get("discussion_assessment")),
        "change_in_treatment_plan": f.get("change_in_treatment_plan", False) or bool(s.get("change_in_treatment_plan")),
        "released_from_care": f.get("released_from_care", False) or bool(s.get("discharge_from_care")),
        "other": f.get("other", False)
    }


def build_section_a_rfa(soap_doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build Section A: Request for Authorization (RFA)
    
    Per data mapping requirements:
    - RFA Section A + DWC RFA form (Mandatory if treatment planned)
      Data Source: Dictation / Auto-generated if treatment detected
      - Treatment Authorization Request
      - Initiates utilization review
    - MTUS Justification (Mandatory if treatment is requested)
      - Must align with MTUS evidence-based guidelines
      - Generated by: MTUS Engine (handled separately)
    """
    items: List[Dict[str, Any]] = []
    drugs: List[Dict[str, Any]] = []
    
    # Extract RFA items from SOAP dictation (per mapping: Dictation / Auto-generated if treatment detected)
    # Handle both "rfa_items" and "RFA_items" (case variations)
    rfa_items = (soap_doc or {}).get("rfa_items") or (soap_doc or {}).get("RFA_items") or []
    
    # Also check plan section for RFA if rfa_items not explicitly provided
    if not rfa_items:
        plan = (soap_doc or {}).get("plan")
        if isinstance(plan, dict) and plan.get("rfa"):
            # Auto-detect RFA from plan section
            logger.info("Auto-detecting RFA from SOAP plan section")
            # Note: This would need additional parsing logic to extract structured RFA items
            # For now, we rely on explicit rfa_items field
    
    for it in rfa_items:
        # Handle both dict and Pydantic model
        if hasattr(it, 'model_dump'):
            it = it.model_dump(exclude_none=True)
        elif not isinstance(it, dict):
            continue
        
        # Handle field name variations: service_or_good, service_good_name, service_or_good_name
        service_or_good = (
            it.get("service_or_good") or 
            it.get("service_good_name") or 
            it.get("service_or_good_name") or
            it.get("service") or
            it.get("good")
        )
        
        # Handle CPT/HCPCS code variations
        cpt_hcpcs = (
            it.get("cpt_or_hcpcs") or 
            it.get("CPT_HCPCS_codes") or
            it.get("cpt_hcpcs") or
            it.get("cpt") or
            it.get("hcpcs")
        )
        
        # Handle diagnosis code variations
        diagnosis_code = (
            it.get("diagnosis_icd10") or 
            it.get("diagnosis_codes") or
            it.get("diagnosis_code") or
            it.get("icd10")
        )
        
        base = {
            "diagnosis": diagnosis_code,
            "service_or_good": service_or_good,
            "cpt_hcpcs": cpt_hcpcs,
            "mtus_consistent": it.get("mtus_consistent"),  # MTUS alignment (MTUS Engine validates)
            "justification": it.get("justification")  # MTUS justification
        }
        
        if it.get("is_drug"):
            drugs.append({
                **base,
                "drug": it.get("drug_name"),
                "dose_form": it.get("dose_form"),
                "frequency": it.get("frequency"),
                "length_or_qty": it.get("length_or_qty"),
                "exempt_drug_review_requested": it.get("exempt_drug_review_requested", False)
            })
        else:
            items.append(base)
    
    if items or drugs:
        logger.info(f"RFA Section A: {len(items)} medical treatment requests, {len(drugs)} drug requests (from SOAP dictation)")
    
    return {
        "medical_treatment_requests": items,  # maps to PR-1 Sec A "Request for Medical Treatment (Non-Drug)"
        "drug_requests": drugs  # maps to PR-1 Sec A "Request for Drug"
    }


def extract_hpi_from_intake(intake_doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract HPI (History of Present Illness) from intake form Sections G and H"""
    if not intake_doc:
        return None
    
    section_g = intake_doc.get("section_g") or {}  # CurrentSymptoms
    section_h = intake_doc.get("section_h") or {}  # FunctionalLimitations
    
    hpi_parts = []
    
    # Extract from Section G (CurrentSymptoms)
    if section_g:
        # Symptoms list
        symptoms = section_g.get("symptoms") or []
        if symptoms:
            if isinstance(symptoms, list):
                hpi_parts.append(f"Symptoms: {', '.join(str(s) for s in symptoms if s)}")
            else:
                hpi_parts.append(f"Symptoms: {symptoms}")
        
        # Pain levels
        pain_at_rest = section_g.get("pain_at_rest")
        pain_with_activity = section_g.get("pain_with_activity")
        pain_worst = section_g.get("pain_worst")
        
        if pain_at_rest or pain_with_activity or pain_worst:
            pain_info = []
            if pain_at_rest:
                pain_info.append(f"Pain at rest: {pain_at_rest}/10")
            if pain_with_activity:
                pain_info.append(f"Pain with activity: {pain_with_activity}/10")
            if pain_worst:
                pain_info.append(f"Worst pain: {pain_worst}/10")
            if pain_info:
                hpi_parts.append(" ".join(pain_info))
        
        # Pain description
        pain_description = section_g.get("pain_description") or []
        if pain_description:
            if isinstance(pain_description, list):
                hpi_parts.append(f"Pain description: {', '.join(str(p) for p in pain_description if p)}")
            else:
                hpi_parts.append(f"Pain description: {pain_description}")
        
        # Aggravating factors
        aggravating = section_g.get("aggravating_factors")
        if aggravating:
            hpi_parts.append(f"Aggravating factors: {aggravating}")
        
        # Relieving factors
        relieving = section_g.get("relieving_factors")
        if relieving:
            hpi_parts.append(f"Relieving factors: {relieving}")
    
    # Extract from Section H (FunctionalLimitations)
    if section_h:
        # Limited activities
        limited_activities = section_h.get("limited_activities") or []
        if limited_activities:
            if isinstance(limited_activities, list):
                hpi_parts.append(f"Limited activities: {', '.join(str(a) for a in limited_activities if a)}")
            else:
                hpi_parts.append(f"Limited activities: {limited_activities}")
        
        # ADL limitations
        adl_limitations = section_h.get("adl_limitations") or []
        if adl_limitations:
            if isinstance(adl_limitations, list):
                hpi_parts.append(f"ADL limitations: {', '.join(str(a) for a in adl_limitations if a)}")
            else:
                hpi_parts.append(f"ADL limitations: {adl_limitations}")
    
    if hpi_parts:
        return " | ".join(hpi_parts)
    
    return None


def extract_objective_findings_from_intake(intake_doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract Objective Findings (Vitals) from intake form Section I (ClinicalInputs)
    
    Per data mapping requirements:
    - Objective Findings: Physical Exam Findings (Mandatory)
    - Data Source: Dictation + vitals in Patient intake form
    - Must include BOTH dictation (SOAP) AND vitals from intake form
    """
    if not intake_doc:
        return None
    
    section_i = intake_doc.get("section_i") or {}  # ClinicalInputs
    
    if not section_i:
        return None
    
    findings_parts = []
    
    # Vital signs (Mandatory per mapping - must be included from intake form)
    bp = section_i.get("bp")
    pulse = section_i.get("pulse")
    temp = section_i.get("temp")
    weight = section_i.get("weight")
    height = section_i.get("height")
    
    vital_signs = []
    if bp:
        vital_signs.append(f"BP: {bp}")
    if pulse:
        vital_signs.append(f"Pulse: {pulse}")
    if temp:
        vital_signs.append(f"Temp: {temp}")
    if weight:
        vital_signs.append(f"Weight: {weight}")
    if height:
        vital_signs.append(f"Height: {height}")
    
    if vital_signs:
        findings_parts.append("Vital Signs: " + ", ".join(vital_signs))
    
    # ROM (Range of Motion)
    rom = section_i.get("rom")
    if rom:
        findings_parts.append(f"Range of Motion: {rom}")
    
    # Strength
    strength = section_i.get("strength")
    if strength:
        findings_parts.append(f"Strength: {strength}")
    
    # Pain chart notes
    pain_chart_notes = section_i.get("pain_chart_notes")
    if pain_chart_notes:
        findings_parts.append(f"Pain Chart Notes: {pain_chart_notes}")
    
    if findings_parts:
        return " | ".join(findings_parts)
    
    return None


def build_section_b(
    soap_doc: Optional[Dict[str, Any]],
    intake_doc: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Build Section B: Evaluation and Management
    
    Per data mapping requirements:
    - Subjective Findings: Subjective Complaints (Mandatory)
      Data Source: Physician dictation (SOAP)
    - Objective Findings: Physical Exam Findings (Mandatory)
      Data Source: Dictation + vitals in Patient intake form (MUST include BOTH)
    - Diagnosis: ICD-10 Diagnosis Code (Mandatory)
      Data Source: Dictation + MTUS mapping (from SOAP diagnoses)
    - Treatment Plan: Proposed Treatment (Mandatory if applicable)
      Data Source: Dictation (SOAP)
    """
    p, s, addl = primary_secondary_dx(soap_doc)
    
    # Extract data from SOAP document (dictation)
    # Extract ALL available data from SOAP dictation (both PR-1 specific and standard SOAP fields)
    doc = soap_doc or {}
    
    # Log available SOAP fields for debugging
    available_soap_fields = [key for key in doc.keys() if key not in ['_id', 'created_at', 'updated_at', 'transcription_id']]
    logger.info(f"Available SOAP dictation fields: {', '.join(available_soap_fields)}")
    
    # Helper function to extract nested data from structures
    def extract_from_nested(obj, *keys):
        """Extract value from nested dict structure"""
        if not obj:
            return None
        for key in keys:
            if isinstance(obj, dict):
                obj = obj.get(key)
            else:
                return None
            if obj is None:
                return None
        return obj if isinstance(obj, str) else str(obj) if obj else None
    
    # SUBJECTIVE FINDINGS (Mandatory) - Data Source: Physician dictation (SOAP)
    # Extract ALL available subjective data from SOAP dictation
    chief_complaint_parts = []
    
    # Priority 1: ALWAYS include standard SOAP subjective section (this is what's stored in MongoDB)
    # This is the most reliable source as it's directly from the SOAP note generation
    soap_subjective = doc.get("subjective")
    if soap_subjective:
        # Handle both string and dict formats
        if isinstance(soap_subjective, str) and soap_subjective.strip():
            chief_complaint_parts.append(soap_subjective.strip())
            logger.info("Including Subjective Findings from SOAP dictation (subjective field)")
        elif isinstance(soap_subjective, dict):
            # If subjective is a dict, extract text content
            subjective_text = soap_subjective.get("text") or soap_subjective.get("content") or str(soap_subjective)
            if subjective_text and str(subjective_text).strip():
                chief_complaint_parts.append(str(subjective_text).strip())
                logger.info("Including Subjective Findings from SOAP dictation (subjective dict)")
    
    # Priority 1b: Extract from nested clinical_information structure (GPT-extracted format)
    clinical_info = doc.get("clinical_information")
    if clinical_info:
        if isinstance(clinical_info, dict):
            # Handle deeply nested structure: clinical_information.subjective.chief_complaint
            subjective_obj = clinical_info.get("subjective")
            if isinstance(subjective_obj, dict):
                # Extract from nested subjective dict
                chief_complaint_nested = subjective_obj.get("chief_complaint")
                brief_history_nested = subjective_obj.get("brief_history")
                
                if chief_complaint_nested and str(chief_complaint_nested).strip():
                    subjective_text = str(chief_complaint_nested).strip()
                    is_duplicate = any(subjective_text == existing.strip() for existing in chief_complaint_parts)
                    if not is_duplicate:
                        chief_complaint_parts.append(subjective_text)
                        logger.info("Including Subjective Findings from SOAP dictation (clinical_information.subjective.chief_complaint)")
                
                if brief_history_nested and str(brief_history_nested).strip():
                    subjective_text = str(brief_history_nested).strip()
                    is_duplicate = any(subjective_text == existing.strip() for existing in chief_complaint_parts)
                    if not is_duplicate:
                        chief_complaint_parts.append(subjective_text)
                        logger.info("Including Subjective Findings from SOAP dictation (clinical_information.subjective.brief_history)")
            elif isinstance(subjective_obj, str) and subjective_obj.strip():
                # If subjective is a string directly
                is_duplicate = any(subjective_obj.strip() == existing.strip() for existing in chief_complaint_parts)
                if not is_duplicate:
                    chief_complaint_parts.append(subjective_obj.strip())
                    logger.info("Including Subjective Findings from SOAP dictation (clinical_information.subjective)")
            
            # Also check for flat fields in clinical_info
            subjective_flat = (
                clinical_info.get("chief_complaint") or 
                clinical_info.get("brief_history") or
                clinical_info.get("history") or
                clinical_info.get("HPI") or
                clinical_info.get("history_of_present_illness")
            )
            if subjective_flat and str(subjective_flat).strip():
                subjective_text = str(subjective_flat).strip()
                is_duplicate = any(subjective_text == existing.strip() for existing in chief_complaint_parts)
                if not is_duplicate:
                    chief_complaint_parts.append(subjective_text)
                    logger.info("Including Subjective Findings from SOAP dictation (clinical_information flat fields)")
        elif isinstance(clinical_info, str) and clinical_info.strip():
            # If clinical_information is a string, use it as subjective
            is_duplicate = any(clinical_info.strip() == existing.strip() for existing in chief_complaint_parts)
            if not is_duplicate:
                chief_complaint_parts.append(clinical_info.strip())
                logger.info("Including Subjective Findings from SOAP dictation (clinical_information as string)")
    
    # Priority 2: Get HPI from PR-1 specific fields (if available)
    soap_chief_complaint = doc.get("chief_complaint") or doc.get("brief_history")
    if soap_chief_complaint and str(soap_chief_complaint).strip():
        subjective_text = str(soap_chief_complaint).strip()
        # Only add if not duplicate
        is_duplicate = any(subjective_text == existing.strip() for existing in chief_complaint_parts)
        if not is_duplicate:
            chief_complaint_parts.append(subjective_text)
            logger.info("Including Subjective Findings from SOAP dictation (chief_complaint/brief_history)")
    
    # Priority 3: Check reason_for_visit field (might contain chief complaint info)
    reason_for_visit = doc.get("reason_for_visit")
    if reason_for_visit and str(reason_for_visit).strip():
        reason_text = str(reason_for_visit).strip()
        is_duplicate = any(reason_text == existing.strip() for existing in chief_complaint_parts)
        if not is_duplicate:
            chief_complaint_parts.append(reason_text)
            logger.info("Including Subjective Findings from SOAP dictation (reason_for_visit)")
    
    # Priority 4: Get HPI from intake form (Sections G + H) - supplementary information
    if intake_doc:
        hpi_from_intake = extract_hpi_from_intake(intake_doc)
        if hpi_from_intake:
            is_duplicate = any(hpi_from_intake == existing.strip() for existing in chief_complaint_parts)
            if not is_duplicate:
                chief_complaint_parts.append(hpi_from_intake)
                logger.info("Including supplementary HPI from intake form (Sections G + H)")
    
    # Combine all HPI sources (SOAP dictation is primary)
    chief_complaint = " | ".join(filter(None, chief_complaint_parts)) if chief_complaint_parts else None
    
    # Log warning if no subjective data found
    if not chief_complaint:
        logger.warning("⚠️ No subjective findings found in SOAP dictation! Checked: subjective, chief_complaint, brief_history, reason_for_visit")
        logger.warning(f"Available SOAP fields: {list(doc.keys())}")
    
    # OBJECTIVE FINDINGS (Mandatory) - Data Source: Dictation + vitals in Patient intake form
    # MUST include BOTH SOAP dictation AND intake form Section I vitals
    physical_exam_parts = []
    
    # Get objective findings from SOAP dictation (required per mapping)
    # Priority: Check both physical_exam (PR-1 specific) and objective (standard SOAP field)
    # Always include objective field from SOAP notes stored in MongoDB
    soap_objective = doc.get("objective")
    soap_physical_exam = doc.get("physical_exam")
    
    # Include physical_exam if it exists (PR-1 specific field)
    if soap_physical_exam and str(soap_physical_exam).strip():
        physical_exam_parts.append(str(soap_physical_exam).strip())
        logger.info("Including Objective Findings from SOAP dictation (physical_exam field)")
    
    # ALWAYS include objective field from SOAP (standard SOAP note field stored in MongoDB)
    if soap_objective and str(soap_objective).strip():
        objective_text = str(soap_objective).strip()
        # Only add if not already added (to avoid duplicates if physical_exam and objective are the same)
        is_duplicate = any(objective_text == existing.strip() for existing in physical_exam_parts)
        if not is_duplicate:
            physical_exam_parts.append(objective_text)
            logger.info("Including Objective Findings from SOAP dictation (objective field)")
        else:
            logger.info("Objective field from SOAP is duplicate of physical_exam, skipping")
    
    # Extract from nested clinical_information structure (GPT-extracted format)
    if clinical_info and isinstance(clinical_info, dict):
        # Check for objective field (can be string or nested)
        objective_from_clinical = clinical_info.get("objective")
        if objective_from_clinical:
            if isinstance(objective_from_clinical, str) and objective_from_clinical.strip():
                objective_text = objective_from_clinical.strip()
                is_duplicate = any(objective_text == existing.strip() for existing in physical_exam_parts)
                if not is_duplicate:
                    physical_exam_parts.append(objective_text)
                    logger.info("Including Objective Findings from SOAP dictation (clinical_information.objective)")
            elif isinstance(objective_from_clinical, dict):
                # If objective is a dict, extract text content
                objective_text = objective_from_clinical.get("text") or objective_from_clinical.get("content") or str(objective_from_clinical)
                if objective_text and str(objective_text).strip():
                    objective_text_str = str(objective_text).strip()
                    is_duplicate = any(objective_text_str == existing.strip() for existing in physical_exam_parts)
                    if not is_duplicate:
                        physical_exam_parts.append(objective_text_str)
                        logger.info("Including Objective Findings from SOAP dictation (clinical_information.objective dict)")
        
        # Also check for physical_exam field
        physical_exam_from_clinical = (
            clinical_info.get("physical_exam") or
            clinical_info.get("physical_examination") or
            clinical_info.get("exam") or
            clinical_info.get("examination")
        )
        if physical_exam_from_clinical and str(physical_exam_from_clinical).strip():
            objective_text = str(physical_exam_from_clinical).strip()
            is_duplicate = any(objective_text == existing.strip() for existing in physical_exam_parts)
            if not is_duplicate:
                physical_exam_parts.append(objective_text)
                logger.info("Including Objective Findings from SOAP dictation (clinical_information.physical_exam)")
    
    # If neither field exists, log a warning
    if not physical_exam_parts:
        logger.warning("No objective findings found in SOAP dictation (neither 'physical_exam' nor 'objective' field present)")
    
    # Get vitals from intake form Section I (required per mapping - must include both)
    if intake_doc:
        objective_from_intake = extract_objective_findings_from_intake(intake_doc)
        if objective_from_intake:
            physical_exam_parts.append(objective_from_intake)
            logger.info("Including Objective Findings (vitals) from intake form Section I (required per mapping)")
        else:
            logger.warning("Intake form Section I (vitals) not found - Objective Findings should include both dictation and vitals per mapping requirements")
    
    # Combine SOAP dictation + intake form vitals (both required per mapping)
    physical_exam = " | ".join(filter(None, physical_exam_parts)) if physical_exam_parts else None
    
    # DIAGNOSIS (Mandatory) - Data Source: Dictation + MTUS mapping
    # Diagnoses are extracted from SOAP dictation (primary_secondary_dx function)
    # MTUS mapping is handled separately by MTUS engine
    
    # TREATMENT PLAN (Mandatory if applicable) - Data Source: Dictation (SOAP)
    # Extract ALL available treatment plan data from SOAP dictation
    treatment_plan_parts = []
    
    # Get from PR-1 specific field (if available)
    treatment_plan_text = doc.get("treatment_plan_text")
    if treatment_plan_text and str(treatment_plan_text).strip():
        treatment_plan_parts.append(str(treatment_plan_text).strip())
        logger.info("Including Treatment Plan from SOAP dictation (treatment_plan_text)")
    
    # Extract from nested treatment_plan_information structure (GPT-extracted format)
    treatment_plan_info = doc.get("treatment_plan_information")
    if treatment_plan_info:
        if isinstance(treatment_plan_info, dict):
            # Extract all treatment plan components and combine them
            plan_components = []
            
            # Extract individual components
            surgery = treatment_plan_info.get("surgery")
            pt = treatment_plan_info.get("PT") or treatment_plan_info.get("pt") or treatment_plan_info.get("physical_therapy")
            injections = treatment_plan_info.get("injections")
            imaging = treatment_plan_info.get("imaging")
            dme = treatment_plan_info.get("DME") or treatment_plan_info.get("dme") or treatment_plan_info.get("durable_medical_equipment")
            
            if surgery and str(surgery).strip():
                plan_components.append(f"Surgery: {str(surgery).strip()}")
            if pt and str(pt).strip():
                plan_components.append(f"PT: {str(pt).strip()}")
            if injections and str(injections).strip():
                plan_components.append(f"Injections: {str(injections).strip()}")
            if imaging and str(imaging).strip():
                plan_components.append(f"Imaging: {str(imaging).strip()}")
            if dme and str(dme).strip():
                plan_components.append(f"DME: {str(dme).strip()}")
            
            # Also check for general treatment/plan fields
            plan_text = (
                treatment_plan_info.get("treatment") or 
                treatment_plan_info.get("plan") or
                treatment_plan_info.get("text") or
                treatment_plan_info.get("treatment_plan")
            )
            
            # Combine components
            if plan_components:
                combined_plan = " | ".join(plan_components)
                if plan_text and str(plan_text).strip():
                    combined_plan = f"{str(plan_text).strip()} | {combined_plan}"
                plan_text_str = combined_plan
            elif plan_text and str(plan_text).strip():
                plan_text_str = str(plan_text).strip()
            else:
                plan_text_str = None
            
            if plan_text_str:
                is_duplicate = any(plan_text_str == existing.strip() for existing in treatment_plan_parts)
                if not is_duplicate:
                    treatment_plan_parts.append(plan_text_str)
                    logger.info("Including Treatment Plan from SOAP dictation (treatment_plan_information)")
        elif isinstance(treatment_plan_info, str) and treatment_plan_info.strip():
            plan_text_str = treatment_plan_info.strip()
            is_duplicate = any(plan_text_str == existing.strip() for existing in treatment_plan_parts)
            if not is_duplicate:
                treatment_plan_parts.append(plan_text_str)
                logger.info("Including Treatment Plan from SOAP dictation (treatment_plan_information as string)")
    
    # ALWAYS include standard SOAP plan section (this is what's stored in MongoDB)
    plan_obj = doc.get("plan")
    if plan_obj:
        if isinstance(plan_obj, dict):
            # Extract from plan dict structure
            plan_text = plan_obj.get("treatment") or plan_obj.get("text") or plan_obj.get("plan")
            if plan_text and str(plan_text).strip():
                plan_text_str = str(plan_text).strip()
                is_duplicate = any(plan_text_str == existing.strip() for existing in treatment_plan_parts)
                if not is_duplicate:
                    treatment_plan_parts.append(plan_text_str)
                    logger.info("Including Treatment Plan from SOAP dictation (plan.treatment/text)")
        elif isinstance(plan_obj, str) and plan_obj.strip():
            # Plan is a string
            plan_text_str = plan_obj.strip()
            is_duplicate = any(plan_text_str == existing.strip() for existing in treatment_plan_parts)
            if not is_duplicate:
                treatment_plan_parts.append(plan_text_str)
                logger.info("Including Treatment Plan from SOAP dictation (plan section)")
    
    # Extract from nested clinical_information structure
    if clinical_info and isinstance(clinical_info, dict):
        # Handle deeply nested structure: clinical_information.plan.treatment_plan_text
        plan_obj_clinical = clinical_info.get("plan")
        if isinstance(plan_obj_clinical, dict):
            # Extract from nested plan dict
            treatment_plan_nested = (
                plan_obj_clinical.get("treatment_plan_text") or
                plan_obj_clinical.get("treatment_plan") or
                plan_obj_clinical.get("treatment") or
                plan_obj_clinical.get("plan") or
                plan_obj_clinical.get("text")
            )
            if treatment_plan_nested and str(treatment_plan_nested).strip():
                plan_text_str = str(treatment_plan_nested).strip()
                is_duplicate = any(plan_text_str == existing.strip() for existing in treatment_plan_parts)
                if not is_duplicate:
                    treatment_plan_parts.append(plan_text_str)
                    logger.info("Including Treatment Plan from SOAP dictation (clinical_information.plan.treatment_plan_text)")
        elif isinstance(plan_obj_clinical, str) and plan_obj_clinical.strip():
            # If plan is a string directly
            is_duplicate = any(plan_obj_clinical.strip() == existing.strip() for existing in treatment_plan_parts)
            if not is_duplicate:
                treatment_plan_parts.append(plan_obj_clinical.strip())
                logger.info("Including Treatment Plan from SOAP dictation (clinical_information.plan)")
        
        # Also check for flat fields in clinical_info
        plan_flat = (
            clinical_info.get("treatment_plan") or
            clinical_info.get("treatment")
        )
        if plan_flat and str(plan_flat).strip():
            plan_text_str = str(plan_flat).strip()
            is_duplicate = any(plan_text_str == existing.strip() for existing in treatment_plan_parts)
            if not is_duplicate:
                treatment_plan_parts.append(plan_text_str)
                logger.info("Including Treatment Plan from SOAP dictation (clinical_information flat fields)")
    
    # Combine all treatment plan sources
    treatment_plan = " | ".join(filter(None, treatment_plan_parts)) if treatment_plan_parts else None
    
    # ASSESSMENT/DISCUSSION - Extract ALL available assessment data from SOAP dictation
    assessment_parts = []
    
    # Get from PR-1 specific field (if available)
    discussion_assessment_text = doc.get("discussion_assessment")
    if discussion_assessment_text and str(discussion_assessment_text).strip():
        assessment_parts.append(str(discussion_assessment_text).strip())
        logger.info("Including Assessment from SOAP dictation (discussion_assessment)")
    
    # ALWAYS include standard SOAP assessment section (this is what's stored in MongoDB)
    soap_assessment = doc.get("assessment")
    if soap_assessment and str(soap_assessment).strip():
        assessment_text = str(soap_assessment).strip()
        is_duplicate = any(assessment_text == existing.strip() for existing in assessment_parts)
        if not is_duplicate:
            assessment_parts.append(assessment_text)
            logger.info("Including Assessment from SOAP dictation (assessment section)")
    
    # Extract from nested clinical_information structure (GPT-extracted format)
    if clinical_info and isinstance(clinical_info, dict):
        # Handle deeply nested structure: clinical_information.assessment.discussion_assessment
        assessment_obj = clinical_info.get("assessment")
        if isinstance(assessment_obj, dict):
            # Extract from nested assessment dict
            discussion_assessment_nested = assessment_obj.get("discussion_assessment") or assessment_obj.get("discussion")
            if discussion_assessment_nested and str(discussion_assessment_nested).strip():
                assessment_text = str(discussion_assessment_nested).strip()
                is_duplicate = any(assessment_text == existing.strip() for existing in assessment_parts)
                if not is_duplicate:
                    assessment_parts.append(assessment_text)
                    logger.info("Including Assessment from SOAP dictation (clinical_information.assessment.discussion_assessment)")
        elif isinstance(assessment_obj, str) and assessment_obj.strip():
            # If assessment is a string directly
            is_duplicate = any(assessment_obj.strip() == existing.strip() for existing in assessment_parts)
            if not is_duplicate:
                assessment_parts.append(assessment_obj.strip())
                logger.info("Including Assessment from SOAP dictation (clinical_information.assessment)")
        
        # Also check for flat fields in clinical_info
        assessment_flat = (
            clinical_info.get("discussion") or
            clinical_info.get("discussion_assessment") or
            clinical_info.get("impression")
        )
        if assessment_flat and str(assessment_flat).strip():
            assessment_text = str(assessment_flat).strip()
            is_duplicate = any(assessment_text == existing.strip() for existing in assessment_parts)
            if not is_duplicate:
                assessment_parts.append(assessment_text)
                logger.info("Including Assessment from SOAP dictation (clinical_information flat fields)")
    
    # Combine all assessment sources
    discussion_assessment = " | ".join(filter(None, assessment_parts)) if assessment_parts else None
    
    # CURRENT TREATMENT PLANS INCLUDING MEDICATION - Extract from SOAP dictation
    # Field 3: "Current Treatment Plans including Medication (list all medications, dose, and frequency)"
    current_treatment_parts = []
    
    # Get from flat field (if available)
    current_treatments_flat = doc.get("current_treatments_and_meds") or doc.get("current_treatments") or doc.get("current_medications")
    if current_treatments_flat and str(current_treatments_flat).strip():
        current_treatment_parts.append(str(current_treatments_flat).strip())
        logger.info("Including Current Treatment Plans from SOAP dictation (current_treatments_and_meds)")
    
    # Extract from nested clinical_information.plan structure
    if clinical_info and isinstance(clinical_info, dict):
        plan_obj = clinical_info.get("plan")
        if isinstance(plan_obj, dict):
            # Extract current treatments from nested plan
            current_treatments_nested = (
                plan_obj.get("current_treatments") or
                plan_obj.get("current_medications") or
                plan_obj.get("medications") or
                plan_obj.get("current_treatment_and_meds")
            )
            if current_treatments_nested and str(current_treatments_nested).strip():
                treatment_text = str(current_treatments_nested).strip()
                is_duplicate = any(treatment_text == existing.strip() for existing in current_treatment_parts)
                if not is_duplicate:
                    current_treatment_parts.append(treatment_text)
                    logger.info("Including Current Treatment Plans from SOAP dictation (clinical_information.plan.current_treatments)")
    
    # Combine all current treatment sources
    current_treatment_and_meds = " | ".join(filter(None, current_treatment_parts)) if current_treatment_parts else None
    
    # OUTCOMES ADL - Extract from SOAP dictation
    # Field 4: "Outcomes to include Functional Improvements and Activities of Daily Living (ADL)"
    outcomes_parts = []
    
    # Get from flat fields
    outcomes_adl_flat = doc.get("outcomes_adl") or doc.get("outcomes") or doc.get("functional_outcomes")
    if outcomes_adl_flat and str(outcomes_adl_flat).strip():
        outcomes_parts.append(str(outcomes_adl_flat).strip())
        logger.info("Including Outcomes ADL from SOAP dictation (outcomes_adl)")
    
    # Extract from nested clinical_information.plan structure
    if clinical_info and isinstance(clinical_info, dict):
        plan_obj = clinical_info.get("plan")
        if isinstance(plan_obj, dict):
            outcomes_nested = (
                plan_obj.get("outcomes") or
                plan_obj.get("outcomes_adl") or
                plan_obj.get("functional_outcomes") or
                plan_obj.get("adl_outcomes")
            )
            if outcomes_nested and str(outcomes_nested).strip():
                outcomes_text = str(outcomes_nested).strip()
                is_duplicate = any(outcomes_text == existing.strip() for existing in outcomes_parts)
                if not is_duplicate:
                    outcomes_parts.append(outcomes_text)
                    logger.info("Including Outcomes ADL from SOAP dictation (clinical_information.plan.outcomes)")
    
    # Combine all outcomes sources
    outcomes_adl = " | ".join(filter(None, outcomes_parts)) if outcomes_parts else None
    
    # ADL GOAL FOR NEXT VISIT/TREATMENT PERIOD - Extract from SOAP dictation
    # Field: "ADL Goal for next visit/treatment period (explain):"
    adl_goal_parts = []
    
    # Get from flat fields
    adl_goal_flat = doc.get("adl_goal_next_visit") or doc.get("adl_goal") or doc.get("goal_next_visit")
    if adl_goal_flat and str(adl_goal_flat).strip():
        adl_goal_parts.append(str(adl_goal_flat).strip())
        logger.info("Including ADL Goal from SOAP dictation (adl_goal_next_visit)")
    
    # Extract from nested clinical_information.plan structure
    if clinical_info and isinstance(clinical_info, dict):
        plan_obj = clinical_info.get("plan")
        if isinstance(plan_obj, dict):
            adl_goal_nested = (
                plan_obj.get("adl_goal_next_visit") or
                plan_obj.get("adl_goal") or
                plan_obj.get("goal_next_visit") or
                plan_obj.get("next_visit_goal") or
                plan_obj.get("treatment_goal")
            )
            if adl_goal_nested and str(adl_goal_nested).strip():
                goal_text = str(adl_goal_nested).strip()
                is_duplicate = any(goal_text == existing.strip() for existing in adl_goal_parts)
                if not is_duplicate:
                    adl_goal_parts.append(goal_text)
                    logger.info("Including ADL Goal from SOAP dictation (clinical_information.plan.adl_goal)")
    
    # Combine all ADL goal sources
    adl_goal_next_visit = " | ".join(filter(None, adl_goal_parts)) if adl_goal_parts else None
    
    # DISABILITY STATUS - Extract from SOAP dictation
    # Field 5: "Disability Status:"
    disability_parts = []
    
    # Get from flat fields
    disability_flat = doc.get("disability_status") or doc.get("disability") or doc.get("functional_status")
    if disability_flat and str(disability_flat).strip():
        disability_parts.append(str(disability_flat).strip())
        logger.info("Including Disability Status from SOAP dictation (disability_status)")
    
    # Extract from nested clinical_information structure
    if clinical_info and isinstance(clinical_info, dict):
        # Check in plan section
        plan_obj = clinical_info.get("plan")
        if isinstance(plan_obj, dict):
            disability_nested = (
                plan_obj.get("disability_status") or
                plan_obj.get("disability") or
                plan_obj.get("functional_status")
            )
            if disability_nested and str(disability_nested).strip():
                disability_text = str(disability_nested).strip()
                is_duplicate = any(disability_text == existing.strip() for existing in disability_parts)
                if not is_duplicate:
                    disability_parts.append(disability_text)
                    logger.info("Including Disability Status from SOAP dictation (clinical_information.plan.disability_status)")
        
        # Also check in assessment section
        assessment_obj = clinical_info.get("assessment")
        if isinstance(assessment_obj, dict):
            disability_from_assessment = (
                assessment_obj.get("disability_status") or
                assessment_obj.get("disability") or
                assessment_obj.get("functional_status")
            )
            if disability_from_assessment and str(disability_from_assessment).strip():
                disability_text = str(disability_from_assessment).strip()
                is_duplicate = any(disability_text == existing.strip() for existing in disability_parts)
                if not is_duplicate:
                    disability_parts.append(disability_text)
                    logger.info("Including Disability Status from SOAP dictation (clinical_information.assessment.disability_status)")
    
    # Combine all disability status sources
    disability_status = " | ".join(filter(None, disability_parts)) if disability_parts else None
    
    # Log extraction summary (after all extractions are complete)
    extraction_summary = {
        "subjective": "✓ Extracted" if chief_complaint else "✗ Missing",
        "objective": "✓ Extracted" if physical_exam else "✗ Missing",
        "assessment": "✓ Extracted" if discussion_assessment else "✗ Missing",
        "treatment_plan": "✓ Extracted" if treatment_plan else "✗ Missing",
        "current_treatment_and_meds": "✓ Extracted" if current_treatment_and_meds else "✗ Missing",
        "outcomes_adl": "✓ Extracted" if outcomes_adl else "✗ Missing",
        "adl_goal_next_visit": "✓ Extracted" if adl_goal_next_visit else "✗ Missing",
        "disability_status": "✓ Extracted" if disability_status else "✗ Missing",
        "diagnoses": f"✓ Extracted ({len([d for d in [p, s] + addl if d])} diagnoses)" if (p or s or addl) else "✗ Missing"
    }
    logger.info(f"SOAP dictation extraction summary: {extraction_summary}")
    
    return {
        "diagnoses": {
            "primary": p,
            "secondary": s,
            "additional": addl
        },
        "chief_complaint_and_history": chief_complaint,
        "physical_exam": physical_exam,
        "current_treatment_and_meds": current_treatment_and_meds,  # Field 3: Current Treatment Plans including Medication
        "outcomes_adl": outcomes_adl,  # Field 4: Outcomes ADL
        "adl_goal_next_visit": adl_goal_next_visit,  # ADL Goal for next visit/treatment period
        "disability_status": disability_status,  # Field 5: Disability Status
        "secondary_physician_reports": doc.get("secondary_physician_reports"),
        "discussion_assessment": discussion_assessment,
        "treatment_plan": treatment_plan,
        "continue_same_treatment": doc.get("continue_same_treatment"),
        "discharge_from_care": doc.get("discharge_from_care"),
        "change_in_treatment_plan": doc.get("change_in_treatment_plan"),
        "dispense_as_written": doc.get("dispense_as_written")
    }


def build_section_c(
    intake_doc: Optional[Dict[str, Any]],
    follow_doc: Optional[Dict[str, Any]],
    soap_doc: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Build Section C: Work Status
    
    Per data mapping requirements:
    - Functional and Work Status: Work Capacity / Restrictions (Mandatory)
      Data Source: Providers' Dictation (SOAP)
      RTW status: Full Duty / Modified Duty / TTD (Temporary Total Disability)
    """
    b = (follow_doc or {}).get("section_b") or {}
    doc = soap_doc or {}
    
    # Work Status (Mandatory) - Data Source: Providers' Dictation (SOAP)
    # Handle nested work_status structure: work_status.work_status
    work_status_obj = doc.get("work_status")
    work_status = None
    restrictions = None
    restrictions_duration = None
    meds_affect_alertness = None
    meds_effect_description = None
    
    if isinstance(work_status_obj, dict):
        # Extract from nested work_status dict
        work_status = work_status_obj.get("work_status") or work_status_obj.get("status")
        restrictions = work_status_obj.get("restrictions")
        
        # Handle nested dates structure
        dates_obj = work_status_obj.get("dates")
        if isinstance(dates_obj, dict):
            restrictions_duration = dates_obj.get("duration")
        
        # Handle medication effects
        meds_obj = work_status_obj.get("medication_effects")
        if isinstance(meds_obj, dict):
            meds_affect_alertness = meds_obj.get("affect_alertness")
            meds_effect_description = meds_obj.get("description")
        elif isinstance(meds_obj, str):
            meds_effect_description = meds_obj
        logger.info("Including Work Status from SOAP dictation (nested work_status structure)")
    elif isinstance(work_status_obj, str):
        work_status = work_status_obj
        logger.info("Including Work Status from SOAP dictation (work_status as string)")
    
    # Fallback to flat fields if nested structure not found
    if not work_status:
        work_status = doc.get("work_status")
    if not restrictions:
        restrictions = doc.get("restrictions")
    if not restrictions_duration:
        restrictions_duration = doc.get("restrictions_duration")
    if not meds_affect_alertness:
        meds_affect_alertness = doc.get("meds_affect_alertness")
    if not meds_effect_description:
        meds_effect_description = doc.get("meds_effect_description")
    
    # Priority: SOAP dictation > follow-up form (fallback)
    if work_status:
        logger.info("Including Work Status from SOAP dictation (Providers' Dictation)")
    elif b.get("work_status_perception"):
        work_status = b.get("work_status_perception")
        logger.info("Including Work Status from follow-up form (fallback)")
    else:
        work_status = "[Not documented]"
        logger.warning("Work Status not found in SOAP dictation (mandatory per mapping)")
    
    return {
        "instruction": work_status,
        "return_full_duty_date": to_mmddyyyy(doc.get("return_full_duty_date")),
        "unable_to_work_from": None,  # optional: populate if you track ranges
        "unable_to_work_to": None,
        "restrictions_text": restrictions,  # From SOAP dictation (nested or flat)
        "restrictions_duration": restrictions_duration,  # From SOAP dictation (nested or flat)
        "meds_affect_alertness": meds_affect_alertness,  # From SOAP dictation (nested or flat)
        "meds_effect_description": meds_effect_description,  # From SOAP dictation (nested or flat)
        "anticipate_full_duty_date": to_mmddyyyy(doc.get("return_full_duty_date")),
        "anticipate_modified_duty_date": to_mmddyyyy(doc.get("return_modified_duty_date")),
        "anticipate_mmi_date": to_mmddyyyy(doc.get("mmi_date")),
        "next_visit_date": to_mmddyyyy(doc.get("next_visit_date")),
        "discharged_from_care_date": to_mmddyyyy(doc.get("discharged_date"))
    }


def normalize_pr1_header(
    intake_doc: Optional[Dict[str, Any]],
    soap_doc: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Build PR-1 header information from intake and SOAP documents
    
    Per data mapping requirements:
    - Administrative Data: Patient, Physician and claim admin Information (Mandatory)
      Data Source: Patient Intake/EMR/Manual
    - Date of First Examination (Mandatory)
      Data Source: EMR Visit record (from SOAP date_of_service)
    """
    patient_name = pick_name(intake_doc, soap_doc)
    dob = pick_dob(intake_doc, soap_doc)
    doi = pick_doi(intake_doc, soap_doc)
    soap_doc = soap_doc or {}
    intake_doc = intake_doc or {}
    
    # Extract from nested patient_information structure if available
    patient_info = soap_doc.get("patient_information")
    if isinstance(patient_info, dict):
        if not patient_name:
            patient_name = patient_info.get("name")
        if not dob:
            dob = patient_info.get("DOB") or patient_info.get("dob")
        if not doi:
            doi = patient_info.get("date_of_injury")
    
    # Get claim number from SOAP or intake
    claim_number = soap_doc.get("claim_number")
    if not claim_number and isinstance(patient_info, dict):
        claim_number = patient_info.get("claim_number")
    if not claim_number:
        claim_number = ((intake_doc.get("section_a") or {}).get("claim_number"))
    # Also check intake section_c for claim number
    if not claim_number:
        claim_number = (intake_doc.get("section_c") or {}).get("claim_number")
    
    # Extract employer from nested structure
    employer = pick_employer(intake_doc) or soap_doc.get("employer")
    if not employer and isinstance(patient_info, dict):
        employer = patient_info.get("employer")
    
    # Extract physician information from nested structure
    physician_info = soap_doc.get("physician_information")
    examiner = soap_doc.get("examiner")
    specialty = soap_doc.get("specialty")
    npi = soap_doc.get("npi")
    state_license = soap_doc.get("state_license")
    practice_name = soap_doc.get("practice_name")
    contact_phone = soap_doc.get("contact_phone")
    contact_fax = soap_doc.get("contact_fax")
    contact_email = soap_doc.get("contact_email")
    
    if isinstance(physician_info, dict):
        if not examiner:
            examiner = physician_info.get("examiner")
        if not specialty:
            specialty = physician_info.get("specialty")
        if not npi:
            npi = physician_info.get("NPI") or physician_info.get("npi")
        if not state_license:
            state_license = physician_info.get("state_license")
        if not practice_name:
            practice_name = physician_info.get("practice_name")
        
        # Handle nested contact_info
        contact_info = physician_info.get("contact_info")
        if isinstance(contact_info, dict):
            if not contact_phone:
                contact_phone = contact_info.get("phone") or contact_info.get("telephone")
            if not contact_fax:
                contact_fax = contact_info.get("fax")
            if not contact_email:
                contact_email = contact_info.get("email")
        elif isinstance(contact_info, str):
            contact_phone = contact_info
    
    # Date of First Examination (Mandatory) - from EMR Visit record (SOAP date_of_service)
    date_of_first_examination = to_mmddyyyy(soap_doc.get("date_of_service"))
    
    return {
        "patient_name": str_or_nd(patient_name),
        "date_of_injury": str_or_nd(doi),
        "date_of_birth": str_or_nd(dob),
        "date_of_first_examination": str_or_nd(date_of_first_examination),  # Mandatory per mapping
        "claim_number": str_or_nd(claim_number),
        "employer": str_or_nd(employer),
        "physician": {
            "physician_name": str_or_nd(examiner),
            "practice_name": practice_name,
            "contact_name": None,
            "address": None,
            "city": None,
            "state": None,
            "zip": None,
            "telephone": contact_phone,
            "fax": contact_fax,
            "email": contact_email,
            "specialty": specialty,
            "state_license_number": state_license,
            "npi_number": npi,
            "primary_treating_physician_name": soap_doc.get("primary_treating_physician")
        },
        "claims_administrator": {
            "name": None,
            "address": None,
            "city": None,
            "state": None,
            "zip": None,
            "contact_name": None,
            "email": None,
            "telephone": None,
            "fax": None
        }
    }


def build_pr1_payload(
    intake_doc: Optional[Dict[str, Any]],
    follow_doc: Optional[Dict[str, Any]],
    soap_doc: Optional[Dict[str, Any]],
    flags: Optional[Dict[str, bool]]
) -> Dict[str, Any]:
    """Build complete PR-1 payload structure"""
    header = normalize_pr1_header(intake_doc, soap_doc)
    checkboxes = calc_checkboxes(soap_doc, follow_doc, flags)
    section_a = build_section_a_rfa(soap_doc)
    # Pass intake_doc to build_section_b so it can extract HPI and Objective Findings
    section_b = build_section_b(soap_doc, intake_doc)
    section_c = build_section_c(intake_doc, follow_doc, soap_doc)
    
    # Page 2 signature block
    signature_block = {
        "include_section_a": bool(section_a["medical_treatment_requests"] or section_a["drug_requests"]),
        "include_section_b": True,
        "include_section_c": True,
        "physician_signature": (soap_doc or {}).get("examiner"),
        "signature_date": datetime.now().strftime("%m/%d/%Y"),
        "executed_at": None
    }
    
    return {
        "form_version": "DWC PR-1 (1/19)",
        "page1_checkboxes": checkboxes,
        "header_admin": header,
        "page2_signature_and_included_sections": signature_block,
        "section_a_request_for_authorization": section_a,
        "section_b_evaluation_management": section_b,
        "section_c_work_status": section_c
    }


# ============================================
# PR-1 GENERATOR API ENDPOINTS
# ============================================

@router.post("/pr1/generate")
async def generate_pr1(payload: PR1GenerateRequest):
    """
    Generate PR-1 form data structure from intake, follow-up, and/or SOAP note data
    
    **Workflow:**
    1. Intake and follow-up forms are automatically fetched from latest data if `use_latest_intake` or `use_latest_followup` is True
    2. SOAP note data should be provided as JSON (can be generated from PDF using GPT API)
    3. All data is combined to generate the complete PR-1 form structure
    
    **Parameters:**
    - intake: Optional IntakeFormForPR1 object (or use intake_id or use_latest_intake)
    - followup: Optional FollowUpFormForPR1 object (or use followup_id or use_latest_followup)
    - soap: Optional SOAPNoteForPR1 object (or use soap_id) - Typically provided as JSON generated from PDF
    - intake_id: Optional MongoDB ObjectId string to fetch specific intake form
    - followup_id: Optional MongoDB ObjectId string to fetch specific follow-up form
    - soap_id: Optional MongoDB ObjectId string to fetch SOAP note
    - use_latest_intake: Optional boolean (default: False) - If True, automatically fetches latest intake form from MongoDB
    - use_latest_followup: Optional boolean (default: False) - If True, automatically fetches latest follow-up form from MongoDB
    - flags: Optional dictionary of PR-1 checkboxes (e.g., {"progress_report": True, "request_for_authorization": True})
    
    **Returns:**
    - status: Success status
    - pr1_values: Complete PR-1 form data structure ready for PDF filling
    - metadata: Information about which documents were used (intake_id, followup_id, soap_id, sources)
    
    **Example Request (with latest data fetching):**
    ```json
    {
        "use_latest_intake": true,
        "use_latest_followup": true,
        "soap": {
            "patient_name": "John Doe",
            "dob": "01/01/1980",
            "examiner": "Dr. Smith",
            "diagnoses": [
                {
                    "condition": "Lower back strain",
                    "icd10": "S39.012A"
                }
            ],
            "rfa_items": [
                {
                    "service_or_good": "Physical Therapy",
                    "cpt_or_hcpcs": "97110",
                    "diagnosis_icd10": "S39.012A",
                    "justification": "Medical necessity"
                }
            ]
        },
        "flags": {
            "progress_report": true,
            "request_for_authorization": true
        }
    }
    ```
    
    **Example Request (with MongoDB IDs):**
    ```json
    {
        "intake_id": "507f1f77bcf86cd799439011",
        "followup_id": "507f1f77bcf86cd799439012",
        "soap_id": "507f1f77bcf86cd799439013",
        "flags": {
            "progress_report": true,
            "request_for_authorization": true
        }
    }
    ```
    
    **Example Request (with embedded objects):**
    ```json
    {
        "soap": {
            "patient_name": "John Doe",
            "dob": "01/01/1980",
            "examiner": "Dr. Smith",
            "diagnoses": [
                {
                    "condition": "Lower back strain",
                    "icd10": "S39.012A"
                }
            ],
            "rfa_items": [
                {
                    "service_or_good": "Physical Therapy",
                    "cpt_or_hcpcs": "97110",
                    "diagnosis_icd10": "S39.012A",
                    "justification": "Medical necessity"
                }
            ]
        },
        "flags": {
            "progress_report": true,
            "request_for_authorization": true
        }
    }
    ```
    """
    try:
        # Fetch/resolve data sources
        # Priority: embedded object > specific ID > latest (if use_latest_* is True) > None
        
        # Handle intake form
        intake_doc = None
        intake_source = None  # Track source: "embedded", "id", "latest", or None
        if payload.intake:
            # Use embedded intake object
            intake_doc = payload.intake.model_dump(exclude_none=True)
            intake_source = "embedded"
            logger.info("Using embedded intake form data")
        elif payload.intake_id:
            # Fetch specific intake by ID
            intake_doc = await fetch_if_needed(None, payload.intake_id, COLL_INTAKE)
            intake_source = "id"
            logger.info(f"Fetched intake form with ID: {payload.intake_id}")
        elif payload.use_latest_intake:
            # Fetch latest intake form
            intake_doc = await fetch_latest_document(COLL_INTAKE)
            if intake_doc:
                intake_source = "latest"
                logger.info(f"Using latest intake form with ID: {intake_doc.get('_id')}")
            else:
                logger.warning("No intake forms found in database (use_latest_intake=True)")
        
        # Handle follow-up form
        follow_doc = None
        followup_source = None  # Track source: "embedded", "id", "latest", or None
        if payload.followup:
            # Use embedded followup object
            follow_doc = payload.followup.model_dump(exclude_none=True)
            followup_source = "embedded"
            logger.info("Using embedded follow-up form data")
        elif payload.followup_id:
            # Fetch specific followup by ID
            follow_doc = await fetch_if_needed(None, payload.followup_id, COLL_FOLLOWUP)
            followup_source = "id"
            logger.info(f"Fetched follow-up form with ID: {payload.followup_id}")
        elif payload.use_latest_followup:
            # Fetch latest follow-up form
            follow_doc = await fetch_latest_document(COLL_FOLLOWUP)
            if follow_doc:
                followup_source = "latest"
                logger.info(f"Using latest follow-up form with ID: {follow_doc.get('_id')}")
            else:
                logger.warning("No follow-up forms found in database (use_latest_followup=True)")
        
        # Handle SOAP note (no automatic latest fetching - user provides this)
        soap_doc = await fetch_if_needed(payload.soap, payload.soap_id, COLL_SOAP)
        soap_source = "embedded" if payload.soap else ("id" if payload.soap_id else None)
        if soap_doc:
            logger.info(f"Using SOAP note data (source: {soap_source})")
        
        # Validate that we have at least one data source
        if not (intake_doc or follow_doc or soap_doc):
            raise HTTPException(
                status_code=400,
                detail="Provide at least one of: intake/followup/soap (object), intake_id/followup_id/soap_id (Mongo IDs), or use_latest_intake/use_latest_followup (boolean flags)."
            )
        
        # Build PR-1 JSON structure
        pr1 = build_pr1_payload(intake_doc, follow_doc, soap_doc, payload.flags or {})
        
        # Prepare response with metadata about which documents were used
        response_data = {
            "status": "success",
            "pr1_values": pr1,
            "metadata": {
                "intake_used": bool(intake_doc),
                "intake_id": intake_doc.get("_id") if (intake_doc and intake_source != "embedded") else None,
                "intake_source": intake_source,
                "followup_used": bool(follow_doc),
                "followup_id": follow_doc.get("_id") if (follow_doc and followup_source != "embedded") else None,
                "followup_source": followup_source,
                "soap_used": bool(soap_doc),
                "soap_id": soap_doc.get("_id") if (soap_doc and soap_source != "embedded") else None,
                "soap_source": soap_source
            }
        }
        
        logger.info(f"✅ PR-1 payload generated successfully")
        intake_status = f"Used (source: {intake_source}" + (f", ID: {response_data['metadata']['intake_id']})" if response_data['metadata']['intake_id'] else ")") if intake_doc else "Not used"
        followup_status = f"Used (source: {followup_source}" + (f", ID: {response_data['metadata']['followup_id']})" if response_data['metadata']['followup_id'] else ")") if follow_doc else "Not used"
        soap_status = f"Used (source: {soap_source}" + (f", ID: {response_data['metadata']['soap_id']})" if response_data['metadata']['soap_id'] else ")") if soap_doc else "Not used"
        logger.info(f"   - Intake: {intake_status}")
        logger.info(f"   - Follow-up: {followup_status}")
        logger.info(f"   - SOAP: {soap_status}")
        
        return JSONResponse(response_data)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating PR-1: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate PR-1: {str(e)}"
        )


# ============================================
# PDF PROCESSING FUNCTIONS
# ============================================

async def extract_text_from_pdf(pdf_file: UploadFile) -> str:
    """Extract text content from uploaded PDF file"""
    try:
        # Read PDF file content
        pdf_content = await pdf_file.read()
        
        # Create temporary file to save PDF
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
            tmp_file.write(pdf_content)
            tmp_file_path = tmp_file.name
        
        try:
            # Extract text from PDF
            reader = PdfReader(tmp_file_path)
            text_content = []
            
            for page_num, page in enumerate(reader.pages, 1):
                page_text = page.extract_text()
                if page_text:
                    text_content.append(f"--- Page {page_num} ---\n{page_text}")
            
            extracted_text = "\n\n".join(text_content)
            logger.info(f"✅ Extracted {len(extracted_text)} characters from PDF ({len(reader.pages)} pages)")
            return extracted_text
            
        finally:
            # Clean up temporary file
            if os.path.exists(tmp_file_path):
                os.unlink(tmp_file_path)
                
    except Exception as e:
        logger.error(f"Error extracting text from PDF: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to extract text from PDF: {str(e)}"
        )


def convert_pdf_text_to_soap_json(pdf_text: str) -> Dict[str, Any]:
    """Use GPT API to convert PDF text to structured SOAPNoteForPR1 JSON format"""
    json_response = None
    
    try:
        # Validate OpenAI API key before proceeding (lazy load)
        openai_api_key = get_openai_api_key()
        if not openai_api_key:
            error_msg = "OpenAI API key not configured. Please set OPENAI_API_KEY environment variable."
            logger.error(error_msg)
            raise HTTPException(status_code=500, detail=error_msg)
        
        # Truncate PDF text if it's too long (GPT-4 has token limits)
        # Roughly 1 token = 4 characters, so 100k chars ≈ 25k tokens
        # We'll limit to ~50k characters to leave room for prompts and response
        max_text_length = 50000
        if len(pdf_text) > max_text_length:
            logger.warning(f"PDF text is {len(pdf_text)} characters, truncating to {max_text_length} characters")
            pdf_text = pdf_text[:max_text_length] + "\n\n[Text truncated due to length...]"
        
        client = create_openai_client()
        
        # Create prompt for GPT to extract structured data from PDF text
        system_prompt = """You are a medical documentation assistant specializing in extracting structured data from SOAP notes and medical records.

Your task is to analyze the provided medical document text and extract all relevant information into a structured JSON format that matches the SOAPNoteForPR1 schema.

The JSON structure should include:
- Patient information (name, DOB, date of injury, claim number, employer)
- Date of First Examination (date_of_service) - MANDATORY: Extract visit date, examination date, or date of service from the document
- Physician information (examiner, specialty, NPI, state license, contact info, practice name)
- Clinical information:
  - Subjective: chief_complaint, brief_history (patient's reported symptoms, pain level, mechanism of injury)
  - Objective: physical_exam AND objective fields - MANDATORY: Extract physical exam findings, ROM tests, swelling, tenderness, clinical observations
  - Assessment: discussion_assessment, diagnoses, disability_status
  - Plan: treatment_plan_text, current_treatments (medications with dose and frequency), outcomes_adl (functional improvements and ADL changes), adl_goal_next_visit (goals for next visit), disability_status
- Diagnoses (list of conditions with ICD-10 codes) - MANDATORY: Must correctly reflect work-related condition
- RFA items (requests for authorization - services, goods, drugs with CPT/HCPCS codes)
- Work status (work_status, restrictions, dates, medication effects) - MANDATORY: RTW status (Full Duty / Modified Duty / TTD)
- Treatment plan information - MANDATORY if applicable: Surgery, PT, injections, imaging, DME
- Current treatments and medications - Extract all medications with dose and frequency
- Outcomes ADL - Functional improvements and Activities of Daily Living (note positive/negative changes)
- ADL Goal for next visit - Goals for the next treatment period
- Disability Status - Current disability/functional status

Extract all available information from the document. If a field is not present in the document, set it to null.
For dates, normalize them to MM/DD/YYYY format.
For diagnoses, extract condition name and ICD-10 code if available.
For RFA items, extract service/good name, CPT/HCPCS codes, diagnosis codes, and justification.
For date_of_service, look for: visit date, examination date, date of service, appointment date, or similar date fields.

IMPORTANT: Always extract the 'objective' field from the SOAP note. This field contains physical exam findings, ROM tests, swelling, tenderness, and other objective clinical observations. This is MANDATORY for PR-1 generation. If the document has an "Objective" or "Physical Examination" section, extract it into the 'objective' field. You may also populate 'physical_exam' if it's a separate field, but 'objective' is the primary field that will be used.

Return ONLY valid JSON, no additional text or explanation."""

        user_prompt = f"""Extract structured medical data from the following document text and return it as JSON matching the SOAPNoteForPR1 schema.

Document text:
{pdf_text}

Return the JSON structure with all extracted fields. Use null for missing fields."""

        logger.info(f"Calling GPT API to convert PDF text to structured JSON... (text length: {len(pdf_text)} chars)")
        
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
                max_tokens=4000
            )
        except Exception as api_error:
            error_type = type(api_error).__name__
            error_msg = str(api_error)
            logger.error(f"OpenAI API error ({error_type}): {error_msg}")
            
            # Provide user-friendly error messages for common issues
            if "rate_limit" in error_msg.lower() or "RateLimitError" in error_type:
                raise HTTPException(
                    status_code=429,
                    detail="OpenAI API rate limit exceeded. Please try again later."
                )
            elif "insufficient_quota" in error_msg.lower() or "quota" in error_msg.lower():
                raise HTTPException(
                    status_code=402,
                    detail="OpenAI API quota exceeded. Please check your API billing."
                )
            elif "invalid_api_key" in error_msg.lower() or "authentication" in error_msg.lower():
                raise HTTPException(
                    status_code=401,
                    detail="OpenAI API key is invalid. Please check your OPENAI_API_KEY environment variable."
                )
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"OpenAI API error: {error_type} - {error_msg}"
                )
        
        # Check if response is valid
        if not response or not response.choices or len(response.choices) == 0:
            error_msg = "Empty response from OpenAI API"
            logger.error(error_msg)
            raise HTTPException(status_code=500, detail=error_msg)
        
        # Parse JSON response
        json_response = response.choices[0].message.content.strip()
        
        if not json_response:
            error_msg = "Empty JSON response from OpenAI API"
            logger.error(error_msg)
            raise HTTPException(status_code=500, detail=error_msg)
        
        try:
            soap_data = json.loads(json_response)
        except json.JSONDecodeError as json_err:
            logger.error(f"Failed to parse JSON from GPT response: {json_err}")
            logger.error(f"GPT response (first 500 chars): {json_response[:500]}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to parse JSON response from GPT API: {str(json_err)}. Response preview: {json_response[:200]}"
            )
        
        logger.info("✅ Successfully converted PDF text to structured SOAP JSON")
        return soap_data
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        logger.error(f"Response that failed to parse: {json_response[:500] if json_response else 'None'}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to parse JSON response: {str(e)}"
        )
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e) if str(e) else f"{error_type} occurred"
        logger.error(f"Error converting PDF text to SOAP JSON ({error_type}): {error_msg}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to convert PDF to structured data: {error_msg}"
        )


@router.post("/pr1/generate-from-soap")
async def generate_pr1_from_soap(
    soap_id: str = Form(..., description="SOAP note MongoDB ID"),
    use_latest_intake: bool = Form(False, description="Use latest intake form"),
    use_latest_followup: bool = Form(False, description="Use latest follow-up form"),
    flags: Optional[str] = Form(None, description="JSON string with PR-1 flags (e.g., {'progress_report': true})")
):
    """
    Generate PR-1 form from SOAP note (using formatted_soap_note field) and latest intake/follow-up data
    
    **Workflow:**
    1. Fetch SOAP note from MongoDB using soap_id
    2. Check for existing structured fields (subjective, objective, assessment, plan) in SOAP document
    3. Extract formatted_soap_note field from the SOAP note
    4. Use GPT API to convert formatted SOAP note text to structured SOAP JSON (if formatted_soap_note exists)
    5. Merge existing structured fields with GPT-extracted data (existing fields take priority)
    6. Automatically fetch latest intake and/or follow-up forms if requested
    7. Extract ALL data from SOAP dictation (subjective, objective, assessment, plan)
    8. Combine SOAP dictation with intake form vitals (Section I) for objective findings
    9. Generate complete PR-1 form structure
    
    **Key Features:**
    - Extracts ALL available SOAP dictation data (both structured fields and GPT-extracted)
    - Merges existing structured fields with GPT-extracted data (prioritizes existing fields)
    - Combines SOAP objective findings with intake form vitals (per data mapping requirements)
    - Comprehensive logging shows what data was extracted and from which sources
    
    **Parameters:**
    - soap_id: MongoDB ObjectId string of the SOAP note (required)
    - use_latest_intake: Boolean - If True, fetches latest intake form (default: False)
    - use_latest_followup: Boolean - If True, fetches latest follow-up form (default: False)
    - flags: Optional JSON string with PR-1 checkbox flags (e.g., '{"progress_report": true, "request_for_authorization": true}')
    
    **Returns:**
    - status: Success status
    - pr1_values: Complete PR-1 form data structure
    - metadata: Information about which documents were used
    - soap_data: Extracted SOAP data (merged from existing structured fields + GPT-extracted data)
    
    **Example Request (multipart/form-data):**
    ```
    soap_id: 507f1f77bcf86cd799439011
    use_latest_intake: true
    use_latest_followup: true
    flags: {"progress_report": true, "request_for_authorization": true}
    ```
    
    **Note:** This endpoint will extract ALL available data from SOAP dictation:
    - Subjective findings from 'subjective', 'chief_complaint', or 'brief_history' fields
    - Objective findings from 'objective' and 'physical_exam' fields (combined with intake vitals)
    - Assessment from 'assessment' and 'discussion_assessment' fields
    - Treatment plan from 'plan' and 'treatment_plan_text' fields
    """
    try:
        # Step 1: Validate OpenAI API key is configured (lazy load)
        openai_api_key = get_openai_api_key()
        if not openai_api_key:
            raise HTTPException(
                status_code=500,
                detail="OpenAI API key not configured. Please set OPENAI_API_KEY environment variable to use SOAP note processing."
            )
        
        # Step 2: Fetch SOAP note from MongoDB
        db = get_database()
        if db is None:
            raise HTTPException(
                status_code=503,
                detail="Database connection not available"
            )
        
        try:
            soap_doc = await db[COLL_SOAP].find_one({"_id": ObjectId(soap_id)})
            if not soap_doc:
                raise HTTPException(
                    status_code=404,
                    detail=f"SOAP note not found with ID: {soap_id}"
                )
        except Exception as e:
            if isinstance(e, HTTPException):
                raise
            logger.error(f"Error fetching SOAP note: {e}")
            raise HTTPException(
                status_code=400,
                detail=f"Invalid SOAP note ID: {str(e)}"
            )
        
        logger.info(f"Fetched SOAP note with ID: {soap_id}")
        
        # Step 3: Check for existing structured fields in SOAP document
        # These are the standard SOAP fields that might already exist in MongoDB
        existing_structured_fields = {}
        structured_fields_found = []
        
        # Check for subjective field - handle both string and non-empty cases
        subjective_value = soap_doc.get("subjective")
        if subjective_value and (isinstance(subjective_value, str) and subjective_value.strip() or not isinstance(subjective_value, str)):
            existing_structured_fields["subjective"] = subjective_value
            structured_fields_found.append("subjective")
            logger.info(f"Found existing 'subjective' field: {len(str(subjective_value))} characters")
        if soap_doc.get("objective"):
            existing_structured_fields["objective"] = soap_doc.get("objective")
            structured_fields_found.append("objective")
        if soap_doc.get("assessment"):
            existing_structured_fields["assessment"] = soap_doc.get("assessment")
            structured_fields_found.append("assessment")
        if soap_doc.get("plan"):
            existing_structured_fields["plan"] = soap_doc.get("plan")
            structured_fields_found.append("plan")
        if soap_doc.get("date_of_service"):
            existing_structured_fields["date_of_service"] = soap_doc.get("date_of_service")
            structured_fields_found.append("date_of_service")
        
        if structured_fields_found:
            logger.info(f"Found existing structured SOAP fields: {', '.join(structured_fields_found)}")
        
        # Step 4: Extract formatted_soap_note field for GPT conversion
        formatted_soap_note = soap_doc.get("formatted_soap_note")
        if not formatted_soap_note:
            # If no formatted_soap_note but we have structured fields, use those
            if existing_structured_fields:
                logger.info("No formatted_soap_note found, but using existing structured fields")
                soap_data_dict = existing_structured_fields.copy()
                # Copy other fields from SOAP doc that might be useful
                for key in ["patient_info", "examiner", "specialty", "npi", "state_license", 
                           "contact_phone", "contact_fax", "contact_email", "practice_name",
                           "diagnoses", "rfa_items", "work_status", "restrictions"]:
                    if soap_doc.get(key):
                        soap_data_dict[key] = soap_doc.get(key)
            else:
                raise HTTPException(
                    status_code=400,
                    detail="SOAP note does not have a 'formatted_soap_note' field and no structured fields found. Please ensure the SOAP note was generated with formatted content."
                )
        else:
            if len(formatted_soap_note.strip()) < 50:
                raise HTTPException(
                    status_code=400,
                    detail="formatted_soap_note appears to be empty or too short. Please ensure the SOAP note contains sufficient content."
                )
            
            logger.info(f"Extracted formatted_soap_note with {len(formatted_soap_note)} characters")
            
            # Step 5: Convert formatted SOAP note text to structured SOAP JSON using GPT API
            try:
                gpt_extracted_data = convert_pdf_text_to_soap_json(formatted_soap_note)
                logger.info("✅ Successfully extracted structured data from formatted_soap_note using GPT")
            except HTTPException:
                raise
            except Exception as e:
                error_msg = f"Failed to convert formatted SOAP note to structured data: {str(e)}"
                logger.error(error_msg)
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                raise HTTPException(
                    status_code=500,
                    detail=error_msg
                )
        
            # Step 6: Merge existing structured fields with GPT-extracted data
            # Priority: Existing structured fields > GPT-extracted fields
            # This ensures we use the most accurate data source
            soap_data_dict = {}
            
            # First, add GPT-extracted data
            soap_data_dict.update(gpt_extracted_data)
            logger.info(f"Added GPT-extracted data with keys: {list(gpt_extracted_data.keys())}")
            
            # Then, override with existing structured fields (these are more reliable)
            # CRITICAL: Always use existing structured fields if they exist (they're from SOAP generation)
            for key, value in existing_structured_fields.items():
                if value:  # Only override if existing value is not empty
                    soap_data_dict[key] = value
                    logger.info(f"✓ Using existing structured field '{key}' instead of GPT-extracted value")
                else:
                    logger.info(f"  Existing field '{key}' is empty, keeping GPT-extracted value if available")
            
            # Also copy other useful fields from original SOAP doc (if not already present)
            # This includes subjective-related fields that might be in the original doc
            additional_fields = ["patient_info", "examiner", "specialty", "npi", "state_license", 
                       "contact_phone", "contact_fax", "contact_email", "practice_name",
                       "diagnoses", "rfa_items", "work_status", "restrictions",
                       "return_full_duty_date", "return_modified_duty_date", "mmi_date",
                       "next_visit_date", "discharged_date", "meds_affect_alertness",
                       "meds_effect_description", "restrictions_duration", "claim_number",
                       "employer", "dob", "patient_name", "date_of_injury",
                       "primary_treating_physician", "reason_for_visit", "transcription",
                       "corrected_transcription"]
            
            for key in additional_fields:
                if soap_doc.get(key) and not soap_data_dict.get(key):
                    soap_data_dict[key] = soap_doc.get(key)
                    logger.info(f"  Added field '{key}' from original SOAP document")
            
            # CRITICAL: Ensure subjective field is always included from original SOAP doc if it exists
            # This is the most reliable source for chief complaint
            if soap_doc.get("subjective") and not soap_data_dict.get("subjective"):
                soap_data_dict["subjective"] = soap_doc.get("subjective")
                logger.info("✓ CRITICAL: Added 'subjective' field from original SOAP document (most reliable source)")
            elif soap_doc.get("subjective"):
                # Even if GPT extracted subjective, prefer the original one
                soap_data_dict["subjective"] = soap_doc.get("subjective")
                logger.info("✓ CRITICAL: Overrode GPT-extracted 'subjective' with original SOAP document value")
            
            logger.info(f"Merged SOAP data: {len(existing_structured_fields)} existing fields + GPT-extracted data")
            logger.info(f"Final merged data has 'subjective': {'✓' if soap_data_dict.get('subjective') else '✗'}")
        
        # Step 7: Parse flags if provided
        pr1_flags = None
        if flags:
            try:
                pr1_flags = json.loads(flags)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in flags parameter: {flags}, error: {e}")
                pr1_flags = None
        
        # Step 8: Fetch latest intake and follow-up data if requested
        intake_doc = None
        intake_source = None
        if use_latest_intake:
            intake_doc = await fetch_latest_document(COLL_INTAKE)
            if intake_doc:
                intake_source = "latest"
                logger.info(f"Using latest intake form with ID: {intake_doc.get('_id')}")
            else:
                logger.warning("No intake forms found in database (use_latest_intake=True)")
        
        follow_doc = None
        followup_source = None
        if use_latest_followup:
            follow_doc = await fetch_latest_document(COLL_FOLLOWUP)
            if follow_doc:
                followup_source = "latest"
                logger.info(f"Using latest follow-up form with ID: {follow_doc.get('_id')}")
            else:
                logger.warning("No follow-up forms found in database (use_latest_followup=True)")
        
        # Step 9: Log extracted SOAP data structure for debugging
        logger.info(f"Final SOAP data structure keys: {list(soap_data_dict.keys())}")
        logger.info(f"SOAP data extraction summary:")
        logger.info(f"  - Subjective: {'✓' if soap_data_dict.get('subjective') or soap_data_dict.get('chief_complaint') or soap_data_dict.get('brief_history') else '✗'}")
        logger.info(f"  - Objective: {'✓' if soap_data_dict.get('objective') or soap_data_dict.get('physical_exam') else '✗'}")
        logger.info(f"  - Assessment: {'✓' if soap_data_dict.get('assessment') or soap_data_dict.get('discussion_assessment') else '✗'}")
        logger.info(f"  - Plan: {'✓' if soap_data_dict.get('plan') or soap_data_dict.get('treatment_plan_text') else '✗'}")
        logger.info(f"  - Diagnoses: {'✓' if soap_data_dict.get('diagnoses') else '✗'}")
        logger.info(f"  - Date of Service: {'✓' if soap_data_dict.get('date_of_service') else '✗'}")
        
        # Step 10: Build PR-1 payload using extracted SOAP data
        try:
            pr1 = build_pr1_payload(intake_doc, follow_doc, soap_data_dict, pr1_flags or {})
        except Exception as e:
            error_msg = f"Failed to build PR-1 payload: {str(e)}"
            logger.error(error_msg)
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise HTTPException(
                status_code=500,
                detail=error_msg
            )
        
        # Step 11: Prepare response
        response_data = {
            "status": "success",
            "pr1_values": pr1,
            "metadata": {
                "intake_used": bool(intake_doc),
                "intake_id": intake_doc.get("_id") if intake_doc else None,
                "intake_source": intake_source,
                "followup_used": bool(follow_doc),
                "followup_id": follow_doc.get("_id") if follow_doc else None,
                "followup_source": followup_source,
                "soap_used": True,
                "soap_source": "soap_note_id",
                "soap_id": soap_id
            },
            "soap_data": soap_data_dict
        }
        
        logger.info(f"✅ PR-1 payload generated successfully from SOAP note")
        logger.info(f"   - Intake: {'Used (source: latest)' if intake_doc else 'Not used'}")
        logger.info(f"   - Follow-up: {'Used (source: latest)' if follow_doc else 'Not used'}")
        logger.info(f"   - SOAP: Used (source: SOAP note ID: {soap_id})")
        
        return JSONResponse(response_data)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating PR-1 from SOAP note: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate PR-1 from SOAP note: {str(e)}"
        )

