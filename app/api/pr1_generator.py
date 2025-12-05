"""
PR-1 Generator API endpoints
Generates PR-1 form data structure from intake, follow-up, and SOAP note data
"""
import logging
import os
import json
import tempfile
import re
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

# OpenAI Model Configuration - Latest ChatGPT model for PR1 generation
OPENAI_MODEL = 'gpt-5.1'  # Latest GPT-5.1 model


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


def generate_supportive_cpts_for_request(primary_cpt: str, service_description: str, openai_client=None) -> List[str]:
    """
    Generate supportive CPT codes for a request based on primary CPT and service description.
    Returns a list of supportive CPT codes, or empty list if unable to generate.
    """
    if not primary_cpt or not primary_cpt.strip():
        return []
    
    if not openai_client:
        try:
            openai_client = create_openai_client()
        except Exception:
            logger.warning("Could not create OpenAI client for supportive CPT generation")
            return []
    
    try:
        from app.cpt_mappings import generate_cpt_with_ai
        
        # Build procedure description from service and CPT
        procedure_description = f"{service_description} (CPT: {primary_cpt})"
        
        # Use the existing AI function to generate CPTs
        # It will return both primary and supportive, but we only need supportive
        _, supportive_cpts = generate_cpt_with_ai(procedure_description, openai_client)
        
        if supportive_cpts and isinstance(supportive_cpts, list):
            # Filter out empty strings and normalize
            return [str(code).strip() for code in supportive_cpts if code and str(code).strip()]
        
        return []
    except Exception as e:
        logger.warning(f"Error generating supportive CPTs for CPT {primary_cpt}: {e}")
        return []


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


def to_yyyy_mm_dd(s: Optional[str]) -> Optional[str]:
    """Convert date string to YYYY-MM-DD format (ISO 8601)"""
    if not s:
        return None
    
    # Accept common formats and normalize to YYYY-MM-DD
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
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
            condition_val = dx.get("condition") or ""
            condition = str(condition_val).strip().lower() if condition_val else ""
            icd10_val = dx.get("icd10") or ""
            icd10 = str(icd10_val).strip().lower() if icd10_val else ""
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


def build_section_a_rfa(soap_doc: Optional[Dict[str, Any]], intake_doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
    
    # Extract from formatted_soap_note Plan section or transcription if not found in structured fields
    if not rfa_items:
        doc = soap_doc or {}
        formatted_soap = doc.get("formatted_soap_note")
        transcription_text = doc.get("transcription") or doc.get("corrected_transcription")
        
        # First try to extract Plan section from formatted_soap_note
        if formatted_soap and isinstance(formatted_soap, str):
            # Look for Plan section specifically
            plan_match = re.search(r'## P – PLAN\s*\n(.*?)(?=---|$)', formatted_soap, re.IGNORECASE | re.DOTALL)
            if plan_match:
                plan_group = plan_match.group(1)
                plan_text = plan_group.strip() if plan_group else ""
                if plan_text:
                    logger.info("Attempting GPT extraction of RFA items from Plan section...")
                    extracted_rfa = extract_rfa_items_from_text(plan_text)
                if extracted_rfa:
                    rfa_items = extracted_rfa
                    logger.info(f"✓ Found {len(rfa_items)} RFA items from Plan section GPT extraction")
            
            # If not found in Plan section, try entire formatted_soap_note
            if not rfa_items:
                logger.info("Attempting GPT extraction of RFA items from formatted_soap_note...")
                extracted_rfa = extract_rfa_items_from_text(formatted_soap)
                if extracted_rfa:
                    rfa_items = extracted_rfa
                    logger.info(f"✓ Found {len(rfa_items)} RFA items from formatted_soap_note GPT extraction")
        
        # Try transcription if still not found
        if not rfa_items and transcription_text and isinstance(transcription_text, str):
            logger.info("Attempting GPT extraction of RFA items from transcription...")
            extracted_rfa = extract_rfa_items_from_text(transcription_text)
            if extracted_rfa:
                rfa_items = extracted_rfa
                logger.info(f"✓ Found {len(rfa_items)} RFA items from transcription GPT extraction")
    
    # Extract patient name using pick_name helper
    doc = soap_doc or {}
    patient_name = pick_name(intake_doc, soap_doc) or ""
    
    # Extract general request text (if available)
    doc = soap_doc or {}
    general_request_text = doc.get("general_request_text") or doc.get("generalRequestText") or ""
    
    # Build requests array in the exact format required
    requests = []
    
    for it in rfa_items:
        # Handle both dict and Pydantic model
        if hasattr(it, 'model_dump'):
            it = it.model_dump(exclude_none=True)
        elif not isinstance(it, dict):
            continue
        
        # Determine type: "treatment" or "drug"
        # Check for type field first, then is_drug
        request_type = it.get("type", "").lower()
        is_drug = request_type == "drug" or it.get("is_drug") or False
        
        if is_drug:
            # Drug request format - use exact field names
            drug_name = (
                it.get("drug") or
                it.get("drug_name") or
                ""
            )
            diagnosis = (
                it.get("diagnosis") or 
                it.get("diagnosis_name") or
                ""
            )
            diagnosis_code = (
                it.get("diagnosisCode") or
                it.get("diagnosis_code") or
                it.get("diagnosis_icd10") or 
                it.get("diagnosis_codes") or
                it.get("icd10") or
                it.get("icd10_code") or
                ""
            )
            dose_form = (
                it.get("doseForm") or
                it.get("dose_form") or
                ""
            )
            quantity = (
                it.get("quantity") or
                it.get("length_or_qty") or
                it.get("qty") or
                ""
            )
            
            if drug_name:  # Only add if drug name exists
                requests.append({
                    "type": "drug",
                    "diagnosis": diagnosis,
                    "diagnosisCode": diagnosis_code,
                    "drug": drug_name,
                    "doseForm": dose_form,
                    "quantity": quantity
                })
        else:
            # Treatment request format - use exact field names
            service_requested = (
                it.get("serviceRequested") or
                it.get("service_requested") or
                it.get("service_or_good") or 
                it.get("service_good_name") or 
                it.get("service_or_good_name") or
                it.get("service") or
                it.get("good") or
                ""
            )
            diagnosis = (
                it.get("diagnosis") or 
                it.get("diagnosis_name") or
                ""
            )
            diagnosis_code = (
                it.get("diagnosisCode") or
                it.get("diagnosis_code") or
                it.get("diagnosis_icd10") or 
                it.get("diagnosis_codes") or
                it.get("icd10") or
                it.get("icd10_code") or
                ""
            )
            cpt = (
                it.get("cpt") or
                it.get("cpt_or_hcpcs") or 
                it.get("CPT_HCPCS_codes") or
                it.get("cpt_hcpcs") or
                it.get("hcpcs") or
                ""
            )
            # Extract supportive CPTs - handle both array and string formats
            supportive_cpts_raw = (
                it.get("supportiveCpts") or
                it.get("supportive_cpts") or
                it.get("supportiveCPTs") or
                it.get("supportiveCPTs") or
                []
            )
            # Normalize supportive CPTs to a list
            if isinstance(supportive_cpts_raw, str):
                # If it's a comma-separated string, split it
                supportive_cpts = [code.strip() for code in supportive_cpts_raw.split(",") if code and isinstance(code, str) and code.strip()]
            elif isinstance(supportive_cpts_raw, list):
                supportive_cpts = [str(code).strip() for code in supportive_cpts_raw if code and str(code).strip()]
            else:
                supportive_cpts = []
            
            frequency_duration = (
                it.get("frequencyDuration") or
                it.get("frequency_duration") or
                it.get("frequency") or
                it.get("duration") or
                ""
            )
            
            if service_requested:  # Only add if service requested exists
                request_item = {
                    "type": "treatment",
                    "diagnosis": diagnosis,
                    "diagnosisCode": diagnosis_code,
                    "serviceRequested": service_requested,
                    "cpt": cpt,
                    "frequencyDuration": frequency_duration
                }
                
                # Auto-fetch supportive CPTs if we have a primary CPT but no supportive CPTs
                if cpt and cpt.strip() and (not supportive_cpts or len(supportive_cpts) == 0):
                    try:
                        openai_client = create_openai_client()
                        generated_supportive = generate_supportive_cpts_for_request(
                            cpt, 
                            service_requested, 
                            openai_client
                        )
                        if generated_supportive:
                            supportive_cpts = generated_supportive
                            logger.info(f"RFA item '{service_requested}': Auto-generated {len(supportive_cpts)} supportive CPTs: {', '.join(supportive_cpts)}")
                    except Exception as e:
                        logger.warning(f"Could not auto-generate supportive CPTs for '{service_requested}' (CPT: {cpt}): {e}")
                
                # Always add supportive CPTs as an array (even if empty) when there's a primary CPT
                # This ensures proper array format in the PR1 form
                if cpt and cpt.strip():
                    request_item["supportiveCpts"] = supportive_cpts if supportive_cpts else []
                    if supportive_cpts:
                        logger.info(f"RFA item '{service_requested}': Using {len(supportive_cpts)} supportive CPTs: {', '.join(supportive_cpts)}")
                    else:
                        logger.debug(f"RFA item '{service_requested}': No supportive CPTs found (empty array)")
                
                requests.append(request_item)
    
    if requests:
        logger.info(f"RFA Section A: {len(requests)} requests extracted (from SOAP dictation)")
    
    return {
        "patientName": patient_name,
        "generalRequestText": general_request_text,
        "requests": requests
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
        # Preserve boolean values, convert other non-strings to strings
        if isinstance(obj, bool):
            return obj
        return obj if isinstance(obj, str) else str(obj) if obj else None
    
    # Helper function to extract nested boolean values (for checkboxes)
    def extract_nested_bool(obj, *keys):
        """Extract boolean value from nested dict structure"""
        if not obj:
            return None
        for key in keys:
            if isinstance(obj, dict):
                obj = obj.get(key)
            else:
                return None
            if obj is None:
                return None
        # Return boolean if it's a boolean, otherwise convert truthy values to True
        if isinstance(obj, bool):
            return obj
        if isinstance(obj, str):
            return obj.lower() in ('true', 'yes', '1', 'checked')
        return bool(obj) if obj else None
    
    # Helper function to try multiple extraction paths and return first non-None value
    def try_extract(*paths):
        """Try multiple extraction paths and return first non-None value (including False)"""
        for path_func in paths:
            result = path_func()
            if result is not None:
                return result
        return None
    
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
    
    # Extract from formatted_soap_note Objective section or transcription if not found in structured fields
    if not physical_exam_parts:
        formatted_soap = doc.get("formatted_soap_note")
        transcription_text = doc.get("transcription") or doc.get("corrected_transcription")
        
        # First try to extract Objective section from formatted_soap_note
        if formatted_soap and isinstance(formatted_soap, str):
            # Look for Objective section specifically
            objective_match = re.search(r'## O – OBJECTIVE\s*\n(.*?)(?=## A – ASSESSMENT|## P – PLAN|---|$)', formatted_soap, re.IGNORECASE | re.DOTALL)
            if objective_match:
                objective_group = objective_match.group(1)
                objective_text = objective_group.strip() if objective_group else ""
                if objective_text:
                    physical_exam_parts.append(objective_text)
                    logger.info("✓ Found Objective Findings from formatted_soap_note Objective section")
            
            # If not found in Objective section, try entire formatted_soap_note with GPT extraction
            if not physical_exam_parts:
                logger.info("Attempting GPT extraction of objective findings from formatted_soap_note...")
                extracted_objective = extract_objective_findings_from_text(formatted_soap)
                if extracted_objective:
                    physical_exam_parts.append(extracted_objective)
                    logger.info("✓ Found Objective Findings from formatted_soap_note GPT extraction")
        
        # Try transcription if still not found
        if not physical_exam_parts and transcription_text and isinstance(transcription_text, str):
            logger.info("Attempting GPT extraction of objective findings from transcription...")
            extracted_objective = extract_objective_findings_from_text(transcription_text)
            if extracted_objective:
                physical_exam_parts.append(extracted_objective)
                logger.info("✓ Found Objective Findings from transcription GPT extraction")
    
    # If still no objective findings found, log a warning
    if not physical_exam_parts:
        logger.warning("No objective findings found in SOAP dictation (neither 'physical_exam' nor 'objective' field present, and extraction from text failed)")
    
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
    
    # Extract from formatted_soap_note Plan section or transcription if not found in structured fields
    if not current_treatment_parts:
        formatted_soap = doc.get("formatted_soap_note")
        transcription_text = doc.get("transcription") or doc.get("corrected_transcription")
        
        # First try to extract Plan section from formatted_soap_note
        if formatted_soap and isinstance(formatted_soap, str):
            # Look for Plan section specifically
            plan_match = re.search(r'## P – PLAN\s*\n(.*?)(?=---|$)', formatted_soap, re.IGNORECASE | re.DOTALL)
            if plan_match:
                plan_group = plan_match.group(1)
                plan_text = plan_group.strip() if plan_group else ""
                if plan_text:
                    logger.info("Attempting GPT extraction of current treatments from Plan section...")
                    extracted_data = extract_treatment_and_outcomes_from_text(plan_text)
                if extracted_data and extracted_data.get("current_treatments_and_meds"):
                    current_treatment_parts.append(extracted_data.get("current_treatments_and_meds"))
                    logger.info("✓ Found Current Treatments from Plan section GPT extraction")
            
            # If not found in Plan section, try entire formatted_soap_note
            if not current_treatment_parts:
                logger.info("Attempting GPT extraction of current treatments from formatted_soap_note...")
                extracted_data = extract_treatment_and_outcomes_from_text(formatted_soap)
                if extracted_data and extracted_data.get("current_treatments_and_meds"):
                    current_treatment_parts.append(extracted_data.get("current_treatments_and_meds"))
                    logger.info("✓ Found Current Treatments from formatted_soap_note GPT extraction")
        
        # Try transcription if still not found
        if not current_treatment_parts and transcription_text and isinstance(transcription_text, str):
            logger.info("Attempting GPT extraction of current treatments from transcription...")
            extracted_data = extract_treatment_and_outcomes_from_text(transcription_text)
            if extracted_data and extracted_data.get("current_treatments_and_meds"):
                current_treatment_parts.append(extracted_data.get("current_treatments_and_meds"))
                logger.info("✓ Found Current Treatments from transcription GPT extraction")
    
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
    
    # Extract from formatted_soap_note Plan section or transcription if not found in structured fields
    if not outcomes_parts:
        formatted_soap = doc.get("formatted_soap_note")
        transcription_text = doc.get("transcription") or doc.get("corrected_transcription")
        
        # First try to extract Plan section from formatted_soap_note
        if formatted_soap and isinstance(formatted_soap, str):
            # Look for Plan section specifically
            plan_match = re.search(r'## P – PLAN\s*\n(.*?)(?=---|$)', formatted_soap, re.IGNORECASE | re.DOTALL)
            if plan_match:
                plan_group = plan_match.group(1)
                plan_text = plan_group.strip() if plan_group else ""
                if plan_text:
                    logger.info("Attempting GPT extraction of outcomes ADL from Plan section...")
                    extracted_data = extract_treatment_and_outcomes_from_text(plan_text)
                if extracted_data and extracted_data.get("outcomes_adl"):
                    outcomes_parts.append(extracted_data.get("outcomes_adl"))
                    logger.info("✓ Found Outcomes ADL from Plan section GPT extraction")
            
            # If not found in Plan section, try entire formatted_soap_note
            if not outcomes_parts:
                logger.info("Attempting GPT extraction of outcomes ADL from formatted_soap_note...")
                extracted_data = extract_treatment_and_outcomes_from_text(formatted_soap)
                if extracted_data and extracted_data.get("outcomes_adl"):
                    outcomes_parts.append(extracted_data.get("outcomes_adl"))
                    logger.info("✓ Found Outcomes ADL from formatted_soap_note GPT extraction")
        
        # Try transcription if still not found
        if not outcomes_parts and transcription_text and isinstance(transcription_text, str):
            logger.info("Attempting GPT extraction of outcomes ADL from transcription...")
            extracted_data = extract_treatment_and_outcomes_from_text(transcription_text)
            if extracted_data and extracted_data.get("outcomes_adl"):
                outcomes_parts.append(extracted_data.get("outcomes_adl"))
                logger.info("✓ Found Outcomes ADL from transcription GPT extraction")
    
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
        "Physical Examination": physical_exam,  # Also include as "Physical Examination" for compatibility
        "objective_findings": physical_exam,  # Also include as "objective_findings" for compatibility
        "current_treatment_and_meds": current_treatment_and_meds,  # Field 3: Current Treatment Plans including Medication
        "outcomes_adl": outcomes_adl,  # Field 4: Outcomes ADL
        "adl_goal_next_visit": adl_goal_next_visit,  # ADL Goal for next visit/treatment period
        "disability_status": disability_status,  # Field 5: Disability Status
        # Extract secondary physician reports - handle both flat and nested structures
        "secondary_physician_reports": try_extract(
            lambda: doc.get("secondary_physician_reports") if doc.get("secondary_physician_reports") else None,
            lambda: extract_from_nested(doc, "values", "page6", "secondaryPhysicianReports"),
            lambda: extract_from_nested(doc, "page6", "secondaryPhysicianReports"),
            lambda: extract_from_nested(doc, "secondaryPhysicianReports")
        ),
        # Discussion/Assessment - already extracted with nested support above
        "discussion_assessment": discussion_assessment,
        # Treatment plan - already extracted with nested support above
        "treatment_plan": treatment_plan,
        # Extract treatment plan checkboxes - handle both flat and nested structures
        # Try multiple paths: flat fields first, then nested structures
        "continue_same_treatment": try_extract(
            lambda: doc.get("continue_same_treatment") if doc.get("continue_same_treatment") is not None else None,
            lambda: extract_nested_bool(doc, "values", "page6", "treatmentPlan", "continueSameTreatmentPlan"),
            lambda: extract_nested_bool(doc, "treatmentPlan", "continueSameTreatmentPlan"),
            lambda: extract_nested_bool(doc, "page6", "treatmentPlan", "continueSameTreatmentPlan")
        ),
        "discharge_from_care": try_extract(
            lambda: doc.get("discharge_from_care") if doc.get("discharge_from_care") is not None else None,
            lambda: extract_nested_bool(doc, "values", "page6", "treatmentPlan", "dischargeFromCare"),
            lambda: extract_nested_bool(doc, "treatmentPlan", "dischargeFromCare"),
            lambda: extract_nested_bool(doc, "page6", "treatmentPlan", "dischargeFromCare")
        ),
        "change_in_treatment_plan": try_extract(
            lambda: doc.get("change_in_treatment_plan") if doc.get("change_in_treatment_plan") is not None else None,
            lambda: extract_nested_bool(doc, "values", "page6", "treatmentPlan", "changeInTreatmentPlan"),
            lambda: extract_nested_bool(doc, "treatmentPlan", "changeInTreatmentPlan"),
            lambda: extract_nested_bool(doc, "page6", "treatmentPlan", "changeInTreatmentPlan")
        ),
        "dispense_as_written": try_extract(
            lambda: doc.get("dispense_as_written") if doc.get("dispense_as_written") is not None else None,
            lambda: extract_nested_bool(doc, "values", "page6", "treatmentPlan", "dispensePrescriptionAsWritten"),
            lambda: extract_nested_bool(doc, "treatmentPlan", "dispensePrescriptionAsWritten"),
            lambda: extract_nested_bool(doc, "page6", "treatmentPlan", "dispensePrescriptionAsWritten")
        ),
        "comments": (
            doc.get("comments") or
            extract_from_nested(doc, "values", "page6", "comments") or
            extract_from_nested(doc, "page6", "comments")
        )
    }


def extract_objective_findings_from_text(text: str) -> Optional[str]:
    """Extract objective findings (physical examination) from transcription or SOAP text using GPT"""
    if not text or not str(text).strip():
        return None
    
    try:
        openai_api_key = get_openai_api_key()
        if not openai_api_key:
            logger.warning("OpenAI API key not available, skipping objective findings extraction")
            return None
        
        client = create_openai_client()
        
        system_prompt = """You are a medical documentation assistant specializing in extracting objective findings (physical examination) from clinical transcriptions and SOAP notes.

Your task is to analyze the provided medical text and extract ONLY the objective findings/physical examination section. This includes:
- Physical exam findings (tenderness, swelling, range of motion, etc.)
- Clinical observations
- Test results mentioned (e.g., anterior drawer test, talar tilt test)
- Neurovascular status
- Any objective clinical observations

Return ONLY the objective findings text as a string. Do not include subjective complaints, assessment, or plan sections.
If objective findings are not present in the text, return null.

Return ONLY the text content, no JSON wrapper, no additional explanation."""

        # Limit text length to avoid token limits
        text_to_analyze = str(text)[:5000] if len(str(text)) > 5000 else str(text)
        
        user_prompt = f"""Extract objective findings (physical examination) from the following medical text. Return ONLY the objective findings, not subjective complaints, assessment, or plan:

{text_to_analyze}

Return ONLY the objective findings text."""

        logger.info("Calling GPT API to extract objective findings from text...")
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            max_completion_tokens=1500
        )
        
        content = response.choices[0].message.content
        if not content:
            logger.warning("GPT API returned empty content for objective findings")
            return None
        result = content.strip()
        if result and result.lower() not in ["null", "none", "not found", "no objective findings"]:
            logger.info("Successfully extracted objective findings from text")
            return result
        else:
            logger.info("No objective findings found in text")
            return None
            
    except Exception as e:
        logger.error(f"Error extracting objective findings from text with GPT: {e}")
        return None


def extract_rfa_items_from_text(text: str) -> Optional[List[Dict[str, Any]]]:
    """Extract RFA items (treatment requests and drug requests) from transcription or SOAP text using GPT"""
    if not text or not str(text).strip():
        return None
    
    try:
        openai_api_key = get_openai_api_key()
        if not openai_api_key:
            logger.warning("OpenAI API key not available, skipping RFA items extraction")
            return None
        
        client = create_openai_client()
        
        system_prompt = """You are a medical documentation assistant specializing in extracting Request for Authorization (RFA) items from clinical transcriptions and SOAP notes.

Your task is to analyze the provided medical text and extract ALL treatment requests and drug requests that require authorization. This includes:
- Medical treatments (physical therapy, injections, imaging, surgery, DME, etc.)
- Medications/drugs prescribed
- Services or goods requested

For each RFA item, extract:
- Type: "treatment" or "drug"
- Diagnosis: The diagnosis/condition this treatment/drug is for
- ICD-10 Code: If mentioned
- Treatment Requested / Drug Requested: Name of the treatment or drug
- Primary CPT/HCPCS Code: The main procedure code (for treatments)
- Supportive CPTs: CRITICAL - Extract ALL supportive CPT/HCPCS codes that are typically required with the primary procedure. This includes:
  * Fluoroscopic guidance codes (77003, 77002, etc.) for injections
  * Ultrasound guidance codes (76942, etc.) for procedures
  * DME/Supplies codes (L-codes for braces, boots, crutches, etc.)
  * Anesthesia codes if mentioned
  * Any other supportive services, devices, or supplies mentioned
  Supportive CPTs should be an array of codes. If multiple supportive codes are mentioned or typically required, include ALL of them.
- Strength & Form: For drugs (e.g., "500mg tablet", "10mg/ml injection")
- Frequency/Duration: For treatments (e.g., "3x/week for 4 weeks", "1 session")
- Quantity: For drugs (e.g., "30 tablets", "1 vial")
- Justification: Any medical justification mentioned

Return a JSON object with an "rfa_items" array containing all extracted items. Use EXACT field names as shown:
{
  "rfa_items": [
    {
      "type": "treatment",
      "diagnosis": "Lower back pain",
      "diagnosisCode": "M54.5",
      "serviceRequested": "Epidural steroid injection",
      "cpt": "62311",
      "supportiveCpts": ["77003"],
      "frequencyDuration": "1 injection"
    },
    {
      "type": "treatment",
      "diagnosis": "Knee pain",
      "diagnosisCode": "M25.561",
      "serviceRequested": "Physical Therapy",
      "cpt": "97110",
      "supportiveCpts": [],
      "frequencyDuration": "3x/week for 6 weeks"
    },
    {
      "type": "treatment",
      "diagnosis": "Ankle fracture",
      "diagnosisCode": "S82.001A",
      "serviceRequested": "Walking boot",
      "cpt": "L4361",
      "supportiveCpts": [],
      "frequencyDuration": "1 unit"
    },
    {
      "type": "drug",
      "diagnosis": "Lower back pain",
      "diagnosisCode": "M54.5",
      "drug": "Ibuprofen",
      "doseForm": "600mg tablet",
      "quantity": "90 tablets"
    }
  ]
}

CRITICAL RULES FOR SUPPORTIVE CPTs:
1. For injection procedures, ALWAYS include fluoroscopic guidance (77003) or ultrasound guidance (76942) if the procedure typically requires it, even if not explicitly mentioned
2. For procedures involving imaging guidance, extract the guidance code as a supportive CPT
3. For DME/Supplies, if a device is mentioned (boot, brace, crutches), include the appropriate HCPCS L-code
4. Supportive CPTs should be an array: ["77003", "L4361"] or [] if none
5. Be thorough - supportive CPTs are CRITICAL for accurate billing and authorization

CRITICAL: Use EXACT field names (camelCase):
- For treatments: type="treatment", diagnosis, diagnosisCode, serviceRequested, cpt, supportiveCpts (array), frequencyDuration
- For drugs: type="drug", diagnosis, diagnosisCode, drug, doseForm, quantity
- Do NOT use snake_case or other variations

Extract ALL treatments and medications mentioned that would require authorization.
If no RFA items are found, return {"rfa_items": []}.

Return ONLY valid JSON, no additional text."""

        # Limit text length to avoid token limits
        text_to_analyze = str(text)[:5000] if len(str(text)) > 5000 else str(text)
        
        user_prompt = f"""Extract all Request for Authorization (RFA) items from the following medical text. Include ALL treatments and medications that require authorization:

{text_to_analyze}

Return a JSON object with rfa_items array."""

        logger.info("Calling GPT API to extract RFA items from text...")
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            max_completion_tokens=2000
        )
        
        result = json.loads(response.choices[0].message.content)
        
        # Extract rfa_items array from response
        rfa_items = result.get("rfa_items") or []
        
        if rfa_items and isinstance(rfa_items, list) and len(rfa_items) > 0:
            logger.info(f"Successfully extracted {len(rfa_items)} RFA items from text")
            return rfa_items
        else:
            logger.info("No RFA items found in text")
            return None
            
    except Exception as e:
        logger.error(f"Error extracting RFA items from text with GPT: {e}")
        return None


def extract_treatment_and_outcomes_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Extract current treatments/medications and outcomes ADL from transcription or SOAP text using GPT"""
    if not text or not str(text).strip():
        return None
    
    try:
        openai_api_key = get_openai_api_key()
        if not openai_api_key:
            logger.warning("OpenAI API key not available, skipping treatment/outcomes extraction")
            return None
        
        client = create_openai_client()
        
        system_prompt = """You are a medical documentation assistant specializing in extracting treatment plans and outcomes from clinical transcriptions and SOAP notes.

Your task is to analyze the provided medical text and extract:
1. Current Treatment Plans including Medications - Extract ALL medications mentioned with dose and frequency, treatments, devices provided (e.g., CAM boot, crutches), exercises, therapy, injections, imaging orders, etc. Include everything mentioned in the Plan section or treatment discussions.
2. Outcomes ADL - Extract functional improvements, changes in Activities of Daily Living, progress notes, positive/negative changes related to treatment, improvements in function, changes in pain levels, mobility improvements, etc.

Return a JSON object with:
{
  "current_treatments_and_meds": "Complete list of all current treatments and medications with doses and frequencies if mentioned. Format as a clear list or paragraph.",
  "outcomes_adl": "Functional improvements, ADL changes, progress notes, positive/negative changes related to treatment. Include any mention of improvements, worsening, or no changes."
}

EXAMPLES:
- If text mentions "treated with CAM boot and crutches", extract: "CAM boot and crutches provided"
- If text mentions "begin gentle ankle range-of-motion exercises", extract: "Gentle ankle range-of-motion exercises"
- If text mentions "weight bearing is allowed as tolerated", extract: "Weight bearing as tolerated"
- If text mentions "follow-up in three weeks", extract: "Follow-up recommended in three weeks"
- If text mentions functional improvements or ADL changes, extract those details

Extract information from the Plan section, treatment discussions, medication mentions, and outcomes discussions.
If information is not present, use null or empty strings.

Return ONLY valid JSON, no additional text."""

        # Limit text length to avoid token limits
        text_to_analyze = str(text)[:5000] if len(str(text)) > 5000 else str(text)
        
        user_prompt = f"""Extract current treatments/medications and outcomes ADL from the following medical text:

{text_to_analyze}

Return a JSON object with current_treatments_and_meds and outcomes_adl fields."""

        logger.info("Calling GPT API to extract treatments and outcomes from text...")
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            max_completion_tokens=1500
        )
        
        result = json.loads(response.choices[0].message.content)
        logger.info(f"Successfully extracted treatments/outcomes from text")
        return result
            
    except Exception as e:
        logger.error(f"Error extracting treatments/outcomes from text with GPT: {e}")
        return None


def extract_work_status_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Extract work status and restrictions from transcription or SOAP text using GPT"""
    if not text or not str(text).strip():
        return None
    
    try:
        openai_api_key = get_openai_api_key()
        if not openai_api_key:
            logger.warning("OpenAI API key not available, skipping work status extraction")
            return None
        
        client = create_openai_client()
        
        system_prompt = """You are a medical documentation assistant specializing in extracting work status and restrictions from clinical transcriptions and SOAP notes.

Your task is to analyze the provided medical text and extract work status information including:
- Work status/capacity (Full Duty, Modified Duty, TTD/Temporary Total Disability, etc.)
- Work restrictions (lifting limits, standing/walking/sitting tolerances, activity limitations)
- Return to work dates
- Any other work-related information

Return a JSON object with the following structure:
{
  "work_status": "Full Duty" | "Modified Duty" | "TTD" | "Temporary Total Disability" | etc.,
  "restrictions": "Text description of restrictions if any",
  "restrictions_details": {
    "liftCarryPounds": "weight limit if mentioned",
    "standing": "standing tolerance if mentioned",
    "walking": "walking tolerance if mentioned",
    "sitting": "sitting tolerance if mentioned",
    "climbing": "climbing restrictions if mentioned",
    "forwardBending": "forward bending restrictions if mentioned",
    "kneeling": "kneeling restrictions if mentioned",
    "crawling": "crawling restrictions if mentioned",
    "twisting": "twisting restrictions if mentioned",
    "keyboarding": "keyboarding restrictions if mentioned",
    "graspingRight": true/false,
    "graspingLeft": true/false,
    "graspingBilateral": true/false,
    "graspingHours": "hours if mentioned",
    "pushingPullingRight": true/false,
    "pushingPullingLeft": true/false,
    "pushingPullingBilateral": true/false,
    "pushingPullingHours": "hours if mentioned"
  },
  "return_full_duty_date": "date if mentioned",
  "unable_to_return_start_date": "date if mentioned",
  "unable_to_return_end_date": "date if mentioned",
  "unable_to_return_reason": "reason if mentioned"
}

CRITICAL RULES:
1. Extract ONLY restrictions that are EXPLICITLY mentioned in the text. Do NOT infer or assume restrictions.
2. If "restricted from" or "restrictions" or "restricted" is mentioned along with specific activities, set returnToWorkWithRestrictions to true.
3. Extract ALL restrictions that ARE mentioned (e.g., if both "avoid prolonged standing" AND "limit walking" are mentioned, extract BOTH).
4. If a restriction is NOT mentioned, use empty string "" for text fields and false for boolean fields in restrictions_details.

Extract all work status information mentioned in the text. If information is not present, use null or empty strings.
For dates, normalize to MM/DD/YYYY format if possible.
For restrictions_details:
- Extract ONLY explicitly mentioned restrictions
- If you see "restricted from prolonged standing", extract standing: "Avoid prolonged standing"
- If you see "limit walking to short distances only", extract walking: "Limit walking to short distances only"
- If a restriction is NOT mentioned, use empty string ""
- Preserve the exact wording or create a clear, concise summary

Examples:
- Text: "restricted from prolonged standing and should limit walking to short distances only"
  → returnToWorkWithRestrictions: true
  → restrictions_details: {standing: "Avoid prolonged standing", walking: "Limit walking to short distances only", all others: "" or false}

Return ONLY valid JSON, no additional text."""

        # Limit text length to avoid token limits
        text_to_analyze = str(text)[:5000] if len(str(text)) > 5000 else str(text)
        
        user_prompt = f"""Extract work status and restrictions from the following medical text:

{text_to_analyze}

IMPORTANT: Extract ONLY restrictions that are EXPLICITLY mentioned. 
- If you see "restricted from prolonged standing", extract standing: "Avoid prolonged standing"
- If you see "limit walking to short distances only", extract walking: "Limit walking to short distances only"
- If restrictions are mentioned, set work_status to "Modified Duty" or similar
- If a restriction is NOT mentioned, use empty string "" for text fields and false for boolean fields

Return a JSON object with work status information."""

        logger.info("Calling GPT API to extract work status from text...")
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            max_completion_tokens=1500
        )
        
        result = json.loads(response.choices[0].message.content)
        logger.info(f"Successfully extracted work status from text: {result.get('work_status', 'Not found')}")
        return result
            
    except Exception as e:
        logger.error(f"Error extracting work status from text with GPT: {e}")
        return None


def parse_restrictions_from_text(restrictions_text: str) -> Optional[Dict[str, Any]]:
    """Parse restrictions text and extract detailed restriction fields using GPT"""
    if not restrictions_text or not str(restrictions_text).strip():
        return None
    
    try:
        openai_api_key = get_openai_api_key()
        if not openai_api_key:
            logger.warning("OpenAI API key not available, skipping restrictions parsing")
            return None
        
        client = create_openai_client()
        
        system_prompt = """You are a medical documentation assistant specializing in parsing work restrictions from medical text.

Your task is to extract detailed work restriction information from the provided text and return it as a structured JSON object.

CRITICAL RULES:
1. Extract ONLY restrictions that are EXPLICITLY mentioned in the text. Do NOT infer or assume restrictions.
2. If a restriction is NOT mentioned, use empty string "" for text fields and false for boolean fields.
3. Extract ALL restrictions that ARE mentioned (e.g., if both "avoid prolonged standing" AND "limit walking" are mentioned, extract BOTH).
4. Preserve the exact wording or create a clear, concise summary of what was stated.

Extract the following fields from the restrictions text:
- liftCarryPounds: Weight limit ONLY if explicitly mentioned (e.g., "20", "10", "50", "no lifting over 10 lbs"). If not mentioned, use "".
- liftCarryHeight: Height restriction ONLY if explicitly mentioned. If not mentioned, use "".
- standing: Extract ONLY if standing restrictions are mentioned. Examples:
  * "avoid prolonged standing" → "Avoid prolonged standing"
  * "restricted from prolonged standing" → "Avoid prolonged standing"
  * "no prolonged standing" → "Avoid prolonged standing"
  * "standing limited to 4 hours" → "4 hours"
  * If NOT mentioned, use "".
- walking: Extract ONLY if walking restrictions are mentioned. Examples:
  * "limit walking to short distances only" → "Limit walking to short distances only"
  * "limit walking" → "Limit walking"
  * "walking limited to short distances" → "Limit walking to short distances only"
  * "no walking" → "Avoid walking"
  * If NOT mentioned, use "".
- sitting: Extract ONLY if sitting restrictions are mentioned. If NOT mentioned, use "".
- climbing: Extract ONLY if climbing restrictions are explicitly mentioned (e.g., "no climbing", "avoid climbing"). If NOT mentioned, use "".
- forwardBending: Extract ONLY if forward bending restrictions are explicitly mentioned. If NOT mentioned, use "".
- kneeling: Extract ONLY if kneeling restrictions are explicitly mentioned. If NOT mentioned, use "".
- crawling: Extract ONLY if crawling restrictions are explicitly mentioned. If NOT mentioned, use "".
- twisting: Extract ONLY if twisting restrictions are explicitly mentioned. If NOT mentioned, use "".
- keyboarding: Extract ONLY if keyboarding restrictions are explicitly mentioned. If NOT mentioned, use "".
- graspingRight: Boolean - true ONLY if right hand grasping is explicitly mentioned as allowed, false if restricted, false if not mentioned.
- graspingLeft: Boolean - true ONLY if left hand grasping is explicitly mentioned as allowed, false if restricted, false if not mentioned.
- graspingBilateral: Boolean - true ONLY if bilateral grasping is explicitly mentioned as allowed, false if restricted, false if not mentioned.
- graspingHours: Extract ONLY if grasping hours are explicitly mentioned. If NOT mentioned, use "".
- pushingPullingRight: Boolean - true ONLY if right hand pushing/pulling is explicitly mentioned as allowed, false if restricted, false if not mentioned.
- pushingPullingLeft: Boolean - true ONLY if left hand pushing/pulling is explicitly mentioned as allowed, false if restricted, false if not mentioned.
- pushingPullingBilateral: Boolean - true ONLY if bilateral pushing/pulling is explicitly mentioned as allowed, false if restricted, false if not mentioned.
- pushingPullingHours: Extract ONLY if pushing/pulling hours are explicitly mentioned. If NOT mentioned, use "".

EXAMPLES:
- Text: "restricted from prolonged standing and should limit walking to short distances only"
  → standing: "Avoid prolonged standing", walking: "Limit walking to short distances only", all others: "" or false

- Text: "no lifting over 20 pounds"
  → liftCarryPounds: "20", all others: "" or false

- Text: "avoid climbing and kneeling"
  → climbing: "Avoid", kneeling: "Avoid", all others: "" or false

Return ONLY valid JSON with the restrictions object, no additional text."""

        user_prompt = f"""Extract detailed work restrictions from the following text. Extract ONLY restrictions that are EXPLICITLY mentioned. If a restriction is NOT mentioned, use empty string "" for text fields and false for boolean fields:

{restrictions_text}

Return a JSON object with the restrictions fields. Extract ONLY explicitly mentioned restrictions. Use empty strings "" for text fields that are NOT mentioned, and false for boolean fields that are NOT mentioned."""

        logger.info("Calling GPT API to parse restrictions text...")
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            max_completion_tokens=1000
        )
        
        result = json.loads(response.choices[0].message.content)
        
        # Extract restrictions object if nested, otherwise use the root
        if "restrictions" in result:
            return result["restrictions"]
        elif any(key in result for key in ["liftCarryPounds", "standing", "walking", "sitting"]):
            return result
        else:
            logger.warning("GPT returned unexpected structure for restrictions parsing")
            return None
            
    except Exception as e:
        logger.error(f"Error parsing restrictions text with GPT: {e}")
        return None


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
    # Comprehensive extraction from multiple sources
    work_status_obj = doc.get("work_status")
    work_status = None
    restrictions = None
    restrictions_duration = None
    meds_affect_alertness = None
    meds_effect_description = None
    work_status_source = None  # Track where work_status was found
    
    # Extract detailed restrictions if available
    detailed_restrictions = None
    
    # Priority 1: Check nested work_status structure
    if isinstance(work_status_obj, dict):
        # Extract from nested work_status dict
        work_status = work_status_obj.get("work_status") or work_status_obj.get("status") or work_status_obj.get("workStatus")
        restrictions = work_status_obj.get("restrictions")
        detailed_restrictions = work_status_obj.get("restrictions")  # Could be dict or string
        
        # Handle nested dates structure
        dates_obj = work_status_obj.get("dates")
        if isinstance(dates_obj, dict):
            restrictions_duration = dates_obj.get("duration")
        
        # Handle medication effects
        meds_obj = work_status_obj.get("medication_effects") or work_status_obj.get("medicationEffects")
        if isinstance(meds_obj, dict):
            meds_affect_alertness = meds_obj.get("affect_alertness") or meds_obj.get("affectAlertness")
            meds_effect_description = meds_obj.get("description")
        elif isinstance(meds_obj, str):
            meds_effect_description = meds_obj
        
        if work_status:
            work_status_source = "SOAP dictation (nested work_status structure)"
            logger.info(f"✓ Found Work Status from {work_status_source}: {work_status}")
    elif isinstance(work_status_obj, str):
        work_status = work_status_obj
        work_status_source = "SOAP dictation (work_status as string)"
        logger.info(f"✓ Found Work Status from {work_status_source}: {work_status}")
    
    # Priority 2: Check flat work_status field
    if not work_status:
        work_status = doc.get("work_status") or doc.get("workStatus")
        if work_status:
            work_status_source = "SOAP dictation (flat work_status field)"
            logger.info(f"✓ Found Work Status from {work_status_source}: {work_status}")
    
    # Priority 3: Check page7 structure
    page7 = doc.get("page7")
    if not work_status and isinstance(page7, dict):
        # page7 might have work status info
        page7_work_status = page7.get("workStatus") or page7.get("work_status")
        if page7_work_status:
            work_status = page7_work_status
            work_status_source = "SOAP dictation (page7 structure)"
            logger.info(f"✓ Found Work Status from {work_status_source}: {work_status}")
    elif not page7:
        page7 = None  # Ensure page7 is defined even if not found
    
    # Priority 4: Check plan section
    plan = doc.get("plan")
    if not work_status and isinstance(plan, dict):
        plan_work_status = plan.get("work_status") or plan.get("workStatus")
        if plan_work_status:
            if isinstance(plan_work_status, str):
                work_status = plan_work_status
                work_status_source = "SOAP dictation (plan.work_status)"
                logger.info(f"✓ Found Work Status from {work_status_source}: {work_status}")
    
    # Priority 5: Check clinical_information nested structure
    clinical_info = doc.get("clinical_information") or doc.get("clinicalInformation")
    if not work_status and isinstance(clinical_info, dict):
        plan_section = clinical_info.get("plan")
        if isinstance(plan_section, dict):
            plan_work_status = plan_section.get("work_status") or plan_section.get("workStatus")
            if plan_work_status:
                work_status = plan_work_status
                work_status_source = "SOAP dictation (clinical_information.plan.work_status)"
                logger.info(f"✓ Found Work Status from {work_status_source}: {work_status}")
    
    # Priority 6: Extract from formatted_soap_note text using GPT
    formatted_soap = doc.get("formatted_soap_note")
    if not work_status and formatted_soap and isinstance(formatted_soap, str):
        # First try regex patterns for quick extraction
        work_status_patterns = [
            r'(?:WORK STATUS|Work Status|Work Capacity)[:\s]*([^\n]+)',
            r'(?:RTW status|Return to Work)[:\s]*([^\n]+)',
            r'(?:Work Capacity|Capacity)[:\s]*([^\n]+)',
            r'(?:Full Duty|Modified Duty|TTD|Temporary Total)[^\n]*'
        ]
        for pattern in work_status_patterns:
            work_status_match = re.search(pattern, formatted_soap, re.IGNORECASE)
            if work_status_match:
                # Safely extract match group, handling None values
                if work_status_match.lastindex and work_status_match.lastindex >= 1:
                    extracted_status = work_status_match.group(1)
                else:
                    extracted_status = work_status_match.group(0)
                
                # Check if extracted_status is None before calling strip()
                if extracted_status:
                    extracted_status = str(extracted_status).strip()
                    # Clean up the extracted text
                    extracted_status = re.sub(r'[:\-]', '', extracted_status).strip()
                else:
                    extracted_status = ""
                if extracted_status and len(extracted_status) > 2:
                    work_status = extracted_status
                    work_status_source = "SOAP dictation (formatted_soap_note text extraction)"
                    logger.info(f"✓ Found Work Status from {work_status_source}: {work_status}")
                    break
        
        # If not found with regex, use GPT to extract from text
        if not work_status:
            logger.info("Attempting GPT extraction of work status from formatted_soap_note...")
            extracted_work_status = extract_work_status_from_text(formatted_soap)
            if extracted_work_status and extracted_work_status.get("work_status"):
                work_status = extracted_work_status.get("work_status")
                work_status_source = "SOAP dictation (formatted_soap_note GPT extraction)"
                logger.info(f"✓ Found Work Status from {work_status_source}: {work_status}")
                
                # Also extract restrictions if available
                if not restrictions and extracted_work_status.get("restrictions"):
                    restrictions = extracted_work_status.get("restrictions")
                if not detailed_restrictions and extracted_work_status.get("restrictions_details"):
                    detailed_restrictions = extracted_work_status.get("restrictions_details")
    
    # Priority 6b: Extract from transcription text if available
    transcription_text = doc.get("transcription") or doc.get("corrected_transcription")
    if not work_status and transcription_text and isinstance(transcription_text, str):
        logger.info("Attempting GPT extraction of work status from transcription...")
        extracted_work_status = extract_work_status_from_text(transcription_text)
        if extracted_work_status and extracted_work_status.get("work_status"):
            work_status = extracted_work_status.get("work_status")
            work_status_source = "SOAP dictation (transcription GPT extraction)"
            logger.info(f"✓ Found Work Status from {work_status_source}: {work_status}")
            
            # Also extract restrictions if available
            if not restrictions and extracted_work_status.get("restrictions"):
                restrictions = extracted_work_status.get("restrictions")
            if not detailed_restrictions and extracted_work_status.get("restrictions_details"):
                detailed_restrictions = extracted_work_status.get("restrictions_details")
    
    # Priority 7: Check intake form (fallback)
    if not work_status and intake_doc:
        intake_prior_treatment = intake_doc.get("section_e")  # PriorTreatment section
        if isinstance(intake_prior_treatment, dict):
            intake_work_status = intake_prior_treatment.get("work_status")
            if intake_work_status:
                work_status = intake_work_status
                work_status_source = "Intake form (section_e.work_status)"
                logger.info(f"✓ Found Work Status from {work_status_source}: {work_status}")
    
    # Extract restrictions and other fields
    if not restrictions:
        restrictions = doc.get("restrictions")
    if not detailed_restrictions:
        detailed_restrictions = doc.get("restrictions")
    if not restrictions_duration:
        restrictions_duration = doc.get("restrictions_duration") or doc.get("restrictionsDuration")
    if not meds_affect_alertness:
        meds_affect_alertness = doc.get("meds_affect_alertness") or doc.get("medsAffectAlertness")
    if not meds_effect_description:
        meds_effect_description = doc.get("meds_effect_description") or doc.get("medsEffectDescription")
    
    # Check for detailed restrictions in page7 or restrictions object
    # Note: page7 was already checked above for work_status, ensure it's defined
    if not isinstance(detailed_restrictions, dict):
        # Ensure page7 is defined
        if page7 is None:
            page7 = doc.get("page7")
        if isinstance(page7, dict) and isinstance(page7.get("restrictions"), dict):
            detailed_restrictions = page7.get("restrictions")
        # Try to get from restrictions field directly if it's a dict
        elif isinstance(doc.get("restrictions"), dict):
            detailed_restrictions = doc.get("restrictions")
        # If restrictions is text, try to parse it
        elif restrictions and isinstance(restrictions, str) and restrictions.strip():
            logger.info("Parsing restrictions text to extract detailed fields...")
            parsed_restrictions = parse_restrictions_from_text(restrictions)
            if parsed_restrictions:
                detailed_restrictions = parsed_restrictions
                logger.info("Successfully parsed restrictions text")
    
    # Also check formatted_soap_note or plan section for restrictions text
    if not isinstance(detailed_restrictions, dict):
        # Check plan section for restrictions
        plan = doc.get("plan")
        if isinstance(plan, dict):
            plan_work_status = plan.get("work_status")
            if plan_work_status and isinstance(plan_work_status, str) and plan_work_status.strip():
                logger.info("Parsing restrictions from plan.work_status...")
                parsed_restrictions = parse_restrictions_from_text(plan_work_status)
                if parsed_restrictions:
                    detailed_restrictions = parsed_restrictions
        
        # Check formatted_soap_note for restrictions text
        formatted_soap = doc.get("formatted_soap_note")
        if formatted_soap and isinstance(formatted_soap, str):
            # Look for work status or restrictions section
            work_status_match = re.search(r'(?:WORK STATUS|Work Status|Restrictions)[:\s]*(.*?)(?=\n\n|\n[A-Z]|$)', formatted_soap, re.IGNORECASE | re.DOTALL)
            if work_status_match:
                restrictions_group = work_status_match.group(1)
                restrictions_text = restrictions_group.strip() if restrictions_group else ""
                if restrictions_text and len(restrictions_text) > 10:  # Only parse if substantial text
                    logger.info("Parsing restrictions from formatted_soap_note...")
                    parsed_restrictions = parse_restrictions_from_text(restrictions_text)
                    if parsed_restrictions:
                        detailed_restrictions = parsed_restrictions
    
    # Priority 8: Check follow-up form (final fallback)
    if not work_status:
        followup_work_status = b.get("work_status_perception") or b.get("workStatusPerception")
        if followup_work_status:
            work_status = followup_work_status
            work_status_source = "Follow-up form (section_b.work_status_perception)"
            logger.info(f"✓ Found Work Status from {work_status_source}: {work_status}")
    
    # Final check: if still no work_status found, log warning and set default
    if not work_status:
        work_status = "[Not documented]"
        logger.warning(f"⚠ Work Status not found in any source (mandatory per mapping). Checked: SOAP dictation, page7, plan, clinical_information, formatted_soap_note, intake form, follow-up form")
    else:
        logger.info(f"✅ Work Status successfully extracted from: {work_status_source}")
    
    # Normalize work_status to determine boolean flags
    work_status_lower = str(work_status).lower() if work_status else ""
    return_to_full_duty = "full duty" in work_status_lower or "full" in work_status_lower
    unable_to_return_to_work = "ttd" in work_status_lower or "temporary total" in work_status_lower or "unable" in work_status_lower
    
    # Check if restrictions exist - either as text or detailed restrictions object
    has_restrictions_text = restrictions and str(restrictions).strip()
    has_detailed_restrictions = False
    if isinstance(detailed_restrictions, dict):
        # Check if any restriction field has a non-empty value
        has_detailed_restrictions = any(
            (isinstance(v, str) and v.strip()) or (isinstance(v, bool) and v) or (v and v != "")
            for v in detailed_restrictions.values()
        )
    
    return_to_work_with_restrictions = (
        "modified" in work_status_lower or 
        "restriction" in work_status_lower or 
        "restricted" in work_status_lower or
        has_restrictions_text or 
        has_detailed_restrictions
    )
    
    # Extract patientStatus object if available
    patient_status = doc.get("patientStatus")
    if isinstance(patient_status, dict):
        # Use patientStatus dates if available, otherwise fall back to flat fields
        return_full_duty_date = to_mmddyyyy(patient_status.get("returnToFullDutyDate") or doc.get("return_full_duty_date"))
        unable_to_return_start_date = to_mmddyyyy(patient_status.get("unableToReturnStartDate") or doc.get("unable_to_return_start_date"))
        unable_to_return_end_date = to_mmddyyyy(patient_status.get("unableToReturnEndDate") or doc.get("unable_to_return_end_date"))
        unable_to_return_reason = patient_status.get("unableToReturnReason") or doc.get("unable_to_return_reason") or ""
        logger.info("Using patientStatus object for date extraction")
    else:
        # Fall back to flat fields
        return_full_duty_date = to_mmddyyyy(doc.get("return_full_duty_date") or doc.get("returnToFullDutyDate"))
        unable_to_return_start_date = to_mmddyyyy(doc.get("unable_to_return_start_date") or doc.get("unableToReturnStartDate"))
        unable_to_return_end_date = to_mmddyyyy(doc.get("unable_to_return_end_date") or doc.get("unableToReturnEndDate"))
        unable_to_return_reason = doc.get("unable_to_return_reason") or doc.get("unableToReturnReason") or ""
    
    # Check page7 for dates and flags if not found
    page7 = doc.get("page7")
    if isinstance(page7, dict):
        if not return_full_duty_date:
            return_full_duty_date = to_mmddyyyy(page7.get("returnToFullDutyDate"))
        if not unable_to_return_start_date:
            unable_to_return_start_date = to_mmddyyyy(page7.get("unableToReturnStartDate"))
        if not unable_to_return_end_date:
            unable_to_return_end_date = to_mmddyyyy(page7.get("unableToReturnEndDate"))
        if not unable_to_return_reason:
            unable_to_return_reason = page7.get("unableToReturnReason") or ""
        if not isinstance(detailed_restrictions, dict):
            detailed_restrictions = page7.get("restrictions")
        # Use page7 boolean flags if explicitly set, otherwise keep derived values
        if "returnToFullDuty" in page7:
            return_to_full_duty = bool(page7.get("returnToFullDuty", False))
        if "unableToReturnToWork" in page7:
            unable_to_return_to_work = bool(page7.get("unableToReturnToWork", False))
        if "returnToWorkWithRestrictions" in page7:
            return_to_work_with_restrictions = bool(page7.get("returnToWorkWithRestrictions", False))
    
    # Build restrictions object with defaults
    # Merge restrictions from all sources - prioritize non-empty values
    restrictions_obj = {
        "liftCarryPounds": "",
        "liftCarryHeight": "",
        "standing": "",
        "walking": "",
        "sitting": "",
        "climbing": "",
        "forwardBending": "",
        "kneeling": "",
        "crawling": "",
        "twisting": "",
        "keyboarding": "",
        "graspingRight": False,
        "graspingLeft": False,
        "graspingBilateral": False,
        "graspingHours": "",
        "pushingPullingRight": False,
        "pushingPullingLeft": False,
        "pushingPullingBilateral": False,
        "pushingPullingHours": ""
    }
    
    if isinstance(detailed_restrictions, dict):
        # Merge restrictions - only update fields that have values
        for key in restrictions_obj.keys():
            if key in detailed_restrictions:
                value = detailed_restrictions.get(key)
                if value is not None and value != "":
                    if isinstance(restrictions_obj[key], bool):
                        restrictions_obj[key] = bool(value)
                    else:
                        restrictions_obj[key] = str(value) if value else ""
                elif isinstance(restrictions_obj[key], bool) and value is False:
                    restrictions_obj[key] = False
    
    # Get otherRestrictions from page7 or restrictions text
    other_restrictions = ""
    if isinstance(page7, dict):
        other_restrictions = page7.get("otherRestrictions") or ""
    if not other_restrictions and restrictions and isinstance(restrictions, str):
        other_restrictions = restrictions
    
    # Extract patient name for page7 structure
    patient_name = None
    if isinstance(page7, dict):
        patient_name = page7.get("patientName") or page7.get("patient_name")
    if not patient_name:
        patient_name = pick_name(intake_doc, doc)
    if not patient_name:
        # Try from SOAP patient_info
        patient_info = doc.get("patient_info") or doc.get("patient_information")
        if isinstance(patient_info, dict):
            patient_name = patient_info.get("name") or patient_info.get("patientName")
    patient_name = patient_name or ""  # Default to empty string if not found
    
    return {
        "patientName": patient_name,
        "returnToFullDuty": return_to_full_duty,
        "returnToFullDutyDate": return_full_duty_date or "",
        "unableToReturnToWork": unable_to_return_to_work,
        "unableToReturnStartDate": unable_to_return_start_date or "",
        "unableToReturnEndDate": unable_to_return_end_date or "",
        "unableToReturnReason": unable_to_return_reason or "",
        "returnToWorkWithRestrictions": return_to_work_with_restrictions,
        "restrictions": restrictions_obj,
        "otherRestrictions": other_restrictions or ""
    }


def to_yyyy_mm_dd(s: Optional[str]) -> Optional[str]:
    """Convert date string to YYYY-MM-DD format (ISO 8601 date format)"""
    if not s:
        return None
    
    # Accept common formats and normalize to YYYY-MM-DD
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    
    return None  # Return None if unknown format


def extract_body_parts_from_diagnoses(
    soap_doc: Optional[Dict[str, Any]],
    section_b: Optional[Dict[str, Any]]
) -> str:
    """Extract body parts injured from diagnoses or assessment"""
    body_parts = []
    
    # Try to extract from SOAP diagnoses
    if soap_doc:
        diagnoses = soap_doc.get("diagnoses") or []
        if isinstance(diagnoses, list):
            for diag in diagnoses:
                if isinstance(diag, dict):
                    condition = diag.get("condition") or diag.get("diagnosis") or ""
                    if condition:
                        # Try to extract body part from condition name
                        # Common patterns: "lower back", "right shoulder", "knee", etc.
                        condition_lower = condition.lower()
                        body_part_keywords = {
                            "back": "back", "spine": "back", "lumbar": "lower back",
                            "neck": "neck", "cervical": "neck",
                            "shoulder": "shoulder", "arm": "arm", "elbow": "elbow",
                            "wrist": "wrist", "hand": "hand", "finger": "finger",
                            "knee": "knee", "leg": "leg", "ankle": "ankle", "foot": "foot",
                            "hip": "hip", "thigh": "thigh"
                        }
                        for keyword, part in body_part_keywords.items():
                            if keyword in condition_lower and part not in body_parts:
                                body_parts.append(part)
        
        # Also try assessment/discussion_assessment
        assessment = soap_doc.get("discussion_assessment") or soap_doc.get("assessment") or ""
        if assessment and isinstance(assessment, str):
            assessment_lower = assessment.lower()
            for keyword, part in body_part_keywords.items():
                if keyword in assessment_lower and part not in body_parts:
                    body_parts.append(part)
    
    # Try to extract from section B diagnoses
    if section_b:
        primary_dx = section_b.get("primary_diagnosis") or ""
        secondary_dx = section_b.get("secondary_diagnosis") or ""
        additional_dx_list = section_b.get("additional_diagnoses") or []
        
        if primary_dx:
            body_parts.append(primary_dx)
        if secondary_dx:
            body_parts.append(secondary_dx)
        if isinstance(additional_dx_list, list):
            body_parts.extend([dx for dx in additional_dx_list if dx])
    
    # Remove duplicates and format
    body_parts = list(dict.fromkeys(body_parts))  # Preserves order while removing duplicates
    
    if body_parts:
        return ", ".join(body_parts)
    
    return ""


def extract_work_status_format(
    pr1_payload: Dict[str, Any],
    intake_doc: Optional[Dict[str, Any]] = None,
    soap_doc: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Extract work status data from PR1 payload and format it according to the Work Status Form structure.
    
    Args:
        pr1_payload: Complete PR1 payload structure from build_pr1_payload()
        intake_doc: Optional intake document for additional data
        soap_doc: Optional SOAP document for additional data
    
    Returns:
        Dictionary with work_status_data structure matching the Work Status Form API format
    """
    header = pr1_payload.get("header_admin") or {}
    section_c = pr1_payload.get("section_c_work_status") or {}
    section_b = pr1_payload.get("section_b_evaluation_management") or {}
    
    # Extract Employee Information
    employee_name = header.get("patient_name") or ""
    claim_number = header.get("claim_number") or ""
    date_of_injury = to_yyyy_mm_dd(header.get("date_of_injury")) or ""
    date_of_evaluation = to_yyyy_mm_dd(header.get("date_of_first_examination")) or ""
    body_parts_injured = extract_body_parts_from_diagnoses(soap_doc, section_b)
    
    # Extract next follow-up appointment from SOAP or section C
    next_follow_up = ""
    if soap_doc:
        next_visit = soap_doc.get("next_visit_date") or soap_doc.get("mmi_date")
        if not next_visit:
            patient_status = soap_doc.get("patientStatus")
            if isinstance(patient_status, dict):
                next_visit = patient_status.get("nextVisitDate")
        if next_visit:
            next_follow_up = to_yyyy_mm_dd(next_visit) or ""
    
    employee_info = {
        "employeeName": employee_name,
        "claimNumber": claim_number,
        "dateOfInjury": date_of_injury,
        "dateOfEvaluation": date_of_evaluation,
        "bodyPartsInjured": body_parts_injured,
        "nextFollowUpAppointment": next_follow_up
    }
    
    # Extract Work Status
    return_to_full_duty = section_c.get("returnToFullDuty", False)
    return_to_full_duty_date = section_c.get("returnToFullDutyDate") or ""
    unable_to_return = section_c.get("unableToReturnToWork", False)
    unable_to_return_start = section_c.get("unableToReturnStartDate") or ""
    unable_to_return_end = section_c.get("unableToReturnEndDate") or ""
    return_with_restrictions = section_c.get("returnToWorkWithRestrictions", False)
    
    # Determine work status value
    work_status_value = ""
    full_duty_date = ""
    modified_duty_from = ""
    modified_duty_to = ""
    off_work_from = ""
    off_work_to = ""
    permanent_stationary_date = ""
    
    if return_to_full_duty:
        work_status_value = "fullDuty"
        full_duty_date = to_yyyy_mm_dd(return_to_full_duty_date) or ""
    elif unable_to_return:
        work_status_value = "offWork"
        off_work_from = to_yyyy_mm_dd(unable_to_return_start) or ""
        off_work_to = to_yyyy_mm_dd(unable_to_return_end) or ""
    elif return_with_restrictions:
        work_status_value = "modifiedDuty"
        # Use date_of_evaluation as modified duty start date
        modified_duty_from = date_of_evaluation
        # Try to extract end date from restrictions duration or return full duty date
        if return_to_full_duty_date:
            modified_duty_to = to_yyyy_mm_dd(return_to_full_duty_date) or ""
        elif unable_to_return_end:
            modified_duty_to = to_yyyy_mm_dd(unable_to_return_end) or ""
    
    # Check for Permanent & Stationary / MMI
    if soap_doc:
        mmi_date = soap_doc.get("mmi_date")
        patient_status = soap_doc.get("patientStatus")
        if isinstance(patient_status, dict):
            mmi_checked = patient_status.get("maxMedicalImprovementChecked", False)
            mmi_date = patient_status.get("maxMedicalImprovementDate") or mmi_date
        
        if mmi_date:
            work_status_value = "permanentStationary"
            permanent_stationary_date = to_yyyy_mm_dd(mmi_date) or ""
    
    work_status = {
        "status": work_status_value,
        "fullDutyEffectiveDate": full_duty_date,
        "modifiedDutyFrom": modified_duty_from,
        "modifiedDutyTo": modified_duty_to,
        "offWorkFrom": off_work_from,
        "offWorkTo": off_work_to,
        "permanentStationaryDate": permanent_stationary_date
    }
    
    # Extract Functional Restrictions
    restrictions = section_c.get("restrictions") or {}
    other_restrictions_text = section_c.get("otherRestrictions") or ""
    
    # Map PR1 restrictions to new format
    functional_restrictions = map_pr1_restrictions_to_new_format(restrictions, other_restrictions_text)
    
    # Extract Provider Information
    physician_info = header.get("physician") or {}
    provider_name = physician_info.get("physician_name") or ""
    clinic = physician_info.get("practice_name") or ""
    phone = physician_info.get("telephone") or ""
    signature_date = to_yyyy_mm_dd(pr1_payload.get("page2_signature_and_included_sections", {}).get("signature_date")) or date_of_evaluation
    
    provider_info = {
        "providerName": provider_name,
        "clinic": clinic,
        "phone": phone,
        "signature": provider_name,  # Use provider name as signature
        "date": signature_date
    }
    
    return {
        "employeeInfo": employee_info,
        "workStatus": work_status,
        "functionalRestrictions": functional_restrictions,
        "providerInfo": provider_info
    }


def map_pr1_restrictions_to_new_format(
    restrictions: Dict[str, Any],
    other_restrictions_text: str
) -> Dict[str, Any]:
    """
    Map PR1 restrictions format to the new functional restrictions structure.
    
    PR1 restrictions structure:
    {
        "liftCarryPounds": "",
        "liftCarryHeight": "",
        "standing": "",
        "walking": "",
        "sitting": "",
        "climbing": "",
        "forwardBending": "",
        "kneeling": "",
        "crawling": "",
        "twisting": "",
        "keyboarding": "",
        "graspingRight": False,
        "graspingLeft": False,
        "graspingBilateral": False,
        "graspingHours": "",
        "pushingPullingRight": False,
        "pushingPullingLeft": False,
        "pushingPullingBilateral": False,
        "pushingPullingHours": ""
    }
    """
    # Initialize all restriction categories
    functional_restrictions = {
        "liftingPushingPulling": {
            "noLiftingOver": False,
            "weightLimit": "",
            "customWeight": ""
        },
        "upperExtremity": {
            "noAboveShoulderReaching": False,
            "aboveShoulderRight": False,
            "aboveShoulderLeft": False,
            "useLimited": False,
            "useLimitedSide": "",
            "useLimitedHours": "",
            "noRepetitiveGripping": False,
            "noRepetitiveGrippingRight": False,
            "noRepetitiveGrippingLeft": False
        },
        "lowerExtremity": {
            "noRepetitiveKneeling": False,
            "walkingLimited": False,
            "walkingLimit": "",
            "walkingCustomMin": "",
            "walkingOther": "",
            "noClimbingStairs": False
        },
        "spinalTrunk": {
            "noRepetitiveBending": False,
            "noRepetitiveTwisting": False,
            "twistingNeck": False,
            "twistingWaist": False
        },
        "positionTolerance": {
            "alternateSittingStanding": False,
            "alternateInterval": "",
            "alternateOther": "",
            "standingLimited": False,
            "standingLimit": "",
            "standingCustomMin": "",
            "sittingLimited": False,
            "sittingLimit": "",
            "sittingCustomMin": ""
        },
        "handFineMotor": {
            "productiveUseEnabled": False,
            "productiveUseMinutes": "",
            "productiveUseRight": False,
            "productiveUseLeft": False
        },
        "workplaceConditions": {
            "noWorkingAtHeights": False,
            "noSafetySensitiveDuties": False
        },
        "otherRestrictions": ""
    }
    
    # Map lifting/pushing/pulling
    lift_pounds = restrictions.get("liftCarryPounds", "")
    if lift_pounds:
        functional_restrictions["liftingPushingPulling"]["noLiftingOver"] = True
        # Try to match standard weights
        lift_pounds_str = str(lift_pounds).strip().lower()
        if lift_pounds_str in ["5", "10", "15", "25"]:
            functional_restrictions["liftingPushingPulling"]["weightLimit"] = lift_pounds_str
        else:
            functional_restrictions["liftingPushingPulling"]["weightLimit"] = "custom"
            functional_restrictions["liftingPushingPulling"]["customWeight"] = lift_pounds_str
    
    # Map upper extremity - pushing/pulling
    if restrictions.get("pushingPullingRight") == False or restrictions.get("pushingPullingLeft") == False:
        functional_restrictions["upperExtremity"]["useLimited"] = True
        if restrictions.get("pushingPullingRight") == False:
            functional_restrictions["upperExtremity"]["useLimitedSide"] = "right"
        elif restrictions.get("pushingPullingLeft") == False:
            functional_restrictions["upperExtremity"]["useLimitedSide"] = "left"
        
        push_pull_hours = restrictions.get("pushingPullingHours", "")
        if push_pull_hours:
            functional_restrictions["upperExtremity"]["useLimitedHours"] = str(push_pull_hours)
    
    # Map upper extremity - grasping
    if restrictions.get("graspingRight") == False or restrictions.get("graspingLeft") == False:
        functional_restrictions["upperExtremity"]["noRepetitiveGripping"] = True
        if restrictions.get("graspingRight") == False:
            functional_restrictions["upperExtremity"]["noRepetitiveGrippingRight"] = True
        if restrictions.get("graspingLeft") == False:
            functional_restrictions["upperExtremity"]["noRepetitiveGrippingLeft"] = True
    
    # Map lower extremity - kneeling
    if restrictions.get("kneeling", ""):
        functional_restrictions["lowerExtremity"]["noRepetitiveKneeling"] = True
    
    # Map lower extremity - walking
    walking = restrictions.get("walking", "")
    if walking:
        functional_restrictions["lowerExtremity"]["walkingLimited"] = True
        walking_lower = str(walking).lower()
        if "2" in walking_lower or "two" in walking_lower:
            functional_restrictions["lowerExtremity"]["walkingLimit"] = "2hrs"
        elif "4" in walking_lower or "four" in walking_lower:
            functional_restrictions["lowerExtremity"]["walkingLimit"] = "4hrs"
        else:
            functional_restrictions["lowerExtremity"]["walkingLimit"] = "other"
            functional_restrictions["lowerExtremity"]["walkingOther"] = walking
    
    # Map lower extremity - climbing
    climbing = restrictions.get("climbing", "")
    if climbing and ("avoid" in str(climbing).lower() or "no" in str(climbing).lower()):
        functional_restrictions["lowerExtremity"]["noClimbingStairs"] = True
    
    # Map spinal/trunk - bending
    if restrictions.get("forwardBending", ""):
        functional_restrictions["spinalTrunk"]["noRepetitiveBending"] = True
    
    # Map spinal/trunk - twisting
    twisting = restrictions.get("twisting", "")
    if twisting:
        functional_restrictions["spinalTrunk"]["noRepetitiveTwisting"] = True
        # Try to determine if neck or waist (default to waist)
        twisting_lower = str(twisting).lower()
        if "neck" in twisting_lower or "cervical" in twisting_lower:
            functional_restrictions["spinalTrunk"]["twistingNeck"] = True
        else:
            functional_restrictions["spinalTrunk"]["twistingWaist"] = True
    
    # Map position tolerance - standing
    standing = restrictions.get("standing", "")
    if standing:
        functional_restrictions["positionTolerance"]["standingLimited"] = True
        standing_lower = str(standing).lower()
        if "2" in standing_lower:
            functional_restrictions["positionTolerance"]["standingLimit"] = "2hrs"
        elif "4" in standing_lower:
            functional_restrictions["positionTolerance"]["standingLimit"] = "4hrs"
        else:
            functional_restrictions["positionTolerance"]["standingLimit"] = "custom"
            functional_restrictions["positionTolerance"]["standingCustomMin"] = standing
    
    # Map position tolerance - sitting
    sitting = restrictions.get("sitting", "")
    if sitting:
        functional_restrictions["positionTolerance"]["sittingLimited"] = True
        sitting_lower = str(sitting).lower()
        if "2" in sitting_lower:
            functional_restrictions["positionTolerance"]["sittingLimit"] = "2hrs"
        elif "4" in sitting_lower:
            functional_restrictions["positionTolerance"]["sittingLimit"] = "4hrs"
        else:
            functional_restrictions["positionTolerance"]["sittingLimit"] = "custom"
            functional_restrictions["positionTolerance"]["sittingCustomMin"] = sitting
    
    # Map hand/fine motor - keyboarding restrictions might indicate hand use limitations
    keyboarding = restrictions.get("keyboarding", "")
    if keyboarding:
        functional_restrictions["handFineMotor"]["productiveUseEnabled"] = True
        # Try to extract minutes/hours
        keyboarding_str = str(keyboarding)
        import re
        minutes_match = re.search(r'(\d+)\s*(?:min|minute)', keyboarding_str, re.IGNORECASE)
        if minutes_match:
            functional_restrictions["handFineMotor"]["productiveUseMinutes"] = minutes_match.group(1)
        else:
            functional_restrictions["handFineMotor"]["productiveUseMinutes"] = "20"  # Default
        # Default to both hands
        functional_restrictions["handFineMotor"]["productiveUseRight"] = True
        functional_restrictions["handFineMotor"]["productiveUseLeft"] = True
    
    # Map workplace conditions from other restrictions text
    if other_restrictions_text:
        other_lower = str(other_restrictions_text).lower()
        if "height" in other_lower or "ladder" in other_lower or "scaffold" in other_lower:
            functional_restrictions["workplaceConditions"]["noWorkingAtHeights"] = True
        if "heavy equipment" in other_lower or "machinery" in other_lower or "safety-sensitive" in other_lower:
            functional_restrictions["workplaceConditions"]["noSafetySensitiveDuties"] = True
    
    # Set other restrictions text
    functional_restrictions["otherRestrictions"] = other_restrictions_text
    
    return functional_restrictions


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
    section_a = build_section_a_rfa(soap_doc, intake_doc)
    # Pass intake_doc to build_section_b so it can extract HPI and Objective Findings
    section_b = build_section_b(soap_doc, intake_doc)
    section_c = build_section_c(intake_doc, follow_doc, soap_doc)
    
    # Page 2 signature block
    # Check if section_a has any requests (new format uses "requests" array)
    has_requests = bool(section_a.get("requests") and len(section_a.get("requests", [])) > 0)
    signature_block = {
        "include_section_a": has_requests,
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
- Secondary physician reports (secondary_physician_reports) - MANDATORY if applicable: Reports from other physicians, discuss and incorporate findings if appropriate
- RFA items (requests for authorization - services, goods, drugs with CPT/HCPCS codes)
- Work status (work_status, restrictions, dates, medication effects) - MANDATORY: RTW status (Full Duty / Modified Duty / TTD)
  Extract work status from any mention in the document including:
  - "Work Status" or "Work Capacity" sections
  - "Return to Work" or "RTW" mentions
  - "Full Duty", "Modified Duty", "TTD", "Temporary Total Disability" mentions
  - Any work restrictions or limitations mentioned
- Detailed work restrictions (page7.restrictions object) - Extract detailed restriction fields from work status text:
  - liftCarryPounds: Weight limit (e.g., "20", "10", "50")
  - liftCarryHeight: Height restriction if mentioned
  - standing: Standing tolerance (e.g., "4 hours", "2 hours", "Unlimited")
  - walking: Walking tolerance (e.g., "2 hours", "1 hour", "Unlimited")
  - sitting: Sitting tolerance (e.g., "6 hours", "4 hours", "Unlimited")
  - climbing: Climbing restrictions (e.g., "Limited", "Avoid", "Unlimited")
  - forwardBending: Forward bending restrictions (e.g., "Avoid", "Limited", "Unlimited")
  - kneeling: Kneeling restrictions (e.g., "Avoid", "Limited", "Unlimited")
  - crawling: Crawling restrictions (e.g., "Avoid", "Limited", "Unlimited")
  - twisting: Twisting restrictions (e.g., "Limited", "Avoid", "Unlimited")
  - keyboarding: Keyboarding restrictions (e.g., "Unlimited", "Limited", "4 hours")
  - graspingRight, graspingLeft, graspingBilateral: Boolean flags for grasping ability
  - graspingHours: Hours for grasping activities
  - pushingPullingRight, pushingPullingLeft, pushingPullingBilateral: Boolean flags for pushing/pulling ability
  - pushingPullingHours: Hours for pushing/pulling activities
- Work status flags (page7 object):
  - returnToFullDuty: Boolean - patient can return to full duty
  - returnToFullDutyDate: Date string
  - unableToReturnToWork: Boolean - patient unable to return to work
  - unableToReturnStartDate: Date string
  - unableToReturnEndDate: Date string
  - unableToReturnReason: Reason text
  - returnToWorkWithRestrictions: Boolean - patient can return with restrictions
  - otherRestrictions: Any additional restrictions text
- Patient status (patientStatus object) - Extract patient status information with checked flags and dates:
  - returnToFullDutyChecked (boolean) and returnToFullDutyDate (date string)
  - returnToModifiedDutyChecked (boolean) and returnToModifiedDutyDate (date string)
  - maxMedicalImprovementChecked (boolean) and maxMedicalImprovementDate (date string)
  - nextVisitChecked (boolean) and nextVisitDate (date string)
  - dischargedFromCareChecked (boolean) and dischargedFromCareDate (date string)
- Treatment plan information - MANDATORY if applicable: Surgery, PT, injections, imaging, DME
- Treatment plan checkboxes (if available in document):
  - continue_same_treatment (boolean) - "Continue same treatment plan"
  - change_in_treatment_plan (boolean) - "Change in treatment plan"
  - discharge_from_care (boolean) - "Discharge from care"
  - dispense_as_written (boolean) - "Dispense prescription as written"
- Treatment plan comments (comments field) - Any additional comments or notes related to the treatment plan
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
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
                max_completion_tokens=4000
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
        content = response.choices[0].message.content
        if not content:
            logger.warning("GPT API returned empty content for PDF to SOAP conversion")
            return {}
        json_response = content.strip()
        
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
                       "corrected_transcription", "patientStatus", "continue_same_treatment",
                       "discharge_from_care", "change_in_treatment_plan", "dispense_as_written",
                       "comments"]
            
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


@router.post("/pr1/extract-work-status")
async def extract_work_status_from_pr1(payload: PR1GenerateRequest):
    """
    Extract work status data from PR1 in the Work Status Form format.
    
    This endpoint generates PR1 data (if not already provided) and then extracts
    work status information in the standardized Work Status Form API format.
    
    **Workflow:**
    1. Fetches/resolves intake, follow-up, and SOAP documents (same as /pr1/generate)
    2. Generates complete PR1 payload structure
    3. Extracts work status data in the new format:
       - Employee Information
       - Work Status (fullDuty/modifiedDuty/offWork/permanentStationary)
       - Functional Restrictions (all categories)
       - Provider Information
    
    **Parameters:**
    Same as `/pr1/generate` endpoint - see PR1GenerateRequest model
    
    **Returns:**
    - status: Success status
    - work_status_data: Complete work status data in the new format
    - metadata: Information about which documents were used
    
    **Example Request:**
    ```json
    {
        "use_latest_intake": true,
        "use_latest_followup": true,
        "soap_id": "507f1f77bcf86cd799439013",
        "flags": {
            "progress_report": true
        }
    }
    ```
    
    **Example Response:**
    ```json
    {
        "status": "success",
        "work_status_data": {
            "employeeInfo": {
                "employeeName": "John Doe",
                "claimNumber": "CLM-12345",
                "dateOfInjury": "2024-01-15",
                "dateOfEvaluation": "2024-03-20",
                "bodyPartsInjured": "Lower back, right shoulder",
                "nextFollowUpAppointment": "2024-04-15"
            },
            "workStatus": {
                "status": "modifiedDuty",
                "modifiedDutyFrom": "2024-03-20",
                "modifiedDutyTo": "2024-04-20"
            },
            "functionalRestrictions": {
                "liftingPushingPulling": {
                    "noLiftingOver": true,
                    "weightLimit": "25"
                },
                ...
            },
            "providerInfo": {
                "providerName": "Dr. Jane Smith",
                "clinic": "Pilot Clinic",
                "phone": "(555) 123-4567",
                "signature": "Dr. Jane Smith",
                "date": "2024-03-20"
            }
        },
        "metadata": {
            "intake_source": "latest",
            "followup_source": "latest",
            "soap_source": "id"
        }
    }
    ```
    """
    try:
        # Fetch/resolve data sources (same logic as /pr1/generate)
        intake_doc = None
        intake_source = None
        if payload.intake:
            intake_doc = payload.intake.model_dump(exclude_none=True)
            intake_source = "embedded"
            logger.info("Using embedded intake form data")
        elif payload.intake_id:
            intake_doc = await fetch_if_needed(None, payload.intake_id, COLL_INTAKE)
            intake_source = "id"
            logger.info(f"Fetched intake form with ID: {payload.intake_id}")
        elif payload.use_latest_intake:
            intake_doc = await fetch_latest_document(COLL_INTAKE)
            if intake_doc:
                intake_source = "latest"
                logger.info(f"Using latest intake form with ID: {intake_doc.get('_id')}")
            else:
                logger.warning("No intake forms found in database (use_latest_intake=True)")
        
        follow_doc = None
        followup_source = None
        if payload.followup:
            follow_doc = payload.followup.model_dump(exclude_none=True)
            followup_source = "embedded"
            logger.info("Using embedded follow-up form data")
        elif payload.followup_id:
            follow_doc = await fetch_if_needed(None, payload.followup_id, COLL_FOLLOWUP)
            followup_source = "id"
            logger.info(f"Fetched follow-up form with ID: {payload.followup_id}")
        elif payload.use_latest_followup:
            follow_doc = await fetch_latest_document(COLL_FOLLOWUP)
            if follow_doc:
                followup_source = "latest"
                logger.info(f"Using latest follow-up form with ID: {follow_doc.get('_id')}")
            else:
                logger.warning("No follow-up forms found in database (use_latest_followup=True)")
        
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
        
        # Extract work status in new format
        work_status_data = extract_work_status_format(pr1, intake_doc, soap_doc)
        
        # Prepare response with metadata
        metadata = {
            "intake_source": intake_source,
            "followup_source": followup_source,
            "soap_source": soap_source,
            "intake_id": intake_doc.get("_id") if intake_doc else None,
            "followup_id": follow_doc.get("_id") if follow_doc else None,
            "soap_id": soap_doc.get("_id") if soap_doc else None
        }
        
        return {
            "status": "success",
            "work_status_data": work_status_data,
            "metadata": metadata
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error extracting work status from PR1: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to extract work status from PR1: {str(e)}"
        )


@router.post("/pr1/extract-work-status-from-soap")
async def extract_work_status_from_soap(
    soap_id: str = Form(..., description="SOAP note MongoDB ID"),
    use_latest_intake: bool = Form(False, description="Use latest intake form"),
    use_latest_followup: bool = Form(False, description="Use latest follow-up form"),
    flags: Optional[str] = Form(None, description="JSON string with PR-1 flags")
):
    """
    Extract work status data from SOAP note in the Work Status Form format.
    
    This endpoint is a convenience wrapper that:
    1. Fetches SOAP note by ID
    2. Optionally fetches latest intake/follow-up forms
    3. Generates PR1 data
    4. Extracts work status in the new format
    
    **Parameters:**
    - soap_id: MongoDB ObjectId string of the SOAP note (required)
    - use_latest_intake: Boolean - If True, fetches latest intake form
    - use_latest_followup: Boolean - If True, fetches latest follow-up form
    - flags: Optional JSON string with PR-1 checkbox flags
    
    **Returns:**
    Same format as `/pr1/extract-work-status` endpoint
    """
    try:
        # Parse flags if provided
        flags_dict = {}
        if flags:
            try:
                flags_dict = json.loads(flags)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON in flags parameter: {flags}")
        
        # Create PR1GenerateRequest payload
        payload = PR1GenerateRequest(
            soap_id=soap_id,
            use_latest_intake=use_latest_intake,
            use_latest_followup=use_latest_followup,
            flags=flags_dict
        )
        
        # Use the same extraction logic
        return await extract_work_status_from_pr1(payload)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error extracting work status from SOAP note: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to extract work status from SOAP note: {str(e)}"
        )

