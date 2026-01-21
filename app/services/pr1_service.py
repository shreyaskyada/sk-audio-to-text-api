"""
PR-1 Generator API endpoints
Generates PR-1 form data structure from intake, follow-up, and SOAP note data
"""
import logging
import os
import json

import re
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from bson import ObjectId
from pydantic import BaseModel

from fastapi import HTTPException
from fastapi.responses import JSONResponse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

from app.mongodb import get_database
from app.services.openai import create_openai_client
from app.services.text_extraction import (
    extract_weight_from_text,
    extract_work_status_section,
    extract_date_from_text,
    extract_body_parts_fallback
)
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

from app.models.pr1_models import (
    PR1GenerateRequest,
    IntakeFormForPR1,
    FollowUpFormForPR1,
    SOAPNoteForPR1,
    SOAPDiagnosis,
    SOAPRFAItem,
    SavedPR1Form
)
from app.mongodb import get_database

# Load environment variables (in case this module is imported before main.py loads them)
load_dotenv()

logger = logging.getLogger(__name__)

# Configuration - using existing collections
COLL_INTAKE = "intake_forms"
COLL_FOLLOWUP = "followup_intake_forms"
COLL_SOAP = "soap_notes"
COLL_SAVED_PR1 = "saved_pr1_forms"

# OpenAI Model Configuration - Latest ChatGPT model for PR1 generation
OPENAI_MODEL = 'gpt-5.1'  # Latest GPT-5.1 model




# ============================================
# UTILITY FUNCTIONS
# ============================================

def str_or_nd(val: Optional[str]) -> str:
    """Return value or empty string if empty"""
    return val if (val is not None and str(val).strip() != "") else ""


def format_clinical_data(val: Any) -> str:
    """
    Recursively format clinical data (strings, lists, dicts) into clean text.
    Prevents literal 'arr' or 'object' markers (like ['...'] or {'...'}) from appearing 
    in the final generated PR-1 form text.
    """
    from typing import List, Dict
    
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, (int, float, bool)):
        return str(val)
        
    if isinstance(val, list):
        # Recursively format each item in the list
        parts = [format_clinical_data(item) for item in val if item]
        if not parts:
            return ""
        # Join short items with commas, long items with newlines
        if all(len(p) < 40 for p in parts):
            return ", ".join(parts)
        return "\n".join(parts)
        
    if isinstance(val, dict):
        # Extract the most meaningful text field if it exists
        text_keys = ["text", "content", "value", "summary", "description", "note"]
        for key in text_keys:
            if key in val and val[key]:
                return format_clinical_data(val[key])
        
        # Special handling for medication-like objects (medication, dose, frequency)
        med = val.get("medication") or val.get("name") or val.get("drug") or val.get("medicine")
        dose = val.get("dose") or val.get("dosage") or val.get("strength")
        freq = val.get("frequency") or val.get("freq") or val.get("sig")
        
        if med:
            med_parts = [format_clinical_data(med)]
            if dose: med_parts.append(format_clinical_data(dose))
            if freq: med_parts.append(format_clinical_data(freq))
            return " ".join(med_parts)
            
        # General dictionary formatting: combine keys and values
        dict_parts = []
        for k, v in val.items():
            if v:
                formatted_v = format_clinical_data(v)
                if formatted_v:
                    # Skip internal/generic keys
                    if k.lower() in ("id", "_id", "type", "metadata"):
                        continue
                    if len(str(k)) < 25:
                        dict_parts.append(f"{k}: {formatted_v}")
                    else:
                        dict_parts.append(formatted_v)
        return " | ".join(dict_parts)
        
    return str(val).strip()


def extract_diagnosis_codes_from_soap_assessment(soap_doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Extract diagnosis codes from SOAP note's A - ASSESSMENT section.
    Returns Primary Diagnosis, Secondary Diagnosis, Associated Diagnosis, and Planned Procedures/RFAs codes.
    
    Returns:
        {
            "primary_diagnosis_code": "ICD10_CODE",
            "secondary_diagnosis_code": "ICD10_CODE",
            "associated_diagnosis_codes": ["ICD10_CODE1", "ICD10_CODE2"],
            "planned_procedures_rfa_codes": ["ICD10_CODE1", "ICD10_CODE2"]
        }
    """
    result = {
        "primary_diagnosis_code": None,
        "secondary_diagnosis_code": None,
        "associated_diagnosis_codes": [],
        "planned_procedures_rfa_codes": []
    }
    
    if not soap_doc:
        return result
    
    # Extract diagnoses using existing function
    primary, secondary, additional = primary_secondary_dx(soap_doc)
    
    # Get Primary Diagnosis code
    if primary:
        primary_code = (
            primary.get("icd10") or 
            primary.get("ICD-10") or 
            primary.get("icd_10") or
            primary.get("ICD10") or
            primary.get("icd10_code")
        )
        if primary_code:
            result["primary_diagnosis_code"] = str(primary_code).strip()
            logger.info(f"Extracted Primary Diagnosis code: {result['primary_diagnosis_code']}")
    
    # Get Secondary Diagnosis code
    if secondary:
        secondary_code = (
            secondary.get("icd10") or 
            secondary.get("ICD-10") or 
            secondary.get("icd_10") or
            secondary.get("ICD10") or
            secondary.get("icd10_code")
        )
        if secondary_code:
            result["secondary_diagnosis_code"] = str(secondary_code).strip()
            logger.info(f"Extracted Secondary Diagnosis code: {result['secondary_diagnosis_code']}")
    
    # Get Associated Diagnosis codes (additional diagnoses)
    for dx in additional:
        if dx:
            associated_code = (
                dx.get("icd10") or 
                dx.get("ICD-10") or 
                dx.get("icd_10") or
                dx.get("ICD10") or
                dx.get("icd10_code")
            )
            if associated_code:
                code_str = str(associated_code).strip()
                if code_str not in result["associated_diagnosis_codes"]:
                    result["associated_diagnosis_codes"].append(code_str)
    
    # Extract Planned Procedures / RFAs codes from RFA items
    rfa_items = (soap_doc or {}).get("rfa_items") or (soap_doc or {}).get("RFA_items") or []
    for rfa_item in rfa_items:
        if isinstance(rfa_item, dict):
            rfa_code = (
                rfa_item.get("diagnoses") or 
                rfa_item.get("diagnosisCode") or
                rfa_item.get("diagnosis_code") or
                rfa_item.get("diagnosis_icd10") or 
                rfa_item.get("diagnosis_codes") or
                rfa_item.get("icd10") or
                rfa_item.get("icd10_code")
            )
            # Handle list of diagnoses
            if isinstance(rfa_code, list) and len(rfa_code) > 0:
                 rfa_code = rfa_code[0]
            if rfa_code:
                code_str = str(rfa_code).strip()
                if code_str not in result["planned_procedures_rfa_codes"]:
                    result["planned_procedures_rfa_codes"].append(code_str)
    
    logger.info(f"Extracted diagnosis codes - Primary: {result['primary_diagnosis_code']}, Secondary: {result['secondary_diagnosis_code']}, Associated: {len(result['associated_diagnosis_codes'])}, RFA: {len(result['planned_procedures_rfa_codes'])}")
    
    return result








def generate_cpt_codes_from_diagnosis_codes(diagnosis_codes: Dict[str, Any], transcription: str = "", soap_doc: Optional[Dict[str, Any]] = None, openai_client=None) -> List[str]:
    """
    Extract CPT codes ONLY from transcription text using diagnosis values from A - ASSESSMENT section.
    Uses diagnosis descriptions in prompt but still only extracts codes mentioned in transcription.
    
    Args:
        diagnosis_codes: Dictionary with primary_diagnosis_code, secondary_diagnosis_code, etc.
        transcription: The original transcription text to extract codes from (MANDATORY)
        soap_doc: SOAP document to extract diagnosis descriptions from A - ASSESSMENT section
        openai_client: Optional OpenAI client
    
    Returns:
        List of CPT codes that are actually mentioned in transcription
    """
    if not transcription or not transcription.strip():
        logger.warning("No transcription provided to extract CPT codes from")
        return []
    
    try:
        # First, try to extract codes directly from transcription using regex
        extracted_codes = extract_cpt_codes_from_text(transcription)
        
        if extracted_codes:
            logger.info(f"Extracted {len(extracted_codes)} CPT codes directly from transcription: {', '.join(extracted_codes[:10])}{'...' if len(extracted_codes) > 10 else ''}")
            return extracted_codes
        
        # If no codes found via regex, use AI to extract only what's mentioned
        # But first, extract diagnosis descriptions from A - ASSESSMENT section
        if openai_client and soap_doc:
            from app.cpt_mappings import generate_cpt_with_ai
            
            # Extract diagnosis descriptions from SOAP Assessment section
            primary, secondary, additional = primary_secondary_dx(soap_doc)
            
            # Build diagnosis values for prompt
            primary_dx = ""
            if primary:
                primary_dx = primary.get("condition") or primary.get("diagnosis") or ""
            
            secondary_dx = ""
            if secondary:
                secondary_dx = secondary.get("condition") or secondary.get("diagnosis") or ""
            
            associated_dx = ""
            if additional and len(additional) > 0:
                associated_dx = ", ".join([dx.get("condition") or dx.get("diagnosis") or "" for dx in additional if dx.get("condition") or dx.get("diagnosis")])
            
            # Extract Planned Procedures / RFAs from SOAP document
            planned_procedures = ""
            rfa_items = soap_doc.get("rfa_items") or soap_doc.get("RFA_items") or []
            if rfa_items:
                procedures = []
                for rfa_item in rfa_items:
                    if isinstance(rfa_item, dict):
                        service = rfa_item.get("serviceRequested") or rfa_item.get("service_requested") or rfa_item.get("service_or_good") or ""
                        if service:
                            procedures.append(service)
                if procedures:
                    planned_procedures = ", ".join(procedures)
            
            # Build prompt with diagnosis values from A - ASSESSMENT section
            diagnosis_prompt_parts = []
            if primary_dx:
                diagnosis_prompt_parts.append(f"Primary Diagnosis: {primary_dx}")
            if associated_dx:
                diagnosis_prompt_parts.append(f"Associated Diagnosis: {associated_dx}")
            if secondary_dx:
                diagnosis_prompt_parts.append(f"Secondary Diagnosis: {secondary_dx}")
            if planned_procedures:
                diagnosis_prompt_parts.append(f"Planned Procedure / Requested Service: {planned_procedures}")
            
            diagnosis_context = "\n".join(diagnosis_prompt_parts) if diagnosis_prompt_parts else ""
            
            # Build the full prompt - STRICT: Only generate codes for procedures ACTUALLY mentioned
            if diagnosis_context:
                prompt = f"""Generate CPT/HCPCS codes ONLY for procedures and services that are EXPLICITLY MENTIONED in the transcription below.

A - ASSESSMENT Section Values (for context only):
{diagnosis_context}

Transcription:
{transcription[:2000]}

**CRITICAL - GENERATE ONLY CODES FOR PROCEDURES MENTIONED:**
- Generate PRIMARY CPT code ONLY for the main procedure/service that is EXPLICITLY mentioned in the transcription
- Generate SUPPORTIVE CPT codes ONLY for:
  * Procedures/services that are EXPLICITLY mentioned (e.g., if "ACL reconstruction" is mentioned, include ACL reconstruction codes)
  * DME/supplies that are EXPLICITLY mentioned (e.g., if "crutches" is mentioned, include crutch codes)
  * Imaging that is EXPLICITLY ORDERED (e.g., if "MRI ordered" is mentioned, include MRI codes)
  * Grafts/implants ONLY if mentioned or if the procedure requires them (e.g., ACL reconstruction typically needs graft codes)
- DO NOT generate codes for procedures that are NOT mentioned
- DO NOT generate codes for "possible future procedures" - only what is mentioned
- DO NOT generate codes for different body parts than what is mentioned
- Be STRICT - only include codes that are clearly related to what is described in the transcription
- Use diagnosis values as context, but ONLY generate codes for procedures/services mentioned in transcription"""
            else:
                prompt = f"""Extract ALL CPT/HCPCS codes that are ACTUALLY MENTIONED in this transcription. DO NOT generate codes that are not mentioned. Only extract codes that are explicitly stated.

Transcription:
{transcription[:2000]}

Return ONLY codes that are actually mentioned in the transcription. DO NOT add codes that might be needed."""
            
            _, extracted_cpts = generate_cpt_with_ai(prompt, openai_client)
            
            if extracted_cpts and isinstance(extracted_cpts, list):
                final_cpts = [str(code).strip() for code in extracted_cpts if code and str(code).strip()]
                
                # CRITICAL: Validate codes are relevant to transcription
                from app.cpt_mappings import validate_cpt_codes_relevance
                validated_cpts = validate_cpt_codes_relevance(final_cpts, transcription, openai_client)
                
                logger.info(f"Extracted {len(final_cpts)} CPT codes, validated {len(validated_cpts)} relevant codes: {', '.join(validated_cpts[:10])}{'...' if len(validated_cpts) > 10 else ''}")
                return validated_cpts
        elif openai_client:
            # Fallback if soap_doc is not provided
            from app.cpt_mappings import generate_cpt_with_ai
            
            prompt = f"""Generate CPT/HCPCS codes ONLY for procedures and services that are EXPLICITLY MENTIONED in this transcription.

Transcription:
{transcription[:2000]}

**CRITICAL - GENERATE ONLY CODES FOR PROCEDURES MENTIONED:**
- Generate codes ONLY for procedures/services that are EXPLICITLY mentioned
- DO NOT generate codes for procedures that are NOT mentioned
- DO NOT generate codes for "possible future procedures"
- Be STRICT - only include codes that are clearly related to what is described"""
            
            _, extracted_cpts = generate_cpt_with_ai(prompt, openai_client)
            
            if extracted_cpts and isinstance(extracted_cpts, list):
                final_cpts = [str(code).strip() for code in extracted_cpts if code and str(code).strip()]
                
                # CRITICAL: Validate codes are relevant to transcription
                from app.cpt_mappings import validate_cpt_codes_relevance
                validated_cpts = validate_cpt_codes_relevance(final_cpts, transcription, openai_client)
                
                logger.info(f"Extracted {len(final_cpts)} CPT codes, validated {len(validated_cpts)} relevant codes: {', '.join(validated_cpts[:10])}{'...' if len(validated_cpts) > 10 else ''}")
                return validated_cpts
        
        return []
        
    except Exception as e:
        logger.warning(f"Error extracting CPT codes from transcription: {e}")
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
        "change_in_work_status": True, # Static True per User Request
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
        
        # PRIORITY 1: Try to parse RFA section directly from formatted SOAP note (most reliable)
        if formatted_soap and isinstance(formatted_soap, str):
            logger.info("Attempting direct parsing of RFA section from formatted_soap_note...")
            parsed_rfa = parse_rfa_section_from_formatted_soap(formatted_soap)
            if parsed_rfa:
                rfa_items = parsed_rfa
                logger.info(f"✓ Found {len(rfa_items)} RFA items from direct RFA section parsing")
        
        # PRIORITY 2: Try GPT extraction from Plan section if direct parsing didn't work
        if not rfa_items and formatted_soap and isinstance(formatted_soap, str):
            # Look for Plan section specifically
            plan_match = re.search(r'## P – PLAN\\s*\\n(.*?)(?=---|$)', formatted_soap, re.IGNORECASE | re.DOTALL)
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
        
        # PRIORITY 3: Try transcription if still not found
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
    
    # Get primary diagnosis code for fallback
    assessment_codes = extract_diagnosis_codes_from_soap_assessment(soap_doc)
    primary_code = assessment_codes.get("primary_diagnosis_code")
    
    # CRITICAL: Pre-calculate common data for all RFA items to assume efficiency
    # Get transcription text for code extraction
    transcription_text = str(soap_doc.get("transcription") or soap_doc.get("corrected_transcription") or soap_doc.get("formatted_soap_note") or "")
    
    # CRITICAL: Extract CPT codes mentioned in the SOAP dictation text ONCE
    mentioned_cpt_codes = extract_cpt_codes_from_text(transcription_text)
    
    # Comprehensive CPT code descriptions for better service names - Define ONCE
    cpt_descriptions = {
        # Meniscus procedures
        "29882": "Meniscus Repair",
        "29881": "Meniscectomy",
        "29880": "Meniscectomy (Medial and Lateral)",
        "29883": "Meniscus Repair (Medial and Lateral)",
        "29877": "Chondroplasty",
        "29879": "Microfracture",
        "29884": "Lysis of Adhesions",
        "29887": "OCD Drilling with Bone Grafting",
        "29870": "Arthroscopy, Knee, Diagnostic",
        "29871": "Arthroscopy, Knee, Surgical",
        "29873": "Arthroscopy, Knee, Surgical; with lateral release",
        "29874": "Arthroscopy, Knee, Surgical; for removal of loose body or foreign body",
        "29875": "Arthroscopy, Knee, Surgical; synovectomy, limited",
        "29876": "Arthroscopy, Knee, Surgical; synovectomy, major",
        # Graft/Allograft codes
        "20924": "Tendon Graft",
        "20925": "Tendon Graft Allograft",
        "20926": "Tissue Graft Allograft",
        "20927": "Tendon Graft, from a distance; composite graft",
        "20928": "Tendon Graft, from a distance; allograft, composite",
        "20929": "Tendon Graft, from a distance; autograft, composite",
        # Implant/Anchor codes
        "C1713": "Anchor/Screw Implant",
        "C1714": "Anchor/Screw Implant, Additional",
        "C1715": "Anchor/Screw Implant, Multiple",
        # DME - Braces
        "L1833": "ACL Functional Knee Brace",
        "L1845": "Hinged Knee Brace",
        "L1832": "Elastic Knee Brace",
        "L1830": "Rigid Knee Brace",
        "L1831": "Knee Orthosis, Single Upright",
        "L1843": "Knee Orthosis, Double Upright",
        "L1844": "Knee Orthosis, Four-Point",
        "L1846": "Knee Orthosis, Multi-Axis",
        "L1847": "Knee Orthosis, Custom",
        # DME - Mobility aids
        "E0114": "Crutches, Forearm",
        "E0116": "Crutches, Underarm",
        "E0118": "Crutches, Forearm, Adjustable",
        "E0130": "Walker, Rigid",
        "E0135": "Walker, Wheeled",
        "E0136": "Walker, Rigid, Adjustable",
        "E0137": "Walker, Wheeled, Adjustable",
        "E0138": "Walker, Heavy Duty",
        "E0140": "Walker, Folding",
        "E0141": "Walker, Folding, Adjustable",
        "E0143": "Walker, Wheeled, Folding",
        "E0144": "Walker, Wheeled, Folding, Adjustable",
        "E0147": "Walker, Heavy Duty, Wheeled",
        "E0148": "Walker, Heavy Duty, Wheeled, Adjustable",
        "E0149": "Walker, Bariatric",
        # DME - Canes
        "E0100": "Cane, Standard",
        "E0105": "Cane, Adjustable",
        "E0110": "Cane, Quad",
        "E0111": "Cane, Quad, Adjustable",
        "E0112": "Cane, Offset",
        "E0113": "Cane, Offset, Adjustable",
        # Cryotherapy
        "E0218": "Cryotherapy Device",
        "E0236": "Cold Therapy Pump",
        "E0235": "Cold Therapy Unit",
        "E0239": "Cold Therapy System",
        # Surgical supplies
        "A4566": "Sling or Arm Support",
        "A4570": "Splint",
        "A4572": "Splint, Custom",
        "A4590": "Elastic Bandage",
        "A456": "Surgical Dressing",
        "A4637": "Surgical Dressing, Advanced",
        "A4638": "Surgical Dressing, Specialty",
        # Post-op care supplies
        "A4217": "Sterile Saline Solution",
        "A4218": "Sterile Water",
        "A4219": "Antiseptic Solution",
        "A4220": "Antibiotic Ointment",
        "A4221": "Wound Care Supplies",
        # Additional supplies
        "A6251": "Gauze Pad",
        "A6252": "Gauze Pad, Sterile",
        "A6253": "Gauze Roll",
        "A6254": "Gauze Roll, Sterile",
        "A6255": "Tape, Medical",
        "A6256": "Tape, Surgical"
    }

    def process_rfa_item(it):
        """Process a single RFA item - helper for parallel execution"""
        item_requests = []
        item_drug_requests = []
        
        # Handle both dict and Pydantic model
        if hasattr(it, 'model_dump'):
            it = it.model_dump(exclude_none=True)
        elif not isinstance(it, dict):
            return [], []
        
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
                primary_code or
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
                drug_item = {
                    "type": "drug",
                    "diagnosis": diagnosis,
                    "diagnosisCode": diagnosis_code,
                    "diagnosis_code": diagnosis_code, # FE alias
                    "drug": drug_name,
                    "doseForm": dose_form,
                    "dose_form": dose_form,
                    "quantity": quantity
                }
                item_requests.append(drug_item)
                item_drug_requests.append(drug_item)
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
                primary_code or
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
                # Merge mentioned CPT codes into supportive CPTs (avoid duplicating primary CPT)
                if mentioned_cpt_codes:
                    for code in mentioned_cpt_codes:
                        code_str = str(code).strip()
                        if code_str and code_str != cpt.strip() and code_str not in supportive_cpts:
                            supportive_cpts.append(code_str)
                            # logger.info(f"Added mentioned CPT code {code_str} to supportive CPTs for '{service_requested}'")
                
                # CRITICAL: Extract CPT codes ONLY from transcription (not generate based on diagnosis codes)
                # But only if we don't have enough supportive codes or want to be thorough
                try:
                    # Create separate client per thread for safety, although httpx client is thread safe, this is cleaner
                    openai_client = create_openai_client()
                    generated_cpts_from_diagnosis = generate_cpt_codes_from_diagnosis_codes(
                        assessment_codes,  # Use pre-calculated assessment codes
                        transcription_text,  # Pass pre-calculated transcription
                        soap_doc,  # Pass SOAP document
                        openai_client
                    )
                    
                    if generated_cpts_from_diagnosis:
                        # CRITICAL: Validate codes are relevant to transcription before adding
                        from app.cpt_mappings import validate_cpt_codes_relevance
                        validated_cpts = validate_cpt_codes_relevance(generated_cpts_from_diagnosis, transcription_text, openai_client)
                        
                        # Merge validated CPT codes with existing codes (avoid duplicates)
                        for gen_code in validated_cpts:
                            gen_code_str = str(gen_code).strip()
                            if gen_code_str and gen_code_str != cpt.strip() and gen_code_str not in supportive_cpts:
                                supportive_cpts.append(gen_code_str)
                except Exception as e:
                    logger.warning(f"Could not auto-generate supportive CPTs from diagnosis codes for '{service_requested}': {e}")
                
                # Final validation: Validate all supportive CPTs against transcription
                if supportive_cpts and len(supportive_cpts) > 0:
                    try:
                        # openai_client already created above or create if not
                        if 'openai_client' not in locals():
                            openai_client = create_openai_client()
                            
                        from app.cpt_mappings import validate_cpt_codes_relevance
                        final_validated_cpts = validate_cpt_codes_relevance(supportive_cpts, transcription_text, openai_client)
                        if len(final_validated_cpts) < len(supportive_cpts):
                            supportive_cpts = final_validated_cpts
                    except Exception as e:
                        logger.warning(f"Error in final CPT validation for '{service_requested}': {e}")
                
                # CRITICAL: Split supportive CPTs into separate request items (binary/individual entries)
                # Each supportive CPT code should be a separate RFA request item
                # First, add the primary request item (without supportiveCpts array)
                primary_request = {
                    "type": "treatment",
                    "diagnosis": diagnosis,
                    "diagnosisCode": diagnosis_code,
                    "diagnosis_code": diagnosis_code, # FE alias
                    "serviceRequested": service_requested,
                    "service_requested": service_requested, # FE alias
                    "cpt": cpt,
                    "frequencyDuration": frequency_duration,
                    "frequency_duration": frequency_duration # FE alias
                }
                item_requests.append(primary_request)
                
                # Now create separate request items for each supportive CPT code
                if supportive_cpts and len(supportive_cpts) > 0:
                    for supportive_cpt in supportive_cpts:
                        if supportive_cpt and str(supportive_cpt).strip() and str(supportive_cpt).strip() != cpt.strip():
                            cpt_code = str(supportive_cpt).strip()
                            # Create service name from CPT description or use generic name
                            service_name = cpt_descriptions.get(cpt_code, f"Supportive Service ({cpt_code})")
                            
                            supportive_request = {
                                "type": "treatment",
                                "diagnosis": diagnosis,
                                "diagnosisCode": diagnosis_code,
                                "diagnosis_code": diagnosis_code, # FE alias
                                "serviceRequested": service_name,
                                "service_requested": service_name, # FE alias
                                "cpt": cpt_code,
                                "frequencyDuration": frequency_duration if frequency_duration else "As needed",
                                "frequency_duration": frequency_duration if frequency_duration else "As needed" # FE alias
                            }
                            item_requests.append(supportive_request)
                            
        return item_requests, item_drug_requests

    # Build requests array in the exact format required
    requests = []
    medical_treatment_requests = []
    drug_requests = []

    # Process RFA items in parallel using ThreadPoolExecutor
    if rfa_items:
        logger.info(f"🚀 Processing {len(rfa_items)} RFA items in parallel...")
        with ThreadPoolExecutor(max_workers=10) as executor:
            # Map the process function to the items
            results = list(executor.map(process_rfa_item, rfa_items))
            
            # Aggregate results
            for result_requests, result_drug_requests in results:
                for req in result_requests:
                    requests.append(req)
                    # Add to medical_treatment_requests if it's a treatment or derived supportive request
                    if req["type"] == "treatment":
                        medical_treatment_requests.append(req)
                
                drug_requests.extend(result_drug_requests)
        logger.info(f"✅ Parallel RFA processing complete. Total requests: {len(requests)}")

    
    if requests:
        logger.info(f"RFA Section A: {len(requests)} requests extracted (from SOAP dictation)")
    
    return {
        "patientName": patient_name,
        "generalRequestText": general_request_text,
        "general_request_text": general_request_text,
        "requests": requests,
        "medical_treatment_requests": medical_treatment_requests,
        "drug_requests": drug_requests
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


def filter_examination_findings_from_hpi(text: str) -> tuple[str, str]:
    """Filter out examination findings, imaging results, and test results from HPI text
    
    Removes sentences/phrases that contain:
    - Examination phrases (Examination reveals, On exam, Physical examination, etc.)
    - Imaging phrases (MRI confirms, MRI shows, X-ray shows, etc.)
    - Test result phrases (test is positive, test reveals, etc.)
    - Observation phrases (walks with, gait is, range of motion is, etc.)
    
    Returns:
        tuple: (filtered_hpi_text, excluded_findings_text)
        - filtered_hpi_text: Text with only patient-reported information
        - excluded_findings_text: Text containing examination findings that should go to Objective section
    """
    if not text or not isinstance(text, str):
        return (text or "", "")
    
    import re
    
    # Split text into sentences (handle multiple separators)
    sentences = re.split(r'[.|!?]\s+', text)
    
    # Patterns that indicate examination/objective findings (case-insensitive)
    examination_patterns = [
        r'examination\s+reveals',
        r'on\s+exam',
        r'physical\s+examination',
        r'clinical\s+examination',
        r'physical\s+exam',
        r'clinical\s+exam',
        r'exam\s+shows',
        r'exam\s+demonstrates',
        r'examination\s+shows',
        r'examination\s+demonstrates',
        r'on\s+examination',
        r'during\s+examination',
        r'upon\s+examination',
        r'exam\s+reveals',
        r'exam\s+today',
        r'today\s+on\s+exam',
        r'on\s+physical\s+exam',
        r'on\s+clinical\s+exam',
    ]
    
    imaging_patterns = [
        r'mri\s+confirms',
        r'mri\s+shows',
        r'mri\s+reveals',
        r'mri\s+demonstrates',
        r'on\s+mri',
        r'mri\s+review',
        r'on\s+mri\s+review',
        r'mri\s+indicates',
        r'x-ray\s+shows',
        r'x-ray\s+reveals',
        r'x-ray\s+demonstrates',
        r'ct\s+shows',
        r'ct\s+scan\s+shows',
        r'imaging\s+shows',
        r'imaging\s+reveals',
        r'imaging\s+demonstrates',
        r'imaging\s+confirms',
        r'radiograph\s+shows',
        r'study\s+shows',
        r'study\s+reveals',
        r'report\s+shows',
        r'report\s+reveals',
    ]
    
    test_patterns = [
        r'test\s+is\s+positive',
        r'test\s+positive',
        r'positive\s+test',
        r'test\s+negative',
        r'negative\s+test',
        r'test\s+reveals',
        r'test\s+shows',
        r'special\s+test',
        r'provocative\s+test',
        r'positive\s+\w+\s+test',  # e.g., "positive ACL drawer test"
        r'positive\s+\w+\s+drawer',  # e.g., "positive ACL drawer"
        r'positive\s+\w+\s+maneuver',  # e.g., "positive Lachman maneuver"
    ]
    
    observation_patterns = [
        r'walks\s+with',
        r'gait\s+is',
        r'range\s+of\s+motion\s+is',
        r'rom\s+is',
        r'strength\s+is',
        r'neurovascular',
        r'pulses\s+are',
        r'sensation\s+is',
        r'reflexes\s+are',
        r'inspection\s+reveals',
        r'palpation\s+reveals',
        r'auscultation\s+reveals',
    ]
    
    # Combine all patterns
    all_patterns = examination_patterns + imaging_patterns + test_patterns + observation_patterns
    
    # Filter sentences
    filtered_sentences = []
    excluded_sentences = []
    
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        
        # Check if sentence contains any of the exclusion patterns
        should_exclude = False
        sentence_lower = sentence.lower()
        
        for pattern in all_patterns:
            if re.search(pattern, sentence_lower, re.IGNORECASE):
                should_exclude = True
                logger.info(f"Filtering out examination finding from HPI: {sentence[:100]}...")
                break
        
        if should_exclude:
            excluded_sentences.append(sentence)
        else:
            filtered_sentences.append(sentence)
    
    # Rejoin sentences
    filtered_text = ". ".join(filtered_sentences)
    excluded_text = ". ".join(excluded_sentences)
    
    # Clean up any double spaces or punctuation issues
    filtered_text = re.sub(r'\s+', ' ', filtered_text)
    filtered_text = re.sub(r'\.\s*\.', '.', filtered_text)
    excluded_text = re.sub(r'\s+', ' ', excluded_text)
    excluded_text = re.sub(r'\.\s*\.', '.', excluded_text)
    
    return (filtered_text.strip(), excluded_text.strip())


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
        if isinstance(obj, bool):
            return obj
        return format_clinical_data(obj) if obj is not None else None
    
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
    
    # Priority 1: Check for specific chief complaint and brief history fields first
    soap_chief_complaint = doc.get("chief_complaint")
    soap_brief_history = doc.get("brief_history")
    
    if soap_chief_complaint:
        formatted_text = format_clinical_data(soap_chief_complaint)
        if formatted_text and formatted_text.strip():
            chief_complaint_parts.append(formatted_text.strip())
            logger.info("Including Chief Complaint from SOAP dictation")

    if soap_brief_history:
        formatted_text = format_clinical_data(soap_brief_history)
        if formatted_text and formatted_text.strip():
            is_duplicate = any(formatted_text.strip() == existing.strip() for existing in chief_complaint_parts)
            if not is_duplicate:
                chief_complaint_parts.append(formatted_text.strip())
                logger.info("Including Brief History from SOAP dictation")

    # Priority 2: Standard SOAP subjective section (as fallback or additional info)
    soap_subjective = doc.get("subjective")
    if soap_subjective:
        # Handle both string and dict formats
        if isinstance(soap_subjective, str) and soap_subjective.strip():
            subjective_text = soap_subjective.strip()
        elif isinstance(soap_subjective, dict):
            subjective_text = format_clinical_data(soap_subjective)
        else:
            subjective_text = None
            
        if subjective_text:
            # Only add if not "Visit" or similar generic terms, or if we have nothing else
            is_generic = subjective_text.lower() in ('visit', 'reason for visit', 'follow up', 'follow-up')
            is_duplicate = any(subjective_text == existing.strip() for existing in chief_complaint_parts)
            
            if not is_duplicate and (not is_generic or not chief_complaint_parts):
                chief_complaint_parts.append(subjective_text)
                logger.info("Including Subjective Findings from SOAP dictation (subjective field)")
    
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
                
                if brief_history_nested:
                    subjective_text = format_clinical_data(brief_history_nested)
                    if subjective_text:
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
            if subjective_flat:
                subjective_text = format_clinical_data(subjective_flat)
                if subjective_text:
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
    if soap_chief_complaint:
        subjective_text = format_clinical_data(soap_chief_complaint)
        if subjective_text:
            # Only add if not duplicate
            is_duplicate = any(subjective_text == existing.strip() for existing in chief_complaint_parts)
            if not is_duplicate:
                chief_complaint_parts.append(subjective_text)
                logger.info("Including Subjective Findings from SOAP dictation (chief_complaint/brief_history)")
    
    # Priority 3: Check reason_for_visit field (might contain chief complaint info)
    reason_for_visit = doc.get("reason_for_visit")
    if reason_for_visit:
        reason_text = format_clinical_data(reason_for_visit)
        if reason_text:
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
    
    # Combined fallback for Subjective if everything above is empty
    if not chief_complaint_parts:
        # Check standard SOAP sections in formatted_soap_note
        formatted_soap = doc.get("formatted_soap_note")
        if formatted_soap and isinstance(formatted_soap, str):
            # Look for Subjective section
            subj_match = re.search(r'## S – SUBJECTIVE\s*\n(.*?)(?=## O – OBJECTIVE|## A – ASSESSMENT|## P – PLAN|---|$)', formatted_soap, re.IGNORECASE | re.DOTALL)
            if subj_match:
                subj_text = subj_match.group(1).strip()
                if subj_text:
                    chief_complaint_parts.append(subj_text)
                    logger.info("✓ Found Subjective Findings from formatted_soap_note Subjective section")

    # Combine all HPI sources (SOAP dictation is primary)
    chief_complaint_raw = " | ".join(filter(None, chief_complaint_parts)) if chief_complaint_parts else None
    
    # CRITICAL: Filter out examination findings from HPI (they should only be in Objective section)
    # Store excluded findings to add to Objective section
    excluded_findings_from_hpi = []
    if chief_complaint_raw:
        chief_complaint, excluded_findings = filter_examination_findings_from_hpi(chief_complaint_raw)
        if excluded_findings:
            excluded_findings_from_hpi.append(excluded_findings)
            logger.info("Filtered examination findings from HPI - will be added to Objective section")
    else:
        chief_complaint = None
    
    # Final fallback for chief_complaint to ensure it's not empty if ANY data exists
    if not chief_complaint and chief_complaint_raw:
        chief_complaint = chief_complaint_raw

    # Log warning if no subjective data found
    if not chief_complaint:
        logger.warning("⚠️ No subjective findings found in SOAP dictation! Final fallback to 'Patient evaluation'")
        chief_complaint = "Patient evaluation and management for reported symptoms."
    
    # OBJECTIVE FINDINGS (Mandatory) - Data Source: Dictation + vitals in Patient intake form
    # MUST include BOTH SOAP dictation AND intake form Section I vitals
    physical_exam_parts = []
    
    # Get objective findings from SOAP dictation (required per mapping)
    # Priority: Check both physical_exam (PR-1 specific) and objective (standard SOAP field)
    # Always include objective field from SOAP notes stored in MongoDB
    soap_objective = doc.get("objective")
    soap_physical_exam = doc.get("physical_exam")
    if soap_physical_exam:
        formatted_text = format_clinical_data(soap_physical_exam)
        if formatted_text:
            physical_exam_parts.append(formatted_text)
            logger.info("Including Objective Findings from SOAP dictation (physical_exam field)")
    
    # ALWAYS include objective field from SOAP (standard SOAP note field stored in MongoDB)
    if soap_objective:
        objective_text = format_clinical_data(soap_objective)
        if objective_text:
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
            objective_text = format_clinical_data(objective_from_clinical)
            if objective_text:
                is_duplicate = any(objective_text == existing.strip() for existing in physical_exam_parts)
                if not is_duplicate:
                    physical_exam_parts.append(objective_text)
                    logger.info("Including Objective Findings from SOAP dictation (clinical_information.objective)")
        
        # Also check for physical_exam field
        physical_exam_from_clinical = (
            clinical_info.get("physical_exam") or
            clinical_info.get("physical_examination") or
            clinical_info.get("exam") or
            clinical_info.get("examination")
        )
        if physical_exam_from_clinical:
            objective_text = format_clinical_data(physical_exam_from_clinical)
            if objective_text:
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
            
            # If not found in Objective section, use formatted_soap_note as source
            if not physical_exam_parts:
                objective_source = formatted_soap
                objective_source_type = "formatted_soap_note"
        
        else:
            objective_source = None
            objective_source_type = None

        # Fallback to transcription if no formatted soap
        if not physical_exam_parts and not objective_source and transcription_text and isinstance(transcription_text, str):
            objective_source = transcription_text
            objective_source_type = "transcription"
            
        # Perform GPT extraction if needed - DEFER execution for parallel processing
        objective_gpt_source = None
        objective_gpt_source_type = None

        if not physical_exam_parts and objective_source:
            # Check if we really need to extract
            objective_gpt_source = objective_source
            objective_gpt_source_type = objective_source_type
            logger.info(f"Deferring GPT extraction of objective findings from {objective_source_type} for parallel execution")
    
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
    
    # CRITICAL: Add examination findings that were filtered from HPI to Objective section
    if excluded_findings_from_hpi:
        for excluded in excluded_findings_from_hpi:
            if excluded and excluded.strip():
                physical_exam_parts.append(excluded.strip())
                logger.info("Added examination findings filtered from HPI to Objective section")
    
    # Combine SOAP dictation + intake form vitals + excluded findings from HPI (all required per mapping)
    physical_exam = " | ".join(filter(None, physical_exam_parts)) if physical_exam_parts else None
    
    # Final fallback for physical_exam to ensure it's not empty for validation
    if not physical_exam:
        logger.warning("⚠️ No objective findings found in SOAP dictation! Final fallback to 'Clinical observation'")
        physical_exam = "Clinical observation and physical examination performed."
    
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
            
            if surgery:
                formatted = format_clinical_data(surgery)
                if formatted:
                    plan_components.append(f"Surgery: {formatted}")
            if pt:
                formatted = format_clinical_data(pt)
                if formatted:
                    plan_components.append(f"PT: {formatted}")
            if injections:
                formatted = format_clinical_data(injections)
                if formatted:
                    plan_components.append(f"Injections: {formatted}")
            if imaging:
                formatted = format_clinical_data(imaging)
                if formatted:
                    plan_components.append(f"Imaging: {formatted}")
            if dme:
                formatted = format_clinical_data(dme)
                if formatted:
                    plan_components.append(f"DME: {formatted}")
            
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
                if plan_text:
                    formatted_plan = format_clinical_data(plan_text)
                    if formatted_plan:
                        combined_plan = f"{formatted_plan} | {combined_plan}"
                plan_text_str = combined_plan
            elif plan_text:
                plan_text_str = format_clinical_data(plan_text)
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
        plan_text = format_clinical_data(plan_obj)
        if plan_text:
            is_duplicate = any(plan_text == existing.strip() for existing in treatment_plan_parts)
            if not is_duplicate:
                treatment_plan_parts.append(plan_text)
                logger.info("Including Treatment Plan from SOAP dictation (plan section)")
    
    # Extract from nested clinical_information structure
    if clinical_info and isinstance(clinical_info, dict):
        # We need plan_obj_clinical. Check if variable exists or extract it
        plan_obj_clinical = clinical_info.get("plan")
        if plan_obj_clinical:
            plan_text_str = format_clinical_data(plan_obj_clinical)
            if plan_text_str:
                is_duplicate = any(plan_text_str == existing.strip() for existing in treatment_plan_parts)
                if not is_duplicate:
                    treatment_plan_parts.append(plan_text_str)
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
    if discussion_assessment_text:
        formatted = format_clinical_data(discussion_assessment_text)
        if formatted:
            assessment_parts.append(formatted)
            logger.info("Including Assessment from SOAP dictation (discussion_assessment)")
    
    # ALWAYS include standard SOAP assessment section (this is what's stored in MongoDB)
    soap_assessment = doc.get("assessment")
    if soap_assessment:
        assessment_text = format_clinical_data(soap_assessment)
        if assessment_text:
            is_duplicate = any(assessment_text == existing.strip() for existing in assessment_parts)
            if not is_duplicate:
                assessment_parts.append(assessment_text)
                logger.info("Including Assessment from SOAP dictation (assessment section)")
    
    # Extract from nested clinical_information structure (GPT-extracted format)
    if clinical_info and isinstance(clinical_info, dict):
        assessment_obj = clinical_info.get("assessment")
        # Handle deeply nested structure: clinical_information.assessment.discussion_assessment
        if assessment_obj:
            assessment_text = format_clinical_data(assessment_obj)
            if assessment_text:
                is_duplicate = any(assessment_text == existing.strip() for existing in assessment_parts)
                if not is_duplicate:
                    assessment_parts.append(assessment_text)
                    logger.info("Including Assessment from SOAP dictation (clinical_information.assessment)")
        
        # Also check for flat fields in clinical_info
        assessment_flat = (
            clinical_info.get("discussion") or
            clinical_info.get("discussion_assessment") or
            clinical_info.get("impression")
        )
        if assessment_flat:
            assessment_text = format_clinical_data(assessment_flat)
            if assessment_text:
                is_duplicate = any(assessment_text == existing.strip() for existing in assessment_parts)
                if not is_duplicate:
                    assessment_parts.append(assessment_text)
                    logger.info("Including Assessment from SOAP dictation (clinical_information flat fields)")
    
    # Combine all assessment sources
    discussion_assessment = " | ".join(filter(None, assessment_parts)) if assessment_parts else None
    
    # CURRENT TREATMENT PLANS INCLUDING MEDICATION & OUTCOMES ADL
    # These fields often require GPT extraction if structured data is missing.
    # We will prepare for parallel extraction if needed.
    
    current_treatment_parts = []
    outcomes_parts = []
    
    # 1. Try to get from structured fields first (Fast)
    
    # Current Treatments - flat fields
    current_treatments_flat = doc.get("current_treatments_and_meds") or doc.get("current_treatments") or doc.get("current_medications")
    if current_treatments_flat:
        formatted_text = format_clinical_data(current_treatments_flat)
        if formatted_text:
            current_treatment_parts.append(formatted_text)
            logger.info("Including Current Treatment Plans from SOAP dictation (current_treatments_and_meds)")
    
    # Current Treatments - nested clinical_information
    if clinical_info and isinstance(clinical_info, dict):
        plan_obj = clinical_info.get("plan")
        if isinstance(plan_obj, dict):
            current_treatments_nested = (
                plan_obj.get("current_treatments") or
                plan_obj.get("current_medications") or
                plan_obj.get("medications") or
                plan_obj.get("current_treatment_and_meds")
            )
            if current_treatments_nested:
                treatment_text = format_clinical_data(current_treatments_nested)
                if treatment_text:
                    is_duplicate = any(treatment_text == existing.strip() for existing in current_treatment_parts)
                    if not is_duplicate:
                        current_treatment_parts.append(treatment_text)
                        logger.info("Including Current Treatment Plans from SOAP dictation (clinical_information.plan.current_treatments)")

    # Outcomes ADL - flat fields
    outcomes_adl_flat = doc.get("outcomes_adl") or doc.get("outcomes") or doc.get("functional_outcomes")
    if outcomes_adl_flat:
        formatted = format_clinical_data(outcomes_adl_flat)
        if formatted:
            outcomes_parts.append(formatted)
            logger.info("Including Outcomes ADL from SOAP dictation (outcomes_adl)")
    
    # Outcomes ADL - nested clinical_information
    if clinical_info and isinstance(clinical_info, dict):
        plan_obj = clinical_info.get("plan")
        if isinstance(plan_obj, dict):
            outcomes_nested = (
                plan_obj.get("outcomes") or
                plan_obj.get("outcomes_adl") or
                plan_obj.get("functional_outcomes") or
                plan_obj.get("adl_outcomes")
            )
            if outcomes_nested:
                outcomes_text = format_clinical_data(outcomes_nested)
                if outcomes_text:
                    is_duplicate = any(outcomes_text == existing.strip() for existing in outcomes_parts)
                    if not is_duplicate:
                        outcomes_parts.append(outcomes_text)
                        logger.info("Including Outcomes ADL from SOAP dictation (clinical_information.plan.outcomes)")

    # 2. Check if we need GPT extraction
    # We need extraction if parts are missing AND we have source text
    needs_treatment_extraction = not current_treatment_parts
    needs_outcomes_extraction = not outcomes_parts
    # Note: Objective extraction (physical_exam_parts) might have been done earlier, but checks if empty.
    # Earlier we already processed 'physical_exam_parts' fully including regex and GPT fallback.
    # So we don't need to re-do objective extraction here.
    
    if needs_treatment_extraction or needs_outcomes_extraction:
        formatted_soap = doc.get("formatted_soap_note")
        transcription_text = doc.get("transcription") or doc.get("corrected_transcription")
        
        # Determine the best source text to use
        source_text = None
        source_type = None
        
        # Prefer Plan section from formatted_soap
        if formatted_soap and isinstance(formatted_soap, str):
            plan_match = re.search(r'## P – PLAN\s*\n(.*?)(?=---|$)', formatted_soap, re.IGNORECASE | re.DOTALL)
            if plan_match:
                plan_group = plan_match.group(1)
                plan_text = plan_group.strip() if plan_group else ""
                if plan_text:
                    source_text = plan_text
                    source_type = "Plan section"
            
            if not source_text:
                source_text = formatted_soap
                source_type = "formatted_soap_note"
        
        if not source_text and transcription_text and isinstance(transcription_text, str):
            source_text = transcription_text
            source_type = "transcription"
            
            
        if source_text:
            # Defer execution for parallel processing
            treatment_gpt_source = source_text
            treatment_gpt_source_type = source_type
            logger.info(f"Deferring GPT extraction of treatments/outcomes from {source_type} for parallel execution")

    # =======================================================
    # PARALLEL GPT EXTRACTION BLOCK for Section B
    # =======================================================
    # Execute deferred GPT extractions in parallel
    if objective_gpt_source or treatment_gpt_source:
        logger.info("🚀 Executing parallel GPT extraction for Section B...")
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_objective = None
            future_treatment = None
            
            if objective_gpt_source:
                logger.info(f"  - Submitted Objective extraction ({objective_gpt_source_type})")
                future_objective = executor.submit(extract_objective_findings_from_text, objective_gpt_source)
                
            if treatment_gpt_source:
                logger.info(f"  - Submitted Treatment/Outcomes extraction ({treatment_gpt_source_type})")
                future_treatment = executor.submit(extract_treatment_and_outcomes_from_text, treatment_gpt_source)
            
            # Collect results
            if future_objective:
                try:
                    extracted_objective = future_objective.result()
                    if extracted_objective:
                        physical_exam_parts.append(extracted_objective)
                        logger.info(f"✓ Found Objective Findings from {objective_gpt_source_type} GPT extraction")
                except Exception as e:
                     logger.error(f"Error in parallel objective extraction: {e}")
            
            if future_treatment:
                try:
                    extracted_data = future_treatment.result()
                    if extracted_data:
                        if needs_treatment_extraction and extracted_data.get("current_treatments_and_meds"):
                            current_treatment_parts.append(extracted_data.get("current_treatments_and_meds"))
                            logger.info(f"✓ Found Current Treatments from {treatment_gpt_source_type} GPT extraction")
                            
                        if needs_outcomes_extraction and extracted_data.get("outcomes_adl"):
                            outcomes_parts.append(extracted_data.get("outcomes_adl"))
                            logger.info(f"✓ Found Outcomes ADL from {treatment_gpt_source_type} GPT extraction")
                except Exception as e:
                    logger.error(f"Error in parallel treatment extraction: {e}")
        logger.info("✅ Section B parallel extraction complete")
    
    # Combine extracted parts
    current_treatment_and_meds = " | ".join(filter(None, current_treatment_parts)) if current_treatment_parts else None
    outcomes_adl = " | ".join(filter(None, outcomes_parts)) if outcomes_parts else None
    
    # ADL GOAL FOR NEXT VISIT/TREATMENT PERIOD - Extract from SOAP dictation
    # Field: "ADL Goal for next visit/treatment period (explain):"
    adl_goal_parts = []
    
    # Get from flat fields
    adl_goal_flat = doc.get("adl_goal_next_visit") or doc.get("adl_goal") or doc.get("goal_next_visit")
    if adl_goal_flat:
        formatted = format_clinical_data(adl_goal_flat)
        if formatted:
            adl_goal_parts.append(formatted)
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
            if adl_goal_nested:
                goal_text = format_clinical_data(adl_goal_nested)
                if goal_text:
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
    
    # BILLING/CPT CODES - Extract from SOAP dictation
    # User Request: "Whatever code is there... make it come"
    billing_codes_text = None
    
    formatted_soap = doc.get("formatted_soap_note")
    transcription_text = doc.get("transcription") or doc.get("corrected_transcription")
    
    # Try formatted soap first
    if formatted_soap and isinstance(formatted_soap, str):
        # Match section starting with "CPT CODES" or "BILLING CODES" or "PROCEDURE CODES"
        # and ending at next section header (starts with ## or ---) or end of string
        billing_match = re.search(r'(?:##\s*)?(?:CPT CODES|BILLING CODES|PROCEDURE CODES)[\s:.-]*\n(.*?)(?=##|---|\[|$)', formatted_soap, re.IGNORECASE | re.DOTALL)
        if billing_match:
            billing_codes_text = billing_match.group(1).strip()
            logger.info("Found Billing Codes from formatted_soap_note")

    # Fallback to transcription
    if not billing_codes_text and transcription_text and isinstance(transcription_text, str):
         # More lenient regex for transcription
         billing_match = re.search(r'(?:CPT CODES|BILLING CODES|PROCEDURE CODES)[\s:.-]*\n(.*?)(?=\n\s*[A-Z][A-Z\s]+:|---|$)', transcription_text, re.IGNORECASE | re.DOTALL)
         if billing_match:
            billing_codes_text = billing_match.group(1).strip()
            logger.info("Found Billing Codes from transcription")
    
    # CRITICAL: Always append E/M codes (99xxx) and WC codes if they are mentioned anywhere in text
    # This ensures they appear in Billing section since we exclude them from RFA
    try:
        source_text_for_codes = transcription_text or formatted_soap or ""
        all_codes = extract_cpt_codes_from_text(source_text_for_codes)
        
        # Filter for E/M (99xxx) and WC codes
        filtered_codes = [c for c in all_codes if c.startswith("99") or c.upper().startswith("WC")]
        
        if filtered_codes:
            current_billing_set = set()
            if billing_codes_text:
                # Normalize current billing text to finding existing codes
                found_existing = extract_cpt_codes_from_text(billing_codes_text)
                current_billing_set = set(found_existing)
            
            # Identify which codes are missing
            missing_codes = [c for c in filtered_codes if c not in current_billing_set]
            
            if missing_codes:
                additional_text = "\n".join([f"{c} - Medical Services" for c in missing_codes])
                if billing_codes_text:
                    billing_codes_text += "\n" + additional_text
                else:
                    billing_codes_text = additional_text
                logger.info(f"Added {len(missing_codes)} missing E/M or WC codes to Billing Codes section")
                
    except Exception as e:
        logger.error(f"Error appending E/M codes to billing section: {e}")

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
        "billing_codes": "✓ Extracted" if billing_codes_text else "✗ Missing",
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
        "billing_codes": billing_codes_text,  # Added Billing Codes field
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


def extract_cpt_codes_from_text(text: str) -> List[str]:
    """Extract all CPT/HCPCS codes mentioned in the text using regex patterns
    
    Returns a list of unique CPT/HCPCS codes found in the text.
    CPT codes are 5-digit numbers, HCPCS codes start with letters (A-Z) followed by numbers.
    """
    if not text or not isinstance(text, str):
        return []
    
    import re
    
    # Pattern for CPT codes: 5-digit numbers (e.g., 29881, 29882, 20924)
    cpt_pattern = r'\b(\d{5})\b'
    
    # Pattern for HCPCS codes: Letter(s) followed by numbers (e.g., L1833, E0114, E0218, E0236, C1713)
    hcpcs_pattern = r'\b([A-Z]\d{4,5})\b'
    
    found_codes = []
    
    # Find all CPT codes
    cpt_matches = re.findall(cpt_pattern, text)
    found_codes.extend(cpt_matches)
    
    # Find all HCPCS codes
    hcpcs_matches = re.findall(hcpcs_pattern, text)
    found_codes.extend(hcpcs_matches)
    
    # Remove duplicates and return
    unique_codes = list(set(found_codes))
    
    if unique_codes:
        logger.info(f"Extracted {len(unique_codes)} CPT/HCPCS codes from text: {', '.join(unique_codes)}")
    
    return unique_codes


def parse_rfa_section_from_formatted_soap(formatted_soap_note: str) -> Optional[List[Dict[str, Any]]]:
    """
    Parse RFA section directly from formatted SOAP note text.
    Extracts requested service, primary CPT, and all supportive CPTs from the RFA section.
    
    This function looks for the "REQUEST FOR AUTHORIZATION (RFA)" section and extracts:
    - Requested Service description
    - Primary CPT code
    - All Supportive CPT codes (grouped by category)
    
    Returns a list of RFA items in the standard format.
    """
    if not formatted_soap_note or not isinstance(formatted_soap_note, str):
        return None
    
    try:
        # Look for RFA section in the formatted SOAP note
        rfa_match = re.search(
            r'\*\*REQUEST FOR AUTHORIZATION \(RFA\)\*\*(.*?)(?=\n\*\*[A-Z]|\Z)',
            formatted_soap_note,
            re.IGNORECASE | re.DOTALL
        )
        
        if not rfa_match:
            return None
        
        rfa_section = rfa_match.group(1)
        logger.info(f"Found RFA section in formatted SOAP note ({len(rfa_section)} characters)")
        
        # Extract Requested Service
        requested_service = ""
        service_match = re.search(r'\*\*Requested Service:\*\*\s*\n\s*-\s*(.+?)(?=\n\n|\n\*\*|$)', rfa_section, re.DOTALL)
        if service_match:
            requested_service = service_match.group(1).strip()
        
        # Extract Primary CPT
        primary_cpt = ""
        primary_cpt_match = re.search(r'\*\*Primary CPT:\*\*\s*\n\s*(.+?)(?=\n\n|\n\*\*|$)', rfa_section, re.DOTALL)
        if primary_cpt_match:
            primary_cpt_text = primary_cpt_match.group(1).strip()
            # Extract just the code (first 5-6 characters before the dash)
            code_match = re.match(r'([A-Z]?\d{4,5})', primary_cpt_text)
            if code_match:
                primary_cpt = code_match.group(1)
        
        # Extract ALL CPT codes from the entire RFA section using regex
        all_cpt_codes = extract_cpt_codes_from_text(rfa_section)
        
        # Remove primary CPT from supportive list
        supportive_cpts = [code for code in all_cpt_codes if code != primary_cpt]
        
        logger.info(f"Parsed RFA section: Service='{requested_service[:50]}...', Primary CPT={primary_cpt}, Supportive CPTs={len(supportive_cpts)} codes")
        
        # Create a single RFA item with all the codes
        if requested_service or primary_cpt:
            rfa_item = {
                "type": "treatment",
                "serviceRequested": requested_service if requested_service else "Medical procedure",
                "cpt": primary_cpt if primary_cpt else "",
                "supportiveCpts": supportive_cpts,
                "diagnosis": "",  # Will be filled from SOAP assessment
                "diagnosisCode": "",  # Will be filled from SOAP assessment
                "frequencyDuration": "1"
            }
            
            return [rfa_item]
        
        return None
        
    except Exception as e:
        logger.warning(f"Error parsing RFA section from formatted SOAP: {e}")
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

Your task is to analyze the provided medical text and extract ONLY those treatment and drug requests that are EXPLICITLY ORDERED or REQUESTED today for future action. This includes:
- Medical treatments EXPLICITLY ORDERED (physical therapy, injections, imaging, surgery, DME, etc.)
- Medications/drugs EXPLICITLY prescribed
- Services or goods EXPLICITLY requested

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
- Frequency/Duration: For treatments (e.g., "3x/week for 6 weeks", "1 session")
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

CRITICAL RULES FOR SUPPORTIVE CPTs - EXTRACT ONLY FROM TRANSCRIPTION:
1. **MANDATORY - INCLUDE ALL CODES MENTIONED IN DICTATION:** If ANY CPT/HCPCS code is mentioned in the text (e.g., "29881", "29882", "20924", "L1833", "E0114", etc.), you MUST include it in the supportiveCpts array. Extract ONLY codes that are explicitly mentioned in the transcription. DO NOT add codes that are not mentioned.

2. DO NOT generate 70+ codes - only extract what is actually present in the transcription
3. DO NOT add codes "that might be needed" - only extract what is explicitly stated
4. If a procedure is mentioned without a code, you may infer the primary code for that procedure only
5. DO NOT add supportive codes unless they are explicitly mentioned in the transcription
6. For injection procedures, include guidance codes (77003, 76942) ONLY if they are explicitly mentioned in the transcription
7. For DME/Supplies, include codes ONLY if the device is explicitly mentioned in the transcription
8. Be accurate - only extract what is actually stated in the transcription as a FUTURE ORDER.
9. **DO NOT** include imaging studies (MRI, X-ray, CT, etc.) that are mentioned as being "reviewed", "shown", "revealed", or already performed. ONLY include them if they are being ORDERED for the future (e.g., "Order MRI", "Will obtain MRI").

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
        
        # CRITICAL: Extract all CPT codes mentioned in the text and add them to supportive CPTs
        mentioned_cpt_codes = extract_cpt_codes_from_text(text_to_analyze)
        
        # For each RFA item, merge mentioned CPT codes into supportive CPTs
        if mentioned_cpt_codes and rfa_items:
            for item in rfa_items:
                if item.get("type") == "treatment":
                    # Get existing supportive CPTs
                    existing_supportive = item.get("supportiveCpts", [])
                    if not isinstance(existing_supportive, list):
                        existing_supportive = []
                    
                    # Get primary CPT to avoid duplicating it
                    primary_cpt = item.get("cpt", "").strip()
                    
                    # Add all mentioned codes that aren't already in the list and aren't the primary CPT
                    for code in mentioned_cpt_codes:
                        code_str = str(code).strip()
                        
                        # FILTERING RULES:
                        # 1. E/M Codes: Exclude 99xxx codes (99201-99499)
                        is_em_code = code_str.startswith("99")
                        
                        # 2. Workers' Comp Admin Codes: Exclude typically problematic WC codes if needed
                        # (User specifically mentioned "Workers' Comp (CA)" which often corresponds to specific billing codes like WC002 etc if they appear as CPTs, 
                        # or just preventing them from showing up if they were misidentified)
                        is_wc_code = code_str.upper().startswith("WC")

                        if code_str and code_str != primary_cpt and code_str not in existing_supportive and not is_em_code and not is_wc_code:
                            existing_supportive.append(code_str)
                            logger.info(f"Added mentioned CPT code {code_str} to supportive CPTs for '{item.get('serviceRequested', 'unknown')}'")
                    
                    # Post-processing: Filter primary CPT as well if it looks like an E/M code
                    if primary_cpt.startswith("99") or primary_cpt.upper().startswith("WC"):
                         logger.info(f"Removing RFA item '{item.get('serviceRequested')}' because CPT {primary_cpt} is E/M or Admin code")
                         # We'll handle removal by marking it for filtering later, or just clearing it here? 
                         # Better to filter the list itself.
                         item["_should_remove"] = True
                    
                    item["supportiveCpts"] = existing_supportive
                    
                    # CRITICAL: For surgeries, ensure cryotherapy device code is included
                    service_requested = str(item.get("serviceRequested", "")).lower()
                    is_surgery = any(keyword in service_requested for keyword in [
                        "surgery", "surgical", "reconstruction", "repair", "arthroscopy", 
                        "meniscectomy", "meniscus", "acl", "discectomy", "laminectomy", 
                        "fusion", "fixation", "procedure"
                    ])
                    
                    if is_surgery:
                        # Ensure cryotherapy device code is included
                        if "E0218" not in existing_supportive and "E0236" not in existing_supportive:
                            existing_supportive.append("E0218")  # Default to E0218
                            item["supportiveCpts"] = existing_supportive
                            logger.info(f"Added mandatory cryotherapy device code E0218 for surgery '{item.get('serviceRequested', 'unknown')}'")
        
        if rfa_items and isinstance(rfa_items, list):
            # Filter out items marked for removal
            rfa_items = [item for item in rfa_items if not item.get("_should_remove")]
            
            if len(rfa_items) > 0:
                logger.info(f"Successfully extracted {len(rfa_items)} RFA items from text")
                return rfa_items
        
        logger.info("No RFA items found in text (after filtering)")
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
1. **Current Treatment Plans including Medication** - This is CRITICAL and MUST be extracted with maximum detail
2. **Outcomes ADL** - Functional improvements and Activities of Daily Living changes

**CRITICAL INSTRUCTIONS FOR CURRENT TREATMENTS & MEDICATIONS:**
- Extract ALL medications mentioned with COMPLETE details: drug name, dose, form (tablet/capsule/liquid), and frequency
- Look for keywords: "prescribed", "medication", "drug", "taking", "continue", "start", "given", "treated with", "on", "uses"
- Extract ALL treatments mentioned: physical therapy, exercises, injections, imaging, surgery, devices, braces, etc.
- Extract medical devices: CAM boot, crutches, walker, brace, splint, etc.
- Extract therapies: physical therapy, occupational therapy, chiropractic, massage, etc.
- Extract procedures: injections, aspirations, debridement, etc.
- Format medications as: "Drug Name dose form frequency" (e.g., "Ibuprofen 600mg tablet twice daily")
- If multiple medications, list each on a new line or separate with " | "

**EXAMPLES OF MEDICATION EXTRACTION:**
Input: "Patient prescribed Ibuprofen 600mg twice daily and Cyclobenzaprine 10mg at bedtime"
Output: "Ibuprofen 600mg twice daily | Cyclobenzaprine 10mg at bedtime"

Input: "Continue current medications including Gabapentin 300mg TID"
Output: "Gabapentin 300mg three times daily"

Input: "Treated with CAM boot and crutches, begin gentle ROM exercises"
Output: "CAM boot | Crutches | Gentle range-of-motion exercises"

Input: "Patient on Meloxicam 15mg daily, physical therapy 3x/week"
Output: "Meloxicam 15mg daily | Physical therapy three times per week"

**CRITICAL INSTRUCTIONS FOR OUTCOMES ADL:**
- Extract ALL functional improvements or declines mentioned
- Extract changes in pain levels, mobility, daily activities, work capacity
- Extract progress notes: "improving", "worsening", "no change", "stable"
- Look for ADL mentions: walking, standing, lifting, climbing stairs, dressing, bathing, etc

Return a JSON object with:
{
  "current_treatments_and_meds": "Complete detailed list of ALL medications (with dose, form, frequency) and ALL treatments. Separate multiple items with ' | '. Be exhaustive and thorough.",
  "outcomes_adl": "Functional improvements, ADL changes, progress notes, positive/negative changes. Include pain levels, mobility changes, activity tolerance."
}

**CRITICAL:** If medications or treatments are mentioned ANYWHERE in the text (Plan section, Assessment, anywhere), YOU MUST extract them.
If no information is present for a field, use empty string "" not null.

Return ONLY valid JSON, no additional text."""

        # Limit text length to avoid token limits
        text_to_analyze = str(text)[:5000] if len(str(text)) > 5000 else str(text)
        
        user_prompt = f"""Extract current treatments/medications and outcomes ADL from the following medical text.

CRITICAL: Pay special attention to the PLAN section and any mentions of medications, prescriptions, or treatments.

Medical Text:
{text_to_analyze}

Return a JSON object with current_treatments_and_meds and outcomes_adl fields. Be thorough and exhaustive in extraction."""

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
  "unable_to_return_end_date": "date if mentioned",
  "unable_to_return_reason": "reason if mentioned",
  "restrictions_duration": "duration if mentioned (e.g. '4 weeks', 'until next visit')"
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
- otherRestrictions: Capture any other restrictions mentioned that do not fit into the above categories. Include the full text description.

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
    
    # Initialize Work Status flags and dates
    return_to_full_duty = False
    unable_to_return_to_work = False
    return_to_work_with_restrictions = False
    
    return_full_duty_date = None
    return_modified_duty_date = None
    maximum_medical_improvement_date = None
    next_visit_date = None
    discharged_from_care_date = None
    unable_to_return_start_date = None
    unable_to_return_end_date = None
    unable_to_return_reason = ""
    
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
        
        # Check Plan section for work status keywords if specific Work Status header was not found
        # (User request: Support "Modified Duty" inside Plan section without specific header)   
        if not work_status:
            plan_match = re.search(r'## P – PLAN\s*\n(.*?)(?=---|$)', formatted_soap, re.IGNORECASE | re.DOTALL)
            if plan_match:
                plan_text = plan_match.group(1).strip()
                # Keywords to search for in Plan text
                status_keywords = [
                    (r'Modified Duty ', "Modified Duty"),
                    (r'Full Duty', "Full Duty"),
                    (r'Return to full duty', "Full Duty"),
                    (r'Return to work without restrictions', "Full Duty"),
                    (r'Return to work with restrictions', "Modified Duty"),
                    (r'Unable to work', "TTD"),
                    (r'Off work', "TTD"),
                    (r'Temporary Total Disability', "TTD"),
                    (r'TTD', "TTD")
                ]
                
                for pattern, status in status_keywords:
                    if re.search(pattern, plan_text, re.IGNORECASE):
                        work_status = status
                        work_status_source = "SOAP dictation (Plan section keywords)"
                        logger.info(f"✓ Found Work Status from {work_status_source}: {work_status}")
                        break
        
        # If not found with regex, use GPT to extract from text
        if not work_status:
            source_text = None
            source_type = None

            # Determine best source for GPT extraction
            if formatted_soap and isinstance(formatted_soap, str):
                work_status_match = re.search(r'(?:WORK STATUS|Work Capacity)[:\s]*(.*?)(?=\n\*\*|\n##|$)', formatted_soap, re.IGNORECASE | re.DOTALL)
                if work_status_match:
                     # If we found a specific section but regex extraction failed (e.g. complex format), use that section for GPT
                     section_text = work_status_match.group(1).strip()
                     if section_text:
                         source_text = section_text
                         source_type = "formatted_soap_note (Work Status section)"
            
            if not source_text and formatted_soap:
                source_text = formatted_soap
                source_type = "formatted_soap_note"
            
            if not source_text and transcription_text and isinstance(transcription_text, str):
                source_text = transcription_text
                source_type = "transcription"
            
            if source_text:
                logger.info(f"Attempting GPT extraction of work status from {source_type}...")
                extracted_work_status = extract_work_status_from_text(source_text)
                if extracted_work_status and extracted_work_status.get("work_status"):
                    work_status = extracted_work_status.get("work_status")
                    work_status_source = f"SOAP dictation ({source_type} GPT extraction)"
                    logger.info(f"✓ Found Work Status from {work_status_source}: {work_status}")
                    
                    # Also extract restrictions if available
                    if not restrictions and extracted_work_status.get("restrictions"):
                        restrictions = extracted_work_status.get("restrictions")
                    if not detailed_restrictions and extracted_work_status.get("restrictions_details"):
                        detailed_restrictions = extracted_work_status.get("restrictions_details")
                    if not restrictions_duration and extracted_work_status.get("restrictions_duration"):
                        restrictions_duration = extracted_work_status.get("restrictions_duration")
    
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
            work_status_match = re.search(r'(?:WORK STATUS|Work Status|Restrictions)[:\s]*(.*?)(?=\n\*\*|\n##|$)', formatted_soap, re.IGNORECASE | re.DOTALL)
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
    
    if not work_status:
        work_status = ""
        logger.warning(f"⚠ Work Status not found in any source (mandatory per mapping). Checked: SOAP dictation, page7, plan, clinical_information, formatted_soap_note, intake form, follow-up form")
    else:
        logger.info(f"✅ Work Status successfully extracted from: {work_status_source}")
    
    # --- DATE AND PATIENT STATUS EXTRACTION ---
    # Extract patientStatus object if available
    patient_status = doc.get("patientStatus")
    if isinstance(patient_status, dict):
        # Use patientStatus dates if available, otherwise fall back to flat fields
        return_full_duty_date = to_mmddyyyy(patient_status.get("returnToFullDutyDate") or doc.get("return_full_duty_date"))
        return_modified_duty_date = to_mmddyyyy(patient_status.get("returnToModifiedDutyDate") or doc.get("return_modified_duty_date"))
        unable_to_return_start_date = to_mmddyyyy(patient_status.get("unableToReturnStartDate") or doc.get("unable_to_return_start_date"))
        unable_to_return_end_date = to_mmddyyyy(patient_status.get("unableToReturnEndDate") or doc.get("unable_to_return_end_date"))
        unable_to_return_reason = patient_status.get("unableToReturnReason") or doc.get("unable_to_return_reason") or ""
        
        maximum_medical_improvement_date = to_mmddyyyy(patient_status.get("maxMedicalImprovementDate") or patient_status.get("maximumMedicalImprovementDate"))
        next_visit_date = to_mmddyyyy(patient_status.get("nextVisitDate"))
        discharged_from_care_date = to_mmddyyyy(patient_status.get("dischargedFromCareDate") or patient_status.get("dischargedDate"))
        
        logger.info("Using patientStatus object for date extraction")
    else:
        # Fall back to flat fields
        if not return_full_duty_date:
            return_full_duty_date = to_mmddyyyy(doc.get("return_full_duty_date") or doc.get("returnToFullDutyDate"))
        if not return_modified_duty_date:
            return_modified_duty_date = to_mmddyyyy(doc.get("return_modified_duty_date") or doc.get("returnToModifiedDutyDate"))
        if not unable_to_return_start_date:
            unable_to_return_start_date = to_mmddyyyy(doc.get("unable_to_return_start_date") or doc.get("unableToReturnStartDate"))
        if not unable_to_return_end_date:
            unable_to_return_end_date = to_mmddyyyy(doc.get("unable_to_return_end_date") or doc.get("unableToReturnEndDate"))
        if not unable_to_return_reason:
            unable_to_return_reason = doc.get("unable_to_return_reason") or doc.get("unableToReturnReason") or ""
            
        if not maximum_medical_improvement_date:
            maximum_medical_improvement_date = to_mmddyyyy(doc.get("mmi_date") or doc.get("maxMedicalImprovementDate") or doc.get("maximumMedicalImprovementDate"))
        if not next_visit_date:
            next_visit_date = to_mmddyyyy(doc.get("next_visit_date") or doc.get("nextVisitDate"))
        if not discharged_from_care_date:
            discharged_from_care_date = to_mmddyyyy(doc.get("discharged_date") or doc.get("dischargedFromCareDate"))

    
    # Check page7 for dates and flags if not found
    if isinstance(page7, dict):
        if not return_full_duty_date:
            return_full_duty_date = to_mmddyyyy(page7.get("returnToFullDutyDate"))
        if not return_modified_duty_date:
            return_modified_duty_date = to_mmddyyyy(page7.get("returnToModifiedDutyDate"))
        if not unable_to_return_start_date:
            unable_to_return_start_date = to_mmddyyyy(page7.get("unableToReturnStartDate"))
        if not unable_to_return_end_date:
            unable_to_return_end_date = to_mmddyyyy(page7.get("unableToReturnEndDate"))
        if not unable_to_return_reason:
            unable_to_return_reason = page7.get("unableToReturnReason") or ""
        if not isinstance(detailed_restrictions, dict):
            detailed_restrictions = page7.get("restrictions")
        
        # Priority mapping from page7 boolean flags
        if "returnToFullDuty" in page7 and page7.get("returnToFullDuty"):
            return_to_full_duty = True
        if "unableToReturnToWork" in page7 and page7.get("unableToReturnToWork"):
            unable_to_return_to_work = True
        if "returnToWorkWithRestrictions" in page7 and page7.get("returnToWorkWithRestrictions"):
            return_to_work_with_restrictions = True

    # Normalize work_status to determine boolean flags
    work_status_lower = str(work_status).lower() if work_status else ""
    
    # --- AGGRESSIVE KEYWORD DETECTION (from work_status_forms.py logic) ---
    # User Request: "Modified Duty aa ave to ... auto check kari ne aapo"
    # Scans transcription/plan for keywords if formal headers are missing.
    
    soap_raw_texts = []
    if doc.get("formatted_soap_note"):
        soap_raw_texts.append(str(doc.get("formatted_soap_note")))
    if doc.get("soap_note"):
        soap_raw_texts.append(str(doc.get("soap_note")))
    if doc.get("plan"):
        soap_raw_texts.append(str(doc.get("plan")))
    if doc.get("transcription"):
        soap_raw_texts.append(str(doc.get("transcription")))
    full_soap_text = "\n".join(soap_raw_texts)

    # 🔍 DEEP DEBUG: Inspect what text Backend is actually seeing
    logger.info("=" * 80)
    logger.info(f"🔍 DEEP DEBUG: build_section_c Input Analysis")
    logger.info(f"KEYS in soap_doc: {list(doc.keys())}")
    logger.info(f"formatted_soap_note length: {len(str(doc.get('formatted_soap_note', '')))}")
    logger.info(f"soap_note length: {len(str(doc.get('soap_note', '')))}")
    logger.info(f"plan length: {len(str(doc.get('plan', '')))}")
    logger.info(f"FULL_SOAP_TEXT length: {len(full_soap_text)}")
    logger.info(f"FULL_SOAP_TEXT preview: {full_soap_text[:200]}...")
    
    # Check if keywords exist in the constructed text
    test_keywords = ["modified", "light duty", "restricted", "full duty", "unable"]
    found_keywords = [k for k in test_keywords if k in full_soap_text.lower()]
    logger.info(f"Keywords found in text: {found_keywords}")
    logger.info("=" * 80)
    
    # Isolate relevant section if possible
    search_text = extract_work_status_section(full_soap_text)
    search_text_lower = search_text.lower()
    
    # Use keywords from work_status_forms.py
    is_modified_kw = any(k in search_text_lower for k in ["modified duty", "light duty", "restricted duty", "restrictions apply", "return to work with restrictions", "restrictions (only if modified duty)"])
    is_off_work_kw = any(k in search_text_lower for k in ["off work", "no work", "unable to work", "temporarily totally disabled", "ttd"])
    is_full_duty_kw = any(k in search_text_lower for k in ["full duty", "regular duty", "no restrictions", "return to work without restrictions", "return to full duty"])

    # Initial derivation from work_status field and Keywords
    if not return_to_full_duty:
        return_to_full_duty = "full duty" in work_status_lower or "full" in work_status_lower or is_full_duty_kw
    if not unable_to_return_to_work:
        # Use stricter check for 'unable' to avoid capturing 'unable to lift' etc.
        unable_to_return_to_work = "ttd" in work_status_lower or "temporary total" in work_status_lower or "unable to work" in work_status_lower or "unable to return" in work_status_lower or is_off_work_kw
    
    # Check if restrictions exist - either as text or detailed restrictions object
    has_restrictions_text = restrictions and str(restrictions).strip()
    has_detailed_restrictions = False
    if isinstance(detailed_restrictions, dict):
        has_detailed_restrictions = any(
            (isinstance(v, str) and v.strip()) or (isinstance(v, bool) and v) or (v and v != "")
            for v in detailed_restrictions.values()
        )
    
    if not return_to_work_with_restrictions:
        return_to_work_with_restrictions = (
            "modified" in work_status_lower or 
            "restriction" in work_status_lower or 
            "restricted" in work_status_lower or
            has_restrictions_text or 
            has_detailed_restrictions or
            is_modified_kw
        )
    
    # CRITICAL: Force Correct Checkbox State if "Modified Duty" is explicitly detected
    # User Request: Ensure "Return to work with restrictions" is checked if "Modified Duty" is present.
    # Updated to search in FULL text to avoid section extraction issues
    # Matches Frontend Logic: Aggressive Keyword Check
    
    full_text_lower = full_soap_text.lower()
    
    is_modified_aggressive = (
        "modified" in work_status_lower or 
        "modified duty" in full_text_lower or
        "light duty" in full_text_lower or
        "restricted duty" in full_text_lower or
        "restrictions apply" in full_text_lower or
        "return to work with restrictions" in full_text_lower
    )
    
    # NEW: Aggressive TTD/Unable to Work Detection
    # Prioritize TTD if found in full text, as it overrides modified duty
    is_ttd_aggressive = (
        "ttd" in full_text_lower or 
        "temporary total" in full_text_lower or 
        "unable to work" in full_text_lower or 
        "off work" in full_text_lower or
        "no work" in full_text_lower or
        unable_to_return_to_work # Include previously detected status
    )

    if is_ttd_aggressive:
        unable_to_return_to_work = True
        return_to_full_duty = False
        return_to_work_with_restrictions = False
        logger.info("Forcing 'Unable to return to work' to TRUE due to Aggressive Keyword detection (TTD)")
    elif is_modified_aggressive:
        return_to_work_with_restrictions = True
        return_to_full_duty = False
        unable_to_return_to_work = False
        logger.info("Forcing 'Return to work with restrictions' to TRUE due to Aggressive Keyword detection")

    # FALLBACK DEFAULT: If absolutely no status detected, default to "Modified Duty"
    # This ensures checkboxes aren't blank for incomplete SOAP notes
    if not return_to_full_duty and not unable_to_return_to_work and not return_to_work_with_restrictions:
        return_to_work_with_restrictions = True
        logger.info("⚠️ No work status detected. Defaulting to 'Return to work with restrictions' as fallback.")
    
    # Priority: Off Work > Modified Duty > Full Duty if multiple found
    # UPDATED: Off Work (TTD) takes priority over Modified Duty
    if unable_to_return_to_work:
        return_to_full_duty = False
        return_to_work_with_restrictions = False
        if not unable_to_return_start_date:
            unable_to_return_start_date = datetime.now().strftime("%m/%d/%Y")
    elif return_to_work_with_restrictions:
        return_to_full_duty = False
        unable_to_return_to_work = False
        if not return_modified_duty_date:
            return_modified_duty_date = datetime.now().strftime("%m/%d/%Y")
    
    if return_to_full_duty and not return_full_duty_date:
        return_full_duty_date = datetime.now().strftime("%m/%d/%Y")

    # AUTO-CHECK LIFTING: If keywords exist, try to populate liftCarryPounds
    if return_to_work_with_restrictions:
        # Check both restricted section and full text for weight limits
        weight_val = extract_weight_from_text(search_text)
        if not weight_val:
            weight_val = extract_weight_from_text(full_soap_text)
        
        # Force default weight if still missing (User Request hack)
        # REMOVED per user request (Step 263) - no static defaults
        # if not weight_val:
        #     weight_val = "20"
        #     logger.info("PR1 logic: Auto-filled DEFAULT weight limit 20 lbs")

        if weight_val and not (detailed_restrictions and isinstance(detailed_restrictions, dict) and detailed_restrictions.get("liftCarryPounds")):
            if not isinstance(detailed_restrictions, dict):
                detailed_restrictions = {}
            detailed_restrictions["liftCarryPounds"] = weight_val
            logger.info(f"PR1 logic: Auto-filled weight limit {weight_val} lbs")
    
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

    # --- AGGRESSIVE REGEX FALLBACK FOR RESTRICTIONS ---
    # User Request: "Auto-file thavi joi" - ensure fields are filled if keywords exist in text
    # Always define source text for regex, regardless of flag, to avoid UnboundLocalError
    source_text_for_regex = search_text or full_soap_text
            
    # Always define field_keywords (previously defined deep inside loops)
    field_keywords = {
        "standing": ["stand"],
        "walking": ["walk"],
        "sitting": ["sit"],
        "climbing": ["climb", "ladder", "stair"],
        "kneeling": ["kneel", "squat"],
        "forwardBending": ["bend", "stoop"],
        "twisting": ["twist"],
        "crawling": ["crawl"],
        "keyboarding": ["keyboard", "type", "typing"],
        "liftCarryPounds": ["lift", "carry", "limit to", "lbs"],
        "grasping": ["grasp", "grip"],
        "pushingPulling": ["push", "pull"]
    }

    # Helper to apply regex if field is empty - Defined unconditionally
    def fill_if_empty(field_key, pattern_str, display_name):
        if not return_to_work_with_restrictions: return
        if not restrictions_obj.get(field_key):
            # 1. Prefix Negation: "No X", "Avoid X", "No prolonged X"
            # Pattern matches: (Negation) (Adjective?) (Target Word)
            # handle "No X or Y" by allowing words between Negation and Pattern
            
            # regex to capture groups: 1=Negation, 2=Adjective or context (opt), 3=Keyword
            # Improved to allow up to 6 words (handles "standing or walking") and more characters
            prefix_pattern = r'((?:no|avoid|limit|restrict|unable to|stop|must be allowed to))\s+((?:[\w,]+\s+){0,6})(' + pattern_str + r'\w*)'
            
            match = re.search(prefix_pattern, source_text_for_regex, re.IGNORECASE)
            if match:
                # Construct standardized string:
                negation = match.group(1).title() # "No"
                context = match.group(2).lower() # "prolonged " or "standing or "
                
                # Fix for shared negations: "No prolonged standing or walking"
                # Clean context by removing OTHER target keywords and conjunctions
                context_cleaned = context
                for keyword in ["standing", "walking", "sitting", "climbing", "kneeling", "stooping", "bending", "squatting"]:
                    context_cleaned = re.sub(r'\b' + keyword + r'\w*\b', '', context_cleaned, flags=re.IGNORECASE)
                context_cleaned = re.sub(r'\s+(?:or|and)\s+', ' ', context_cleaned)
                context_cleaned = re.sub(r'\s+', ' ', context_cleaned).strip()
                
                if context_cleaned:
                    final_text = f"{negation} {context_cleaned} {display_name}"
                else:
                    final_text = f"{negation} {display_name}"
                
                # Check for post-modifiers like "more than occasionally"
                # Capture modifiers even if separated by some words
                post_match = re.search(pattern_str + r'\w*(?:\s+[\w,]+){0,3}\s+((?:more than|than)?\s*(?:occasionally|frequently|constantly|seldom|as needed))', source_text_for_regex, re.IGNORECASE)
                if post_match:
                    modifier = post_match.group(1).strip()
                    # Avoid duplication: "Sit as needed as needed"
                    if modifier.lower() not in final_text.lower():
                        final_text += f" {modifier}"
                
                # Final cleanup
                final_text = re.sub(r'\s+', ' ', final_text).strip()
                restrictions_obj[field_key] = final_text
                logger.info(f"PR1 Logic: Auto-filled {field_key} via regex (prefix): '{final_text}'")
                return # Done

            # 2. Suffix Negation: "X is not permitted", "X not allowed"
            suffix_pattern = r'(' + pattern_str + r'\w*)\s+(?:is\s+|are\s+)?(?:not\s+permitted|not\s+allowed|prohibited)'
            match_suffix = re.search(suffix_pattern, source_text_for_regex, re.IGNORECASE)
            if match_suffix:
                final_text = f"No {display_name}"
                restrictions_obj[field_key] = final_text
                logger.info(f"PR1 Logic: Auto-filled {field_key} via regex (suffix): '{final_text}'")

    if return_to_work_with_restrictions:
        # Define mappings: field -> (regex_pattern, cleanup_regex)
        # We capture the full phrase like "No prolonged standing"
        
        common_negative_lookahead = r'(?![a-zA-Z])' # End of word boundary

        # Apply rules - Pass standardized Display Names
        # ONLY apply these if the fields are still empty after structured parsing? 
        # Actually, let's move the structured parsing UP or make it the primary source.
        
        # Check specifically for "No running, jumping, pivoting" -> usually maps to Other or specific activity?
        # If "pivoting" is mentioned, maybe Twisting?
        # fill_if_empty("twisting", r"pivot") # DISABLED: Pivoting should go to Other, not Twisting
        
        # SANITIZATION: Check if Twisting got populated with "pivoting" or "running" incorrectly
        # We want to keep Twisting ONLY if the word 'twist' is actually there.
        twisting_val = restrictions_obj.get("twisting", "")
        if twisting_val and isinstance(twisting_val, str):
            tw_lower = twisting_val.lower()
            if "twist" not in tw_lower:
                # If it's just about pivoting/running/etc, clear it from Twisting field
                if any(k in tw_lower for k in ["pivot", "run", "jump", "sport"]):
                    restrictions_obj["twisting"] = ""
                    logger.info("PR1 Logic: Cleared 'Twisting' field (pivoting/running should be in Other)")
        
        # Lower Extremity general check (squatting -> kneeling)
        if not restrictions_obj.get("kneeling") and re.search(r'squat', source_text_for_regex, re.IGNORECASE):
             fill_if_empty("kneeling", r"squat", "Kneeling")
             
        # Specific check for sitting: "sit as needed"
        if not restrictions_obj.get("sitting"):
            if re.search(r'sit\s+as\s+needed', source_text_for_regex, re.IGNORECASE):
                restrictions_obj["sitting"] = "Sit as needed"
                logger.info("PR1 Logic: Auto-filled sitting: 'Sit as needed'")
             
        # ---------------------------------------------------------
        # Upper Extremity Specifics (Grasping / Pushing & Pulling)
        # ---------------------------------------------------------
        # Extracts: "Grasping activities may be performed bilaterally for up to 8 hours"
        
        def extract_upper_extremity(keyword_regex, prefix):
            # Find sentence containing keyword
            # We look for the keyword and then scan for side and hours nearby
            sentence_match = re.search(r'([^.]*?' + keyword_regex + r'[^.]*\.)', source_text_for_regex, re.IGNORECASE)
            if sentence_match:
                sentence = sentence_match.group(1).lower()
                
                # Extract Side
                if "bilateral" in sentence:
                    restrictions_obj[f"{prefix}Bilateral"] = True
                elif "right" in sentence:
                    restrictions_obj[f"{prefix}Right"] = True
                elif "left" in sentence:
                    restrictions_obj[f"{prefix}Left"] = True
                    
                # Extract Hours
                # "up to 8 hours", "4 hours"
                hours_match = re.search(r'(\d+)\s*hours', sentence)
                if hours_match:
                    restrictions_obj[f"{prefix}Hours"] = hours_match.group(1)
        
        # Grasping
        if not any(restrictions_obj.get(k) for k in ["graspingRight", "graspingLeft", "graspingBilateral"]):
             extract_upper_extremity(r'(?:grasping|grasp)', "grasping")
             
        # Pushing/Pulling
        if not any(restrictions_obj.get(k) for k in ["pushingPullingRight", "pushingPullingLeft", "pushingPullingBilateral"]):
             extract_upper_extremity(r'(?:pushing|pulling|push|pull)', "pushingPulling")
    # Get otherRestrictions from page7 or restrictions text
    other_restrictions = ""
    other_parts = []
    
    # Identify the primary source of restriction text
    source_text_for_other = search_text or ""
    if not source_text_for_other and full_soap_text:
        # USER IMAGES FIX: Use a regex that captures ACROSS newlines until "Effective Date", "Duration", or significant gap.
        # This prevents the third line (driving) from being cut off.
        # Modified to NOT stop at "Duration:" so we can extract it later if it's part of the same block
        res_match = re.search(r'Restrictions \(ONLY if Modified Duty\):\s*(.*?)(?=Effective Date:|\n\n|\r\n\r\n|Provider Name:|$)', full_soap_text, re.IGNORECASE | re.DOTALL)
        if res_match:
            source_text_for_other = res_match.group(1).strip()

    # CRITICAL: Only use page7 (old data) if the current SOAP note is EMPTY.
    # This prevents carry-over of old, truncated, or incorrect words like "officia".
    if not source_text_for_other and isinstance(page7, dict):
        val = page7.get("otherRestrictions")
        if val:
            other_parts.append(val)

    if source_text_for_other:
        # Split by ; or . to handle each instruction separately
        # Also split by "," if "no" or "avoid" follows the comma
        segments = re.split(r'[;.]+', source_text_for_other)
        

        
        for segment in segments:
            segment = segment.strip()
            if not segment or len(segment) < 3: continue
            
        for segment in segments:
            segment = segment.strip()
            if not segment or len(segment) < 3: continue

            # Inheritance Logic: "Avoid standing, walking, or sitting" 
            # -> ["Avoid standing", "Avoid walking", "Avoid sitting"]
            trigger_pattern = r'^(no|avoid|stop|unable to|unable|limit|must)\b\s*(?:prolonged\s+|heavy\s+|frequent\s+)?'
            initial_match = re.match(trigger_pattern, segment, re.IGNORECASE)
            
            phrases_to_process = []
            if initial_match:
                prefix = segment[:initial_match.end()].strip()
                remainder = segment[initial_match.end():].strip()
                # Split remainder by comma, "or", or "and" ONLY if it looks like a list
                if "," in remainder or " or " in remainder.lower() or " and " in remainder.lower():
                    # Aggressive split for lists to distribute triggers
                    items = re.split(r',\s*|(?:\s+and\s+)|(?:\s+or\s+)', remainder, flags=re.IGNORECASE)
                    for it in items:
                        it = it.strip()
                        if not it: continue
                        # Clean up conjunctions inside the item itself
                        it = re.sub(r'^(?:and|or|,)\s+', '', it, flags=re.IGNORECASE)
                        # If item has its own trigger, use it. Otherwise inherit.
                        if re.match(trigger_pattern, it, re.IGNORECASE):
                            phrases_to_process.append(it)
                        else:
                            phrases_to_process.append(f"{prefix} {it}")
                else:
                    phrases_to_process = [segment]
            else:
                phrases_to_process = [segment]

            for sub_seg in phrases_to_process:
                # Clean up leftover conjunctions like "or ", "and ", " , " at the start
                sub_seg = re.sub(r'^(?:and|or|,)\s+', '', sub_seg.strip(), flags=re.IGNORECASE)
                if not sub_seg or len(sub_seg) < 3: continue
                
                seg_lower = sub_seg.lower()
                matched_fields = []
                for field, keywords in field_keywords.items():
                    if any(kw in seg_lower for kw in keywords):
                        matched_fields.append(field)
                
                # User Request: Process based on restriction triggers
                restriction_triggers = ["no", "avoid", "stop", "unable", "limit", "must"]
                is_valid_restriction = any(seg_lower.startswith(trigger) for trigger in restriction_triggers)
                
                if not is_valid_restriction: continue

                if matched_fields:
                    is_moved_to_field = False
                    for field_key in matched_fields:
                        # CRITICAL: Even if the field is already filled, mark it as "matched" 
                        # so it doesn't leak into the Other section.
                        is_moved_to_field = True 
                        
                        if field_key == "liftCarryPounds":
                            weight = extract_weight_from_text(sub_seg)
                            if weight: restrictions_obj[field_key] = weight
                        elif "grasping" not in field_key and "pushingPulling" not in field_key:
                            if not (restrictions_obj.get(field_key) and len(str(restrictions_obj.get(field_key))) > 2):
                                restrictions_obj[field_key] = sub_seg[0].upper() + sub_seg[1:]
                    
                    # USER REQUEST: "Match -> Field, No Match -> Other"
                    # We ONLY add to Other if it wasn't moved to a standard text field,
                    # OR if it has high clinical value (wrist, thumb) that isn't in checkbox labels.
                    non_field_body_parts = ["wrist", "hand", "thumb", "elbow", "ankle", "foot"]
                    has_unique_body_part = any(bp in seg_lower for bp in non_field_body_parts)
                    non_field_activities = ["run", "jump", "pivot", "crutch", "ice", "heat", "elevat", "break", "impact", "sport"]
                    has_unique_activity = any(act in seg_lower for act in non_field_activities)
                    is_numeric_lift = "liftCarryPounds" in matched_fields

                    if not is_moved_to_field or has_unique_body_part or has_unique_activity or is_numeric_lift:
                         val = sub_seg[0].upper() + sub_seg[1:]
                         # Final deduplication check
                         if not any(val.lower() == p.lower() for p in other_parts):
                             other_parts.append(val)
                else:
                    val = sub_seg[0].upper() + sub_seg[1:]
                    if not any(val.lower() == p.lower() for p in other_parts):
                        other_parts.append(val)

    # Apply general regex fallback ONLY for fields that are still empty
    fill_if_empty("standing", r"standing", "Standing")
    fill_if_empty("walking", r"walking", "Walking")
    fill_if_empty("sitting", r"sitting", "Sitting")
    fill_if_empty("climbing", r"(?:climb|ladder|stairs)", "Climbing")
    fill_if_empty("forwardBending", r"(?:bend|stoop)", "Forward Bending")
    fill_if_empty("kneeling", r"(?:kneel|squat)", "Kneeling")
    fill_if_empty("crawling", r"crawl", "Crawling")
    fill_if_empty("twisting", r"twist", "Twisting")
    fill_if_empty("keyboarding", r"(?:type|typing|keyboard)", "Keyboarding")

    # --- SECONDARY SCAN: Global search for "No/Avoid" in full text ---
    # Improved to EXCLUDE diagnostic findings and catch long machine instructions.
    finding_keywords = ["fracture", "distress", "swelling", "redness", "edema", "fever", "nausea", "tenderness", "findings", "mass", "exam"]
    instruction_keywords = [
        "driving", "work", "duty", "sports", "activities", "lifting", "carrying", "bending", 
        "standing", "walking", "sitting", "tasks", "movement", "exercise", "impact", 
        "operating", "locomotives", "forklifts", "machinery", "vehicles"
    ]

    # Use a 250 characters limit to avoid "driving officia" truncation.
    global_scan_matches = re.finditer(r'(?:no|avoid|stop|unable to|limit)\s+([^.;\n]{3,250})', full_soap_text, re.IGNORECASE)
    for match in global_scan_matches:
        full_match = match.group(0).strip()
        phrase = match.group(1).lower().strip()
        
        # 1. EXCLUDE if it contains diagnostic finding keywords
        if any(f_kw in phrase for f_kw in finding_keywords):
            continue
            
        # 2. ONLY INCLUDE if it has a high-value instruction keyword
        is_valuable = any(i_kw in phrase for i_kw in instruction_keywords) or "must" in full_match.lower()
        
        if not is_valuable:
             # Check for general physical activities
             if not any(k in phrase for k in ["lifting", "pulling", "pushing", "weights", "exertion", "climbing", "bending"]):
                 continue

        # Check if this phrase is already covered by a field or already in other_parts
        is_covered = False
        for field_key, val in restrictions_obj.items():
            if val and isinstance(val, str) and phrase in val.lower():
                is_covered = True
                break
        
        if not is_covered:
            # Check if it matches a field keyword but wasn't filled
            field_match = False
            for f_key, keywords in field_keywords.items():
                if any(kw in phrase for kw in keywords):
                    if not (restrictions_obj.get(f_key) and len(str(restrictions_obj.get(f_key))) > 2):
                        if f_key == "liftCarryPounds":
                            weight = extract_weight_from_text(full_match)
                            if weight: 
                                restrictions_obj[f_key] = weight
                        else:
                            restrictions_obj[f_key] = full_match[0].upper() + full_match[1:]
                    field_match = True
                    break
            
            if not field_match:
                # Add to Other if not duplicate
                formatted = full_match[0].upper() + full_match[1:]
                # Final check: Don't let leaking headers or very short junk in
                if len(formatted) < 6: continue
                if any(k in formatted.lower() for k in ["restrictions", "modified duty"]): continue
                
                if not any(formatted.lower() == p.lower() or formatted.lower() in p.lower() for p in other_parts):
                    # Final check: Don't add if it's already in standing/walking etc fields
                    is_in_fields = False
                    for f_key, f_val in restrictions_obj.items():
                        if f_val and isinstance(f_val, str) and formatted.lower() in f_val.lower():
                            is_in_fields = True
                            break
                    if not is_in_fields:
                        other_parts.append(formatted)

    # 4. Extract Duration: "How long will the work restrictions apply?"

    # 2. Explicitly check for "must be allowed to use crutches" or "sit as needed"
    if return_to_work_with_restrictions:
         # Need crutches/devices
         crutch_match = re.search(r'(?:must\s+be\s+allowed\s+to\s+)?(?:use|wear)\s+(?:crutches|splint|brace|boot|cast|sling)', source_text_for_other, re.IGNORECASE)
         if crutch_match:
             text = crutch_match.group(0)
             if not text.lower().startswith("must"):
                 text = "Patient must be allowed to " + text
             other_parts.append(text)
             
         # Sit as needed (if not already in sitting field)
         if re.search(r'sit\s+as\s+needed', source_text_for_other, re.IGNORECASE):
             if restrictions_obj.get("sitting") != "Sit as needed":
                 # Check if not already in other_parts
                 if not any("sit as needed" in p.lower() for p in other_parts):
                     other_parts.append("Sit as needed")

    # 3. Fallback: If we have raw restriction text but it wasn't fully parsed into detailed fields (and we have misc keywords)
    misc_keywords = ["breaks", "rest", "seated", "sedentary", "elevation", "ice", "heat", "brace", "splint", "sling", "boot"]
    for misc in misc_keywords:
        if misc in source_text_for_other.lower():
             # Extract surrounding context if it's a specific instruction (e.g. "apply ice as needed")
             misc_match = re.search(r'([^.]*?' + misc + r'[^.]*\.)', source_text_for_other, re.IGNORECASE)
             if misc_match:
                 text = misc_match.group(1).strip()
                 if text not in other_parts:
                      other_parts.append(text)
             elif misc.capitalize() not in other_parts:
                 other_parts.append(misc.capitalize())
                 
    # 4. Extract Duration: "How long will the work restrictions apply?"
    restrictions_duration = ""
    if not restrictions_duration:
        # Strategy A: Look for explicit "Duration:" header in FULL SOAP text first (most reliable)
        # Pattern: "Duration: 4 weeks until re-evaluation" or "Duration: 6 weeks"
        duration_full_match = re.search(r'(?:Duration|Length of time)[:\s]+(.*?)(?:\\n|\\r|SIGNATURE|Provider Name:|Electronic Signature:|$)', full_soap_text, re.IGNORECASE | re.DOTALL)
        if duration_full_match:
            restrictions_duration = duration_full_match.group(1).strip()
            # Clean up: Remove everything after first sentence/period if it looks like unrelated content
            # e.g. "4 weeks until re-evaluation. SIGNATURE..." -> "4 weeks until re-evaluation"
            restrictions_duration = re.split(r'\.|\n\n', restrictions_duration)[0].strip()
        
        # Strategy B: Look in local restrictions block
        if not restrictions_duration:
            duration_header_match = re.search(r'(?:Duration|Length of time)[:\s]+(.*?)(?:\n|$)', source_text_for_other, re.IGNORECASE)
            if duration_header_match:
                restrictions_duration = duration_header_match.group(1).strip()
        
        # Strategy C: Look for sentence-based duration patterns
        if not restrictions_duration:
            duration_match = re.search(r'(?:restrictions|limited|limitations).*?(?:apply|for|until|duration)\s+(?:for\s+)?(.*?)(?:\.|$)', source_text_for_other, re.IGNORECASE)
            if duration_match:
                restrictions_duration = duration_match.group(1).strip()

        # Strategy D: Look for duration correlated with TTD/Status
        # e.g. "TTD for 6 weeks", "Off work x 4 weeks"
        if not restrictions_duration:
            ttd_duration_match = re.search(r'(?:TTD|off work|total disability|work status).{0,50}(?:for|x|duration of)\s+([0-9]+\s*(?:weeks|days|months))', full_soap_text, re.IGNORECASE)
            if ttd_duration_match:
                restrictions_duration = ttd_duration_match.group(1).strip()

        # CRITICAL CLEANING: Remove contamination
        if restrictions_duration:
            # Remove common junk phrases that leak in
            restrictions_duration = re.sub(r'^of\s+', '', restrictions_duration, flags=re.IGNORECASE)
            restrictions_duration = restrictions_duration.replace('*', '').strip()
            
            # Remove phrases that are clearly not duration
            junk_phrases = ['pain control', 'as needed', 'for pain', 'signature', 'provider name', 'electronic']
            for junk in junk_phrases:
                if junk in restrictions_duration.lower():
                    # If the entire string is just junk, clear it
                    if restrictions_duration.lower().strip() == junk:
                        restrictions_duration = ""
                        break
                    # Otherwise try to extract just the duration part before the junk
                    parts = re.split(r'(?:for pain|as needed|signature)', restrictions_duration, flags=re.IGNORECASE)
                    if parts and parts[0].strip():
                        restrictions_duration = parts[0].strip()
                        break
    
    # Final accumulation
    # Only use source_text_for_other verbatim if it looks like a specific extracted block (short)
    # AND it does not contain large narrative keywords like "Medical Decision Making"
    is_specific_block = (len(source_text_for_other) < len(full_soap_text) * 0.8) and \
                        "Medical Decision Making" not in source_text_for_other and \
                        "Data Reviewed" not in source_text_for_other
                        
    # Post-process cleaning for source_text_for_other to remove common leakage
    if source_text_for_other:
        # Remove "Temporary Total Disability..." status lines (covered by checkboxes)
        source_text_for_other = re.sub(r'Temporary Total Disability.*?Signature:?', '', source_text_for_other, flags=re.IGNORECASE|re.DOTALL)
        # Remove specific signature blocks that regex might miss
        source_text_for_other = re.sub(r'SIGNATURE / PROVIDER INFORMATION.*', '', source_text_for_other, flags=re.IGNORECASE|re.DOTALL)
        # Remove history start lines "The patient is..."
        source_text_for_other = re.sub(r'The patient is a.*', '', source_text_for_other, flags=re.IGNORECASE)
        # Remove separate "Electronic Signature" lines
        source_text_for_other = re.sub(r'Electronic Signature:.*', '', source_text_for_other, flags=re.IGNORECASE)
        source_text_for_other = source_text_for_other.strip()
                        
    if source_text_for_other and len(source_text_for_other) > 10 and \
       not source_text_for_other.lower().startswith("duration") and \
       is_specific_block:
        # USER REQUEST: otherRestrictions should exact same as SOAP
        # If we successfully extracted a specific restriction block, use it verbatim
        other_restrictions = source_text_for_other.replace('\n', ' ').strip()
        # Remove multiple spaces
        other_restrictions = re.sub(r'\s+', ' ', other_restrictions)
    else:
        other_restrictions = "; ".join(list(dict.fromkeys(other_parts))) # remove duplicates preservation order


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
    
    # Calculate medication during work hours
    medication_during_work_hours = "No"  # Default
    # if meds_affect_alertness is True or str(meds_affect_alertness).lower() in ["true", "yes"]:
    #     medication_during_work_hours = "Yes"
    # elif meds_affect_alertness is False or str(meds_affect_alertness).lower() in ["false", "no"]:
    #     medication_during_work_hours = "No"
    # If string matches "Yes" or "No", use it
    # elif isinstance(meds_affect_alertness, str) and meds_affect_alertness in ["Yes", "No"]:
    #     medication_during_work_hours = meds_affect_alertness

    # -------------------------------------------------------------------------
    # Rule: nextVisitDate should be always 4 weeks from returnToModifiedDutyDate or returnToFullDutyDate
    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    # Rule: nextVisitDate Calculation
    # Dynamic: Extract from 'restrictions_duration' (e.g. "6 weeks") if available.
    # Fallback: Default to 4 weeks if no duration specified.
    # -------------------------------------------------------------------------
    ref_date_str = return_modified_duty_date or return_full_duty_date or unable_to_return_start_date
    if ref_date_str:
        try:
            # Parse the reference date (MM/DD/YYYY)
            ref_date_str = ref_date_str.strip()
            ref_date = datetime.strptime(ref_date_str, "%m/%d/%Y")
            
            # Default to 4 weeks
            # Default to 4 weeks for Next Visit Date (Standard Protocol)
            weeks_to_add = 4
            days_to_add = 0
            
            # ATTEMPT DYNAMIC PARSING from extracted Duration - Populate TTD END DATE (Per User Request)
            # User Rule: Next Visit Date should stay static 4 weeks, but TTD End Date should follow SOAP duration.
            if unable_to_return_to_work and restrictions_duration:
                # Regex to find number + unit (weeks/days/months)
                dur_match = re.search(r'(\d+)\s*(week|day|month)', restrictions_duration, re.IGNORECASE)
                if dur_match:
                    amount = int(dur_match.group(1))
                    unit = dur_match.group(2).lower()
                    
                    ttd_weeks = 0
                    ttd_days = 0
                    
                    if "week" in unit:
                        ttd_weeks = amount
                    elif "day" in unit:
                        ttd_days = amount
                    elif "month" in unit:
                        ttd_weeks = amount * 4 # Approx
                        
                    logger.info(f"Dynamic Date (TTD): Found {amount} {unit} duration in SOAP")
                    
                    # Apply to TTD End Date (unable_to_return_end_date)
                    # ref_date is unable_to_return_start_date in this context (for TTD)
                    if ref_date:
                        ttd_end_date_obj = ref_date + timedelta(weeks=ttd_weeks, days=ttd_days)
                        unable_to_return_end_date = ttd_end_date_obj.strftime("%m/%d/%Y")
                        logger.info(f"Updated TTD End Date to {unable_to_return_end_date} based on SOAP duration")
            
            # Calculate next_visit_date (Always 4 weeks from ref_date)
            new_visit_date = ref_date + timedelta(weeks=weeks_to_add, days=days_to_add)
            
            # Format back to MM/DD/YYYY
            next_visit_date = new_visit_date.strftime("%m/%d/%Y")
            logger.info(f"Calculated nextVisitDate: {next_visit_date} (Static 4 weeks from {ref_date_str})")
        except Exception as e:
            logger.warning(f"Could not calculate dates from {ref_date_str}: {e}")
            logger.warning(f"Could not calculate nextVisitDate from {ref_date_str}: {e}")

    # Strategy E: Imputation / Calculation Fallback
    # If extraction found nothing, but we have dates or standard protocol (next visit in 4 weeks),
    # we can reasonable infer the duration.
    if not restrictions_duration:
        # 1. Try to calculate from date diffs
        fmt = "%m/%d/%Y"
        try:
             # Case 1: TTD / Off Work
             if unable_to_return_start_date and unable_to_return_end_date:
                  d1 = datetime.strptime(unable_to_return_start_date, fmt)
                  d2 = datetime.strptime(unable_to_return_end_date, fmt)
                  diff_days = (d2 - d1).days
                  if diff_days > 0:
                       weeks = round(diff_days / 7)
                       if weeks > 0:
                           restrictions_duration = f"{weeks} weeks"
                       else:
                           restrictions_duration = f"{diff_days} days"
             
             # Case 2: Modified Duty
             elif return_modified_duty_date and return_full_duty_date:
                  d1 = datetime.strptime(return_modified_duty_date, fmt)
                  d2 = datetime.strptime(return_full_duty_date, fmt)
                  diff_days = (d2 - d1).days
                  if diff_days > 0:
                       weeks = round(diff_days / 7)
                       if weeks > 0:
                           restrictions_duration = f"{weeks} weeks"
                       else:
                           restrictions_duration = f"{diff_days} days"
        except:
             pass
        
        # 2. If still empty, but we have a next visit date (which we often calculate as 4 weeks)
        # Default to "Until next visit" or "4 weeks"
        # if not restrictions_duration and next_visit_date:
        #      restrictions_duration = "Until next visit"
        
        # 3. Final Fallback if we have active restrictions/status but no text
        # if not restrictions_duration and (return_to_work_with_restrictions or unable_to_return_to_work):
        #      restrictions_duration = "4 weeks" # Standard default

    # If TTD is checked but reason is empty, map 'Other Restrictions' text to 'State reason'
    if unable_to_return_to_work and not unable_to_return_reason and other_restrictions:
        unable_to_return_reason = other_restrictions
        # User Request: remove from Other if moved to State reason in this specific condition
        other_restrictions = ""
        logger.info(f"Auto-filled unableToReturnReason from otherRestrictions and cleared Other: {unable_to_return_reason}")

    # User Request: Ensure TTD end date defaults to nextVisitDate if missing (Same as Work Status Form)
    if unable_to_return_to_work and not unable_to_return_end_date and next_visit_date:
        unable_to_return_end_date = next_visit_date

    return {
        "patientName": patient_name,
        "returnToFullDuty": return_to_full_duty,
        "returnToFullDutyDate": return_full_duty_date or "",
        "returnToModifiedDutyDate": return_modified_duty_date or "",
        "maximumMedicalImprovementDate": maximum_medical_improvement_date or "",
        "nextVisitDate": next_visit_date or "",
        "dischargedFromCareDate": discharged_from_care_date or "",
        "unableToReturnToWork": unable_to_return_to_work,
        "unableToReturnStartDate": unable_to_return_start_date or "",
        "unableToReturnEndDate": unable_to_return_end_date or "",
        "unableToReturnReason": unable_to_return_reason or "",
        "returnToWorkWithRestrictions": return_to_work_with_restrictions,
        "restrictions": restrictions_obj,

        # Page 8 Checkboxes (Patient Status) - Auto-check if date is present
        "nextVisitChecked": True if next_visit_date else False,
        "mmiChecked": True if maximum_medical_improvement_date else False,
        "dischargedChecked": True if discharged_from_care_date else False,
        "returnToFullDutyChecked": True if return_full_duty_date else False,
        "returnToModifiedDutyChecked": True if return_modified_duty_date else False,
        **restrictions_obj, # Unpack restrictions to root level for easier frontend access
        "restrictions_duration": restrictions_duration or "",
        "workRestrictionsDuration": restrictions_duration or "", # Match frontend casing
        "medicationDuringWorkHours": medication_during_work_hours,
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
    
    if section_b:
        primary_dx = section_b.get("primary_diagnosis") or ""
        secondary_dx = section_b.get("secondary_diagnosis") or ""
        additional_dx_list = section_b.get("additional_diagnoses") or []
        
        # Helper to clean up diagnosis string (remove codes)
        def clean_dx(dx):
            if not dx: return ""
            # Remove M54.50 etc if present at start
            return re.sub(r'^[A-Z]\d+\.?\d*\s*-\s*', '', str(dx)).strip()

        if primary_dx:
            cleaned = clean_dx(primary_dx)
            if cleaned not in body_parts:
                body_parts.append(cleaned)
        if secondary_dx:
            cleaned = clean_dx(secondary_dx)
            if cleaned not in body_parts:
                body_parts.append(cleaned)
        if isinstance(additional_dx_list, list):
            for adx in additional_dx_list:
                cleaned = clean_dx(adx)
                if cleaned and cleaned not in body_parts:
                    body_parts.append(cleaned)
    
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
    if not employee_name and soap_doc:
        employee_name = (soap_doc.get("patient_info") or {}).get("name") or soap_doc.get("patient_name") or ""

    claim_number = header.get("claim_number") or ""
    date_of_injury = to_yyyy_mm_dd(header.get("date_of_injury"))
    if not date_of_injury and soap_doc:
         date_of_injury = to_yyyy_mm_dd(soap_doc.get("date_of_injury"))
         if not date_of_injury:
              pat_info = soap_doc.get("patient_information") or soap_doc.get("patientInfo")
              if isinstance(pat_info, dict):
                   date_of_injury = to_yyyy_mm_dd(pat_info.get("date_of_injury") or pat_info.get("dateOfInjury"))
    
    if not date_of_injury and intake_doc:
         # Try common locations in intake doc
         val = (intake_doc.get("section_d") or {}).get("date_of_injury")
         if val: date_of_injury = to_yyyy_mm_dd(val)
         
         if not date_of_injury:
             val = intake_doc.get("date_of_injury")
             if val: date_of_injury = to_yyyy_mm_dd(val)
             
    date_of_injury = date_of_injury or ""
    
    # Priority: SOAP Date of Service > PR1 First Exam Date
    date_of_evaluation = ""
    if soap_doc:
        date_of_evaluation = to_yyyy_mm_dd(soap_doc.get("date_of_service") or soap_doc.get("date") or soap_doc.get("created_at"))
    
    if not date_of_evaluation:
        date_of_evaluation = to_yyyy_mm_dd(header.get("date_of_first_examination")) or ""
    
    # Generate Body Parts Injured from SOAP/Diagnoses
    body_parts_injured = extract_body_parts_from_diagnoses(soap_doc, section_b)
    
    # Extract next follow-up appointment from SOAP or section C
    # Priority 1: From Section C (calculated or extracted)
    next_follow_up = to_yyyy_mm_dd(section_c.get("nextVisitDate"))
    
    # CRITICAL OVERRIDE for Work Status Form: Next Follow-Up should strictly be 4 weeks 
    # regardless of SOAP duration (User Request: "only work status ma 4 week nu thay")
    # We recalculate 4 weeks from the effective date found in section_c
    try:
        ref_start_date = (
            section_c.get("returnToModifiedDutyDate") or 
            section_c.get("returnToFullDutyDate") or 
            section_c.get("unableToReturnStartDate")
        )
        if ref_start_date:
            # Dates in Section C are typically MM/DD/YYYY from build_section_c
            # Handle potential formats just in case
            dt_ref = None
            for fmt in ["%m/%d/%Y", "%Y-%m-%d"]:
                 try:
                     dt_ref = datetime.strptime(ref_start_date, fmt)
                     break
                 except: continue
            
            if dt_ref:
                # Force 4 weeks exactly
                forced_date = dt_ref + timedelta(weeks=4)
                next_follow_up = forced_date.strftime("%Y-%m-%d")
                logger.info(f"WorkStatus extraction: Forced nextFollowUpAppointment to 4 weeks: {next_follow_up}")
    except Exception as e:
        logger.warning(f"Error enforcing 4-week override for Work Status: {e}")

    # Priority 2: From SOAP doc directly (Fallback if override failed and Section C empty)
    if not next_follow_up and soap_doc:
        next_visit = soap_doc.get("next_visit_date") or soap_doc.get("mmi_date")
        if not next_visit:
            patient_status = soap_doc.get("patientStatus")
            if isinstance(patient_status, dict):
                next_visit = patient_status.get("nextVisitDate")
        if next_visit:
            next_follow_up = to_yyyy_mm_dd(next_visit)
            
    next_follow_up = next_follow_up or ""
    
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
    unable_to_return_reason = section_c.get("unableToReturnReason") or ""
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
        if not off_work_to and next_follow_up:
            off_work_to = next_follow_up
    elif return_with_restrictions:
        work_status_value = "modifiedDuty"
        # Use date_of_evaluation as modified duty start date
        modified_duty_from = date_of_evaluation
        # Try to extract end date from restrictions duration or return full duty date
        if return_to_full_duty_date:
            modified_duty_to = to_yyyy_mm_dd(return_to_full_duty_date) or ""
        elif unable_to_return_end:
            modified_duty_to = to_yyyy_mm_dd(unable_to_return_end) or ""
        elif next_follow_up:
            modified_duty_to = next_follow_up
    
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
    restrictions_duration = section_c.get("workRestrictionsDuration") or section_c.get("restrictions_duration") or ""
    
    # Map PR1 restrictions to new format
    functional_restrictions = map_pr1_restrictions_to_new_format(
        restrictions, 
        other_restrictions_text,
        restrictions_duration
    )
    
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
    other_restrictions_text: str,
    restrictions_duration: str = ""
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
    
    # Map upper extremity - pushing/pulling (mapped to "Use of: ... limited to:")
    push_right = restrictions.get("pushingPullingRight")
    push_left = restrictions.get("pushingPullingLeft")
    push_bilateral = restrictions.get("pushingPullingBilateral")
    
    if push_right or push_left or push_bilateral:
        functional_restrictions["upperExtremity"]["useLimited"] = True
        if push_bilateral:
            # If bilateral, we might just pick one for the radio button or leave blank
            functional_restrictions["upperExtremity"]["useLimitedSide"] = "right" # Default to right if both
        elif push_right:
            functional_restrictions["upperExtremity"]["useLimitedSide"] = "right"
        elif push_left:
            functional_restrictions["upperExtremity"]["useLimitedSide"] = "left"
        
        push_pull_hours = restrictions.get("pushingPullingHours", "")
        if push_pull_hours:
            functional_restrictions["upperExtremity"]["useLimitedHours"] = str(push_pull_hours).strip()
    
    # Map upper extremity - grasping (mapped to "No repetitive gripping/grasping")
    grasp_right = restrictions.get("graspingRight")
    grasp_left = restrictions.get("graspingLeft")
    grasp_bilateral = restrictions.get("graspingBilateral")
    
    if grasp_right or grasp_left or grasp_bilateral:
        functional_restrictions["upperExtremity"]["noRepetitiveGripping"] = True
        if grasp_bilateral:
            functional_restrictions["upperExtremity"]["noRepetitiveGrippingRight"] = True
            functional_restrictions["upperExtremity"]["noRepetitiveGrippingLeft"] = True
        else:
            if grasp_right:
                functional_restrictions["upperExtremity"]["noRepetitiveGrippingRight"] = True
            if grasp_left:
                functional_restrictions["upperExtremity"]["noRepetitiveGrippingLeft"] = True
    
    # Map lower extremity - kneeling
    if restrictions.get("kneeling", ""):
        functional_restrictions["lowerExtremity"]["noRepetitiveKneeling"] = True
    
    # Map lower extremity - walking
    walking = restrictions.get("walking", "")
    if walking:
        functional_restrictions["lowerExtremity"]["walkingLimited"] = True
        walking_lower = str(walking).lower()
        if "2" in walking_lower and "hour" in walking_lower:
            functional_restrictions["lowerExtremity"]["walkingLimit"] = "2hrs"
        elif "4" in walking_lower and "hour" in walking_lower:
            functional_restrictions["lowerExtremity"]["walkingLimit"] = "4hrs"
        else:
            # If it's a phrase like "No prolonged walking", put it in "Other"
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
        if "2" in standing_lower and "hour" in standing_lower:
            functional_restrictions["positionTolerance"]["standingLimit"] = "2hrs"
        elif "4" in standing_lower and "hour" in standing_lower:
            functional_restrictions["positionTolerance"]["standingLimit"] = "4hrs"
        else:
            # Use custom field for text like "No prolonged standing"
            functional_restrictions["positionTolerance"]["standingLimit"] = "custom"
            functional_restrictions["positionTolerance"]["standingCustomMin"] = standing
    
    # Map position tolerance - sitting
    sitting = restrictions.get("sitting", "")
    if sitting:
        functional_restrictions["positionTolerance"]["sittingLimited"] = True
        sitting_lower = str(sitting).lower()
        if "2" in sitting_lower and "hour" in sitting_lower:
            functional_restrictions["positionTolerance"]["sittingLimit"] = "2hrs"
        elif "4" in sitting_lower and "hour" in sitting_lower:
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
    
    # Map workplace conditions (Safety Sensitive / Hazards)
    if other_restrictions_text:
        other_lower = str(other_restrictions_text).lower()
        if any(k in other_lower for k in ["height", "ladder", "scaffold"]):
            functional_restrictions["workplaceConditions"]["noWorkingAtHeights"] = True
        if any(k in other_lower for k in ["safety", "machinery", "equipment", "drive", "driving", "vehicle"]):
            functional_restrictions["workplaceConditions"]["noSafetySensitiveDuties"] = True
    
    # Set other restrictions text
    # Set other restrictions text
    functional_restrictions["otherRestrictions"] = other_restrictions_text or ""
    functional_restrictions["workRestrictionsDuration"] = restrictions_duration or ""
    
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


async def build_pr1_payload(
    intake_doc: Optional[Dict[str, Any]],
    follow_doc: Optional[Dict[str, Any]],
    soap_doc: Optional[Dict[str, Any]],
    flags: Optional[Dict[str, bool]]
) -> Dict[str, Any]:
    """Build complete PR-1 payload structure"""
    header = normalize_pr1_header(intake_doc, soap_doc)
    checkboxes = calc_checkboxes(soap_doc, follow_doc, flags)
    
    # CRITICAL PERFORMANCE OPTIMIZATION: Parallelize the heavy section builders
    # which use sequential blocking GPT calls for data extraction.
    # We use asyncio.to_thread to run these sync functions in parallel threads.
    logger.info("🚀 Starting parallel extraction for Sections A, B and C...")
    tasks = [
        asyncio.to_thread(build_section_a_rfa, soap_doc, intake_doc),
        asyncio.to_thread(build_section_b, soap_doc, intake_doc),
        asyncio.to_thread(build_section_c, intake_doc, follow_doc, soap_doc)
    ]
    
    section_a, section_b, section_c = await asyncio.gather(*tasks)
    logger.info("✅ Parallel extraction complete")
    
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

async def generate_pr1_orchestrator(payload: PR1GenerateRequest):
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
        pr1 = await build_pr1_payload(intake_doc, follow_doc, soap_doc, payload.flags or {})
        
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

async def process_pr1_generation_service(
    soap_id: str,
    use_latest_intake: bool = False,
    use_latest_followup: bool = False,
    flags: Optional[Dict[str, Any]] = None
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
        openai_api_key = os.getenv('OPENAI_API_KEY')
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
            logger.info(f"Fetched SOAP note with ID: {soap_id}, status: {soap_doc.get('status') if soap_doc else 'NOT_FOUND'}")
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
        
        # Check for saved PR1 form (Cache Check)
        saved_pr1 = await db[COLL_SAVED_PR1].find_one({"soap_id": soap_id})
        if saved_pr1 and saved_pr1.get("form_data") and saved_pr1.get("status") != "pending":
            logger.info(f"✅ Found saved PR1 form for SOAP ID: {soap_id}, returning cached version")
            
            # Construct simplified soap_data from soap_doc for response
            soap_data_dict = {
                "subjective": soap_doc.get("subjective", ""),
                "objective": soap_doc.get("objective", ""),
                "assessment": soap_doc.get("assessment", ""),
                "plan": soap_doc.get("plan", ""),
                "date_of_service": soap_doc.get("date_of_service", ""),
                "patient_info": soap_doc.get("patient_info", {})
            }
            # Add other common fields
            for key in ["examiner", "specialty", "npi", "state_license", 
                       "contact_phone", "contact_fax", "contact_email", "practice_name",
                       "primary_treating_physician"]:
                if soap_doc.get(key):
                    soap_data_dict[key] = soap_doc.get(key)

            return JSONResponse({
                "status": "success",
                "pr1_values": saved_pr1["form_data"],
                "metadata": {
                    "soap_used": True,
                    "soap_source": "soap_note_id",
                    "soap_id": soap_id,
                    "source": "cache",
                    "cached_at": saved_pr1.get("updated_at") or saved_pr1.get("created_at")
                },
                "soap_data": soap_data_dict
            })
            
        # Create/Update pending status in PR1 collection
        if saved_pr1:
             await db[COLL_SAVED_PR1].update_one(
                {"_id": saved_pr1["_id"]},
                {"$set": {"status": "pending", "updated_at": datetime.utcnow().isoformat()}}
            )
        else:
             await db[COLL_SAVED_PR1].insert_one({
                "soap_id": soap_id,
                "status": "pending", 
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            })
        
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
            
            # Step 5: Convert formatted SOAP note text to structured SOAP JSON only if needed
            # OPTIMIZATION: process_pr1_from_soap is calling build_pr1_payload which now runs
            # section extractors in parallel. We only need this full conversion if we are missing
            # critical metadata (header info) or if we want to fallback to this single-shot extraction.
            # To speed up generation (saves ~10-15s), we skip this heavy serial call if we have
            # basic structured fields or if we can assume the parallel builders will handle it.
            
            should_run_full_conversion = False
            
            # Check if we have critical metadata in the original doc
            has_metadata = (
                soap_doc.get("patient_info") or 
                soap_doc.get("patient_information") or
                soap_doc.get("patient_name")
            )
            
            # If we don't have metadata, we might need conversion to extract it
            if not has_metadata and not use_latest_intake:
                 should_run_full_conversion = True
                 logger.info("Metadata missing in SOAP doc, will run full conversion")
            
            # If we are missing ALL core sections, we might want to run it (though builders can handle it)
            # But normally we skip to let builders do it in parallel
            if should_run_full_conversion:
                try:
                    logger.info("Running full GPT conversion of formatted soap note...")
                    gpt_extracted_data = await asyncio.to_thread(convert_pdf_text_to_soap_json, formatted_soap_note)
                    logger.info("✅ Successfully extracted structured data from formatted_soap_note using GPT")
                except HTTPException:
                    raise
                except Exception as e:
                    error_msg = f"Failed to convert formatted SOAP note to structured data: {str(e)}"
                    logger.error(error_msg)
                    import traceback
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    # Don't fail completely, try to verify with what we have
                    gpt_extracted_data = {}
            else:
                logger.info("🚀 OPTIMIZATION: Skipping heavy serial GPT conversion. Relying on parallel section builders.")
                gpt_extracted_data = {}
        
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
            additional_fields = ["formatted_soap_note", "patient_info", "examiner", "specialty", "npi", "state_license", 
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
        pr1_flags = flags or {}

        
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
            pr1 = await build_pr1_payload(intake_doc, follow_doc, soap_data_dict, pr1_flags or {})
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
        
        # Extract patient name for SavedPR1Form
        patient_name = pick_name(intake_doc, soap_doc)
        if not patient_name:
            # Try to extract from patient_info in SOAP doc
            patient_info = soap_doc.get("patient_info")
            if patient_info and isinstance(patient_info, dict):
                patient_name = patient_info.get("name") or patient_info.get("patientName")
        if not patient_name:
            # Try from pr1 data itself
            patient_name = pr1.get("patientName") or pr1.get("patient_name")
        patient_name = patient_name or "Unknown Patient"
        
        # Save PR1 form using the dedicated /pr1/save API endpoint
        saved_pr1_payload = SavedPR1Form(
            soap_id=soap_id,
            patient_name=patient_name,
            form_data=pr1,
            soap_data=soap_data_dict,
            metadata=response_data["metadata"]
        )
        
        # Call the save_pr1_form function to store the PR1
        save_result = await save_pr1_service(saved_pr1_payload)
        logger.info(f"✅ PR1 form saved via /pr1/save API: {save_result.get('message')}")
        
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


async def extract_work_status_orchestrator(payload: PR1GenerateRequest):
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
        
        # Build PR-1 and extract payload with parallel optimization
        pr1 = await build_pr1_payload(intake_doc, follow_doc, soap_doc, payload.flags or {})
        
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


async def save_pr1_service(payload: SavedPR1Form):
    """
    Save or update a PR1 form in MongoDB.
    """
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        
        collection = db[COLL_SAVED_PR1]
        
        now = datetime.utcnow().isoformat()
        
        # Check if a form already exists for this soap_id
        existing = await collection.find_one({"soap_id": payload.soap_id})
        
        form_dict = payload.dict()
        if existing:
            # Update existing form
            form_dict["updated_at"] = now
            form_dict["created_at"] = existing.get("created_at", now)
            await collection.update_one(
                {"soap_id": payload.soap_id},
                {"$set": form_dict}
            )
            logger.info(f"✅ Updated saved PR1 form for SOAP ID: {payload.soap_id}")
            return {"status": "success", "message": "PR1 form updated", "id": str(existing["_id"])}
        else:
            # Create new form
            form_dict["created_at"] = now
            form_dict["updated_at"] = now
            result = await collection.insert_one(form_dict)
            logger.info(f"✅ Saved new PR1 form for SOAP ID: {payload.soap_id}")
            return {"status": "success", "message": "PR1 form saved", "id": str(result.inserted_id)}
            
    except Exception as e:
        logger.error(f"Error saving PR1 form: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to save PR1 form: {str(e)}")


async def get_saved_pr1_service(soap_id: str):
    """
    Retrieve a saved PR1 form by its associated SOAP ID.
    """
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        
        collection = db[COLL_SAVED_PR1]
        
        form = await collection.find_one({"soap_id": soap_id})
        
        if not form:
            return {"status": "not_found", "message": "No saved PR1 form found for this SOAP note"}
        
        # Convert ObjectId to string
        form["_id"] = str(form["_id"])
        
        return {
            "status": "success",
            "data": form
        }
            
    except Exception as e:
        logger.error(f"Error retrieving saved PR1 form: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve PR1 form: {str(e)}")


async def extract_work_status_from_soap_service(
    soap_id: str,
    use_latest_intake: bool = False,
    use_latest_followup: bool = False,
    flags: Optional[str] = None
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
        return await extract_work_status_orchestrator(payload)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error extracting work status from SOAP note: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to extract work status from SOAP note: {str(e)}"
        )


async def get_all_saved_pr1_soap_ids_service():
    """
    Retrieve all soap_ids that have a saved PR1 form.
    """
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        
        collection = db[COLL_SAVED_PR1]
        
        # Get all documents but only the soap_id field
        cursor = collection.find({}, {"soap_id": 1, "_id": 0})
        saved_forms = await cursor.to_list(length=1000)
        
        soap_ids = [str(doc["soap_id"]) for doc in saved_forms if "soap_id" in doc]
        
        return {
            "status": "success",
            "soap_ids": soap_ids
        }
            
    except Exception as e:
        logger.error(f"Error retrieving saved PR1 soap_ids: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve saved PR1 soap_ids: {str(e)}")
