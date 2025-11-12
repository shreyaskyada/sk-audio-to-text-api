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
    """Extract primary, secondary, and additional diagnoses from SOAP document"""
    dxs = (soap_doc or {}).get("diagnoses") or []
    
    # Handle both list of dicts and list of objects
    diagnoses = []
    for dx in dxs:
        if isinstance(dx, dict):
            diagnoses.append(dx)
        else:
            # If it's a Pydantic model, convert to dict
            diagnoses.append(dx.model_dump(exclude_none=True) if hasattr(dx, 'model_dump') else dx)
    
    primary = diagnoses[0] if len(diagnoses) > 0 else None
    secondary = diagnoses[1] if len(diagnoses) > 1 else None
    additional = diagnoses[2:] if len(diagnoses) > 2 else []
    
    # Normalize to dicts
    def norm(d):
        if not d:
            return None
        if isinstance(d, dict):
            return {
                "condition": d.get("condition"),
                "icd10": d.get("icd10"),
                "notes": d.get("notes")
            }
        return d
    
    return norm(primary), norm(secondary), [norm(d) for d in additional if d]


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
    """Build Section A: Request for Authorization (RFA)"""
    items: List[Dict[str, Any]] = []
    drugs: List[Dict[str, Any]] = []
    
    rfa_items = (soap_doc or {}).get("rfa_items") or []
    
    for it in rfa_items:
        # Handle both dict and Pydantic model
        if hasattr(it, 'model_dump'):
            it = it.model_dump(exclude_none=True)
        elif not isinstance(it, dict):
            continue
        
        base = {
            "diagnosis": it.get("diagnosis_icd10"),
            "service_or_good": it.get("service_or_good"),
            "cpt_hcpcs": it.get("cpt_or_hcpcs"),
            "mtus_consistent": it.get("mtus_consistent"),
            "justification": it.get("justification")
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
    """Extract Objective Findings from intake form Section I (ClinicalInputs)"""
    if not intake_doc:
        return None
    
    section_i = intake_doc.get("section_i") or {}  # ClinicalInputs
    
    if not section_i:
        return None
    
    findings_parts = []
    
    # Vital signs
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
    """Build Section B: Evaluation and Management"""
    p, s, addl = primary_secondary_dx(soap_doc)
    
    # Extract data from SOAP document
    # Try PR-1 specific fields first, then fall back to existing SOAP note structure
    doc = soap_doc or {}
    
    # For chief complaint/HPI - Combine SOAP data with intake form Sections G+H
    # Priority: SOAP data (if exists) > Intake form Sections G+H > Subjective section
    chief_complaint_parts = []
    
    # Get HPI from SOAP first
    soap_chief_complaint = doc.get("chief_complaint") or doc.get("brief_history")
    if soap_chief_complaint:
        chief_complaint_parts.append(soap_chief_complaint)
    
    # Get HPI from intake form (Sections G + H) - always include if available
    if intake_doc:
        hpi_from_intake = extract_hpi_from_intake(intake_doc)
        if hpi_from_intake:
            chief_complaint_parts.append(hpi_from_intake)
            logger.info("Including HPI from intake form (Sections G + H)")
    
    # If still not found, try to extract from subjective section
    if not chief_complaint_parts and doc.get("subjective"):
        # Try to extract from subjective section if it's a string
        subjective = doc.get("subjective", "")
        if isinstance(subjective, str):
            # Use first few sentences as chief complaint
            chief_complaint_parts.append(subjective.split('.')[0] if subjective else "")
    
    # Combine all HPI sources
    chief_complaint = " | ".join(filter(None, chief_complaint_parts)) if chief_complaint_parts else None
    
    # For physical exam/objective findings - Combine SOAP data with intake form Section I
    # Priority: SOAP data (if exists) > Intake form Section I > Objective section
    physical_exam_parts = []
    
    # Get objective findings from SOAP first
    soap_physical_exam = doc.get("physical_exam")
    if soap_physical_exam:
        physical_exam_parts.append(soap_physical_exam)
    
    # Get objective findings from intake form (Section I) - always include if available
    if intake_doc:
        objective_from_intake = extract_objective_findings_from_intake(intake_doc)
        if objective_from_intake:
            physical_exam_parts.append(objective_from_intake)
            logger.info("Including Objective Findings from intake form (Section I)")
    
    # If still not found, try to use objective section from SOAP
    if not physical_exam_parts and doc.get("objective"):
        physical_exam_parts.append(doc.get("objective"))
    
    # Combine all objective findings sources
    physical_exam = " | ".join(filter(None, physical_exam_parts)) if physical_exam_parts else None
    
    # For treatment plan - try PR-1 field, then use plan section
    treatment_plan = doc.get("treatment_plan_text")
    if not treatment_plan and doc.get("plan"):
        treatment_plan = doc.get("plan")
    
    # For assessment/discussion - try PR-1 field, then use assessment section
    discussion_assessment = doc.get("discussion_assessment")
    if not discussion_assessment and doc.get("assessment"):
        discussion_assessment = doc.get("assessment")
    
    return {
        "diagnoses": {
            "primary": p,
            "secondary": s,
            "additional": addl
        },
        "chief_complaint_and_history": chief_complaint,
        "physical_exam": physical_exam,
        "current_treatment_and_meds": doc.get("current_treatments_and_meds"),
        "outcomes_adl": doc.get("outcomes_adl"),
        "adl_goal_next_visit": doc.get("adl_goal_next_visit"),
        "disability_status": doc.get("disability_status"),
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
    """Build Section C: Work Status"""
    b = (follow_doc or {}).get("section_b") or {}
    
    # Prefer SOAP explicit fields if present (e.g., dates), then follow-up selections
    return {
        "instruction": (soap_doc or {}).get("work_status") or b.get("work_status_perception") or "[Not documented]",
        "return_full_duty_date": to_mmddyyyy((soap_doc or {}).get("return_full_duty_date")),
        "unable_to_work_from": None,  # optional: populate if you track ranges
        "unable_to_work_to": None,
        "restrictions_text": (soap_doc or {}).get("restrictions"),
        "restrictions_duration": (soap_doc or {}).get("restrictions_duration"),
        "meds_affect_alertness": (soap_doc or {}).get("meds_affect_alertness"),
        "meds_effect_description": (soap_doc or {}).get("meds_effect_description"),
        "anticipate_full_duty_date": to_mmddyyyy((soap_doc or {}).get("return_full_duty_date")),
        "anticipate_modified_duty_date": to_mmddyyyy((soap_doc or {}).get("return_modified_duty_date")),
        "anticipate_mmi_date": to_mmddyyyy((soap_doc or {}).get("mmi_date")),
        "next_visit_date": to_mmddyyyy((soap_doc or {}).get("next_visit_date")),
        "discharged_from_care_date": to_mmddyyyy((soap_doc or {}).get("discharged_date"))
    }


def normalize_pr1_header(
    intake_doc: Optional[Dict[str, Any]],
    soap_doc: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Build PR-1 header information from intake and SOAP documents"""
    patient_name = pick_name(intake_doc, soap_doc)
    dob = pick_dob(intake_doc, soap_doc)
    doi = pick_doi(intake_doc, soap_doc)
    soap_doc = soap_doc or {}
    intake_doc = intake_doc or {}
    
    # Get claim number from SOAP or intake
    claim_number = soap_doc.get("claim_number") or ((intake_doc.get("section_a") or {}).get("claim_number"))
    # Also check intake section_c for claim number
    if not claim_number:
        claim_number = (intake_doc.get("section_c") or {}).get("claim_number")
    
    employer = pick_employer(intake_doc) or soap_doc.get("employer")
    
    return {
        "patient_name": str_or_nd(patient_name),
        "date_of_injury": str_or_nd(doi),
        "date_of_birth": str_or_nd(dob),
        "claim_number": str_or_nd(claim_number),
        "employer": str_or_nd(employer),
        "physician": {
            "physician_name": str_or_nd(soap_doc.get("examiner")),
            "practice_name": soap_doc.get("practice_name"),
            "contact_name": None,
            "address": None,
            "city": None,
            "state": None,
            "zip": None,
            "telephone": soap_doc.get("contact_phone"),
            "fax": soap_doc.get("contact_fax"),
            "email": soap_doc.get("contact_email"),
            "specialty": soap_doc.get("specialty"),
            "state_license_number": soap_doc.get("state_license"),
            "npi_number": soap_doc.get("npi"),
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
- Physician information (examiner, specialty, NPI, state license, contact info, practice name)
- Clinical information (chief complaint, history, physical exam, current treatments, outcomes, disability status)
- Diagnoses (list of conditions with ICD-10 codes)
- RFA items (requests for authorization - services, goods, drugs with CPT/HCPCS codes)
- Work status (work status, restrictions, dates, medication effects)
- Treatment plan information

Extract all available information from the document. If a field is not present in the document, set it to null.
For dates, normalize them to MM/DD/YYYY format.
For diagnoses, extract condition name and ICD-10 code if available.
For RFA items, extract service/good name, CPT/HCPCS codes, diagnosis codes, and justification.

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


@router.post("/pr1/generate-from-pdf")
async def generate_pr1_from_pdf(
    pdf_file: UploadFile = File(..., description="SOAP note PDF file"),
    use_latest_intake: bool = Form(False, description="Use latest intake form"),
    use_latest_followup: bool = Form(False, description="Use latest follow-up form"),
    flags: Optional[str] = Form(None, description="JSON string with PR-1 flags (e.g., {'progress_report': true})")
):
    """
    Generate PR-1 form from PDF SOAP note and latest intake/follow-up data
    
    **Workflow:**
    1. Upload SOAP note PDF file
    2. Extract text from PDF
    3. Use GPT API to convert PDF text to structured SOAP JSON
    4. Automatically fetch latest intake and/or follow-up forms if requested
    5. Generate complete PR-1 form structure
    
    **Parameters:**
    - pdf_file: SOAP note PDF file (required)
    - use_latest_intake: Boolean - If True, fetches latest intake form (default: False)
    - use_latest_followup: Boolean - If True, fetches latest follow-up form (default: False)
    - flags: Optional JSON string with PR-1 checkbox flags (e.g., '{"progress_report": true, "request_for_authorization": true}')
    
    **Returns:**
    - status: Success status
    - pr1_values: Complete PR-1 form data structure
    - metadata: Information about which documents were used
    - soap_data: Extracted SOAP data from PDF
    
    **Example Request (multipart/form-data):**
    ```
    pdf_file: [PDF file]
    use_latest_intake: true
    use_latest_followup: true
    flags: {"progress_report": true, "request_for_authorization": true}
    ```
    """
    try:
        # Validate PDF file
        if not pdf_file.filename or not pdf_file.filename.lower().endswith('.pdf'):
            raise HTTPException(
                status_code=400,
                detail="File must be a PDF (.pdf)"
            )
        
        logger.info(f"Processing PDF file: {pdf_file.filename}")
        
        # Step 1: Validate OpenAI API key is configured (lazy load)
        openai_api_key = get_openai_api_key()
        if not openai_api_key:
            raise HTTPException(
                status_code=500,
                detail="OpenAI API key not configured. Please set OPENAI_API_KEY environment variable to use PDF processing."
            )
        
        # Step 2: Extract text from PDF
        pdf_text = await extract_text_from_pdf(pdf_file)
        
        if not pdf_text or len(pdf_text.strip()) < 50:
            raise HTTPException(
                status_code=400,
                detail="PDF appears to be empty or could not extract sufficient text. Please ensure the PDF contains readable text."
            )
        
        logger.info(f"Extracted {len(pdf_text)} characters from PDF")
        
        # Step 3: Convert PDF text to structured SOAP JSON using GPT API
        try:
            soap_data_dict = convert_pdf_text_to_soap_json(pdf_text)
        except HTTPException:
            # Re-raise HTTP exceptions from convert function
            raise
        except Exception as e:
            error_msg = f"Failed to convert PDF to structured data: {str(e)}"
            logger.error(error_msg)
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise HTTPException(
                status_code=500,
                detail=error_msg
            )
        
        # Step 4: Parse flags if provided
        pr1_flags = None
        if flags:
            try:
                pr1_flags = json.loads(flags)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in flags parameter: {flags}, error: {e}")
                pr1_flags = None
        
        # Step 5: Fetch latest intake and follow-up data if requested
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
        
        # Step 6: Build PR-1 payload using extracted SOAP data
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
        
        # Step 7: Prepare response
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
                "soap_source": "pdf_upload",
                "pdf_filename": pdf_file.filename
            },
            "soap_data": soap_data_dict  # Include extracted SOAP data for reference
        }
        
        logger.info(f"✅ PR-1 payload generated successfully from PDF")
        logger.info(f"   - Intake: {'Used (source: latest)' if intake_doc else 'Not used'}")
        logger.info(f"   - Follow-up: {'Used (source: latest)' if follow_doc else 'Not used'}")
        logger.info(f"   - SOAP: Used (source: PDF upload)")
        
        return JSONResponse(response_data)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating PR-1 from PDF: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate PR-1 from PDF: {str(e)}"
        )

