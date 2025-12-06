"""
PR2 Forms API endpoints
"""
import logging
from fastapi import APIRouter, HTTPException, Form
from fastapi.responses import JSONResponse
from datetime import datetime
from bson import ObjectId
from typing import Any, Dict, Optional

from app.models.pr2_form import PR2Form
from app.mongodb import get_database
from app.schemas import SOAPRequest
from app.api.transcription_storage import get_transcription_by_id
from app.api.soap_storage import save_soap_note_to_db, get_soap_note_by_transcription_id

# Import functions from pr1_generator for data fetching
from app.api.pr1_generator import (
    fetch_if_needed,
    fetch_latest_document,
    COLL_INTAKE,
    COLL_FOLLOWUP,
    COLL_SOAP
)

# Import SOAP generation functions
# Note: We'll use httpx to call the SOAP generation endpoint internally to avoid circular imports
import httpx
import re

logger = logging.getLogger(__name__)

router = APIRouter()

# Configuration
PR2_FORMS_COLLECTION = 'pr2_forms'


def parse_subjective_from_text(text: str) -> Optional[Dict[str, Any]]:
    """
    Parse structured subjective complaints from formatted text.
    Extracts chief_complaint, brief_history, pain_level, mechanism_of_injury, other_subjective
    from formatted text like:
    "SUBJECTIVE COMPLAINTS: Chief Complaint: = Right shoulder pain  History of Present Illness (HPI): = ..."
    """
    if not text or not isinstance(text, str) or not text.strip():
        return None
    
    result = {
        "chief_complaint": None,
        "brief_history": None,
        "pain_level": None,
        "mechanism_of_injury": None,
        "other_subjective": None
    }
    
    # Pattern 1: Extract Chief Complaint
    # Matches: "Chief Complaint:" or "Chief Complaint:" followed by "=" and text
    chief_complaint_patterns = [
        r'Chief\s+Complaint\s*:\s*\n?\s*=\s*(.*?)(?=History|HPI|Objective|##|OBJECTIVE|$)',  # "Chief Complaint: = ..."
        r'Chief\s+Complaint\s*:\s*(.*?)(?=History|HPI|Objective|##|OBJECTIVE|$)',  # "Chief Complaint: ..."
    ]
    
    for pattern in chief_complaint_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            extracted = match.group(1).strip()
            # Clean up: remove "=" at start, extra whitespace
            extracted = re.sub(r'^=\s*', '', extracted)
            extracted = re.sub(r'\s+', ' ', extracted).strip()
            if extracted and len(extracted) > 3:
                result["chief_complaint"] = extracted
                break
    
    # Pattern 2: Extract Brief History / HPI
    # Matches: "History of Present Illness (HPI):" or "HPI:" followed by "=" and text
    hpi_patterns = [
        r'History\s+of\s+Present\s+Illness\s*\(HPI\)\s*:\s*\n?\s*=\s*(.*?)(?=Objective|##|OBJECTIVE|Physical|Assessment|Treatment|WORK|$)',  # "History of Present Illness (HPI): = ..."
        r'History\s+of\s+Present\s+Illness\s*\(HPI\)\s*:\s*(.*?)(?=Objective|##|OBJECTIVE|Physical|Assessment|Treatment|WORK|$)',  # "History of Present Illness (HPI): ..."
        r'HPI\s*:\s*\n?\s*=\s*(.*?)(?=Objective|##|OBJECTIVE|Physical|Assessment|Treatment|WORK|$)',  # "HPI: = ..."
        r'HPI\s*:\s*(.*?)(?=Objective|##|OBJECTIVE|Physical|Assessment|Treatment|WORK|$)',  # "HPI: ..."
        r'Brief\s+History\s*:\s*\n?\s*=\s*(.*?)(?=Objective|##|OBJECTIVE|Physical|Assessment|Treatment|WORK|$)',  # "Brief History: = ..."
        r'Brief\s+History\s*:\s*(.*?)(?=Objective|##|OBJECTIVE|Physical|Assessment|Treatment|WORK|$)',  # "Brief History: ..."
    ]
    
    for pattern in hpi_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            extracted = match.group(1).strip()
            # Clean up: remove "=" at start, extra whitespace
            extracted = re.sub(r'^=\s*', '', extracted)
            extracted = re.sub(r'\n=\s*', '\n', extracted)  # Remove "=" at start of lines
            extracted = re.sub(r'\s+', ' ', extracted).strip()
            if extracted and len(extracted) > 10:
                result["brief_history"] = extracted
                break
    
    # Pattern 3: Extract Mechanism of Injury
    # Look for phrases like "mechanism of injury", "injury occurred", "while training", "snapping sensation", "feeling a snap"
    mechanism_patterns = [
        r'mechanism\s+of\s+injury[:\s]*\n?\s*=\s*(.*?)(?=\.\s|Objective|##|OBJECTIVE|Brief|History|$)',  # "mechanism of injury: = ..."
        r'mechanism\s+of\s+injury[:\s]*(.*?)(?=\.\s|Objective|##|OBJECTIVE|Brief|History|$)',  # "mechanism of injury: ..."
        r'(?:acute\s+injury|injury\s+occurred|while\s+training|snapping\s+sensation|feeling\s+a\s+snap|feeling\s+.*?\s+snap)[^.]*\.',  # Extract sentence containing mechanism keywords
    ]
    
    for pattern in mechanism_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            extracted = match.group(1).strip() if match.groups() else match.group(0).strip()
            # Clean up
            extracted = re.sub(r'^=\s*', '', extracted)
            extracted = re.sub(r'\s+', ' ', extracted).strip()
            # Remove trailing period if it's part of the sentence
            extracted = re.sub(r'\.$', '', extracted).strip()
            if extracted and len(extracted) > 10:
                # If extracted contains mechanism keywords, use it
                if any(keyword in extracted.lower() for keyword in ['injury', 'training', 'snap', 'mechanism', 'acute', 'sensation']):
                    result["mechanism_of_injury"] = extracted
                    break
    
    # If mechanism not found with patterns, try to extract from HPI text
    # Look for sentences containing mechanism-related keywords
    if not result["mechanism_of_injury"] and result["brief_history"]:
        hpi_text = result["brief_history"]
        # Look for mechanism-related sentences
        mechanism_sentences = re.findall(
            r'[^.]*(?:acute\s+injury|injury\s+occurred|while\s+training|snapping\s+sensation|feeling\s+.*?\s+snap|mechanism)[^.]*\.',
            hpi_text,
            re.IGNORECASE
        )
        if mechanism_sentences:
            # Take the first sentence that contains mechanism keywords
            mechanism_text = mechanism_sentences[0].strip()
            if len(mechanism_text) > 10:
                result["mechanism_of_injury"] = mechanism_text
    
    # Pattern 4: Extract Pain Level
    # Look for pain level patterns like "pain level: 7/10", "pain: 5/10", etc.
    pain_level_patterns = [
        r'pain\s+level[:\s]*\n?\s*=\s*(\d+(?:\/\d+)?)',  # "pain level: = 7/10"
        r'pain\s+level[:\s]*(\d+(?:\/\d+)?)',  # "pain level: 7/10"
        r'pain[:\s]*(\d+(?:\/\d+)?)',  # "pain: 7/10"
    ]
    
    for pattern in pain_level_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            result["pain_level"] = match.group(1).strip()
            break
    
    # Only return object if at least one field has a value
    has_any_value = any(
        v is not None and v != "" and (isinstance(v, str) and v.strip())
        for v in result.values()
    )
    
    if has_any_value:
        return result
    return None


def parse_objective_from_text(text: str) -> Optional[Dict[str, Any]]:
    """
    Parse structured objective findings from formatted text.
    Extracts physical_examination, vital_signs, test_results, etc.
    from formatted text like:
    "OBJECTIVE FINDINGS: Physical Examination: = ... Vital Signs: = ..."
    """
    if not text or not isinstance(text, str) or not text.strip():
        return None
    
    result = {
        "physical_examination": None,
        "vital_signs": None,
        "test_results": None,
        "other_objective": None
    }
    
    # Pattern 1: Extract Physical Examination
    physical_exam_patterns = [
        r'Physical\s+Examination[:\s]*\n?\s*=\s*(.*?)(?=Vital|Test|Objective|##|OBJECTIVE|Assessment|Treatment|$)',  # "Physical Examination: = ..."
        r'Physical\s+Examination[:\s]*(.*?)(?=Vital|Test|Objective|##|OBJECTIVE|Assessment|Treatment|$)',  # "Physical Examination: ..."
        r'Physical\s+Exam[:\s]*\n?\s*=\s*(.*?)(?=Vital|Test|Objective|##|OBJECTIVE|Assessment|Treatment|$)',  # "Physical Exam: = ..."
        r'Physical\s+Exam[:\s]*(.*?)(?=Vital|Test|Objective|##|OBJECTIVE|Assessment|Treatment|$)',  # "Physical Exam: ..."
    ]
    
    for pattern in physical_exam_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            extracted = match.group(1).strip()
            extracted = re.sub(r'^=\s*', '', extracted)
            extracted = re.sub(r'\n=\s*', '\n', extracted)
            extracted = re.sub(r'\s+', ' ', extracted).strip()
            if extracted and len(extracted) > 10:
                result["physical_examination"] = extracted
                break
    
    # Pattern 2: Extract Vital Signs
    vital_signs_patterns = [
        r'Vital\s+Signs[:\s]*\n?\s*=\s*(.*?)(?=Physical|Test|Objective|##|OBJECTIVE|Assessment|Treatment|$)',  # "Vital Signs: = ..."
        r'Vital\s+Signs[:\s]*(.*?)(?=Physical|Test|Objective|##|OBJECTIVE|Assessment|Treatment|$)',  # "Vital Signs: ..."
        r'Vitals[:\s]*\n?\s*=\s*(.*?)(?=Physical|Test|Objective|##|OBJECTIVE|Assessment|Treatment|$)',  # "Vitals: = ..."
        r'Vitals[:\s]*(.*?)(?=Physical|Test|Objective|##|OBJECTIVE|Assessment|Treatment|$)',  # "Vitals: ..."
    ]
    
    for pattern in vital_signs_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            extracted = match.group(1).strip()
            extracted = re.sub(r'^=\s*', '', extracted)
            extracted = re.sub(r'\n=\s*', '\n', extracted)
            extracted = re.sub(r'\s+', ' ', extracted).strip()
            if extracted and len(extracted) > 5:
                result["vital_signs"] = extracted
                break
    
    # Pattern 3: Extract Test Results
    test_results_patterns = [
        r'Test\s+Results[:\s]*\n?\s*=\s*(.*?)(?=Physical|Vital|Objective|##|OBJECTIVE|Assessment|Treatment|$)',  # "Test Results: = ..."
        r'Test\s+Results[:\s]*(.*?)(?=Physical|Vital|Objective|##|OBJECTIVE|Assessment|Treatment|$)',  # "Test Results: ..."
        r'Diagnostic\s+Tests[:\s]*\n?\s*=\s*(.*?)(?=Physical|Vital|Objective|##|OBJECTIVE|Assessment|Treatment|$)',  # "Diagnostic Tests: = ..."
        r'Diagnostic\s+Tests[:\s]*(.*?)(?=Physical|Vital|Objective|##|OBJECTIVE|Assessment|Treatment|$)',  # "Diagnostic Tests: ..."
    ]
    
    for pattern in test_results_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            extracted = match.group(1).strip()
            extracted = re.sub(r'^=\s*', '', extracted)
            extracted = re.sub(r'\n=\s*', '\n', extracted)
            extracted = re.sub(r'\s+', ' ', extracted).strip()
            if extracted and len(extracted) > 5:
                result["test_results"] = extracted
                break
    
    # Only return object if at least one field has a value
    has_any_value = any(
        v is not None and v != "" and (isinstance(v, str) and v.strip())
        for v in result.values()
    )
    
    if has_any_value:
        return result
    return None


def parse_treatment_plan_from_text(text: str) -> Optional[Dict[str, Any]]:
    """
    Parse structured treatment plan from formatted text.
    Extracts medications, procedures, therapy, follow_up, etc.
    from formatted text like:
    "TREATMENT PLAN: Medications: = ... Procedures: = ..."
    """
    if not text or not isinstance(text, str) or not text.strip():
        return None
    
    result = {
        "medications": None,
        "procedures": None,
        "therapy": None,
        "follow_up": None,
        "other_treatment": None
    }
    
    # Pattern 1: Extract Medications
    medications_patterns = [
        r'Medications[:\s]*\n?\s*=\s*(.*?)(?=Procedures|Therapy|Follow|Treatment|##|ASSESSMENT|$)',  # "Medications: = ..."
        r'Medications[:\s]*(.*?)(?=Procedures|Therapy|Follow|Treatment|##|ASSESSMENT|$)',  # "Medications: ..."
        r'Medication[:\s]*\n?\s*=\s*(.*?)(?=Procedures|Therapy|Follow|Treatment|##|ASSESSMENT|$)',  # "Medication: = ..."
        r'Medication[:\s]*(.*?)(?=Procedures|Therapy|Follow|Treatment|##|ASSESSMENT|$)',  # "Medication: ..."
    ]
    
    for pattern in medications_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            extracted = match.group(1).strip()
            extracted = re.sub(r'^=\s*', '', extracted)
            extracted = re.sub(r'\n=\s*', '\n', extracted)
            extracted = re.sub(r'\s+', ' ', extracted).strip()
            if extracted and len(extracted) > 5:
                result["medications"] = extracted
                break
    
    # Pattern 2: Extract Procedures
    procedures_patterns = [
        r'Procedures[:\s]*\n?\s*=\s*(.*?)(?=Medications|Therapy|Follow|Treatment|##|ASSESSMENT|$)',  # "Procedures: = ..."
        r'Procedures[:\s]*(.*?)(?=Medications|Therapy|Follow|Treatment|##|ASSESSMENT|$)',  # "Procedures: ..."
        r'Procedure[:\s]*\n?\s*=\s*(.*?)(?=Medications|Therapy|Follow|Treatment|##|ASSESSMENT|$)',  # "Procedure: = ..."
        r'Procedure[:\s]*(.*?)(?=Medications|Therapy|Follow|Treatment|##|ASSESSMENT|$)',  # "Procedure: ..."
    ]
    
    for pattern in procedures_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            extracted = match.group(1).strip()
            extracted = re.sub(r'^=\s*', '', extracted)
            extracted = re.sub(r'\n=\s*', '\n', extracted)
            extracted = re.sub(r'\s+', ' ', extracted).strip()
            if extracted and len(extracted) > 5:
                result["procedures"] = extracted
                break
    
    # Pattern 3: Extract Therapy
    therapy_patterns = [
        r'Therapy[:\s]*\n?\s*=\s*(.*?)(?=Medications|Procedures|Follow|Treatment|##|ASSESSMENT|$)',  # "Therapy: = ..."
        r'Therapy[:\s]*(.*?)(?=Medications|Procedures|Follow|Treatment|##|ASSESSMENT|$)',  # "Therapy: ..."
        r'Physical\s+Therapy[:\s]*\n?\s*=\s*(.*?)(?=Medications|Procedures|Follow|Treatment|##|ASSESSMENT|$)',  # "Physical Therapy: = ..."
        r'Physical\s+Therapy[:\s]*(.*?)(?=Medications|Procedures|Follow|Treatment|##|ASSESSMENT|$)',  # "Physical Therapy: ..."
    ]
    
    for pattern in therapy_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            extracted = match.group(1).strip()
            extracted = re.sub(r'^=\s*', '', extracted)
            extracted = re.sub(r'\n=\s*', '\n', extracted)
            extracted = re.sub(r'\s+', ' ', extracted).strip()
            if extracted and len(extracted) > 5:
                result["therapy"] = extracted
                break
    
    # Pattern 4: Extract Follow-up
    follow_up_patterns = [
        r'Follow[-\s]+up[:\s]*\n?\s*=\s*(.*?)(?=Medications|Procedures|Therapy|Treatment|##|ASSESSMENT|$)',  # "Follow-up: = ..."
        r'Follow[-\s]+up[:\s]*(.*?)(?=Medications|Procedures|Therapy|Treatment|##|ASSESSMENT|$)',  # "Follow-up: ..."
        r'Follow\s+up[:\s]*\n?\s*=\s*(.*?)(?=Medications|Procedures|Therapy|Treatment|##|ASSESSMENT|$)',  # "Follow up: = ..."
        r'Follow\s+up[:\s]*(.*?)(?=Medications|Procedures|Therapy|Treatment|##|ASSESSMENT|$)',  # "Follow up: ..."
    ]
    
    for pattern in follow_up_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            extracted = match.group(1).strip()
            extracted = re.sub(r'^=\s*', '', extracted)
            extracted = re.sub(r'\n=\s*', '\n', extracted)
            extracted = re.sub(r'\s+', ' ', extracted).strip()
            if extracted and len(extracted) > 5:
                result["follow_up"] = extracted
                break
    
    # Only return object if at least one field has a value
    has_any_value = any(
        v is not None and v != "" and (isinstance(v, str) and v.strip())
        for v in result.values()
    )
    
    if has_any_value:
        return result
    return None


def serialize_mongodb_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert MongoDB document to JSON-serializable format
    Converts ObjectId and datetime objects to strings
    """
    if doc is None:
        return None
    
    serialized = {}
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            serialized[key] = str(value)
        elif isinstance(value, datetime):
            serialized[key] = value.isoformat()
        elif isinstance(value, dict):
            serialized[key] = serialize_mongodb_doc(value)
        elif isinstance(value, list):
            serialized[key] = [
                serialize_mongodb_doc(item) if isinstance(item, dict) else
                str(item) if isinstance(item, (ObjectId, datetime)) else item
                for item in value
            ]
        else:
            serialized[key] = value
    
    return serialized


def create_transcription_from_pr2(pr2_data: Dict[str, Any]) -> str:
    """
    Create a transcription text from PR2 form data by combining all text fields
    
    Args:
        pr2_data: PR2 form data dictionary
        
    Returns:
        Combined transcription text string
    """
    transcription_parts = []
    
    # Add patient information
    patient = pr2_data.get("patient", {})
    if patient:
        patient_name = f"{patient.get('firstName', '')} {patient.get('middleInitial', '')} {patient.get('lastName', '')}".strip()
        if patient_name:
            transcription_parts.append(f"Patient: {patient_name}")
        if patient.get("dateOfBirth"):
            transcription_parts.append(f"Date of Birth: {patient.get('dateOfBirth')}")
        if patient.get("dateOfInjury"):
            transcription_parts.append(f"Date of Injury: {patient.get('dateOfInjury')}")
        if patient.get("occupation"):
            transcription_parts.append(f"Occupation: {patient.get('occupation')}")
    
    # Add report type
    report_type = pr2_data.get("reportType", {})
    report_types = []
    if report_type.get("periodicReport"):
        report_types.append("Periodic Report")
    if report_type.get("changeInTreatmentPlan"):
        report_types.append("Change in Treatment Plan")
    if report_type.get("releaseFromCare"):
        report_types.append("Release from Care")
    if report_type.get("changeInWorkStatus"):
        report_types.append("Change in Work Status")
    if report_type.get("needForReferral"):
        report_types.append("Need for Referral")
    if report_type.get("responseToRequest"):
        report_types.append("Response to Request")
    if report_type.get("changeInPatientCondition"):
        report_types.append("Change in Patient Condition")
    if report_type.get("needForSurgery"):
        report_types.append("Need for Surgery")
    if report_type.get("requestForAuthorization"):
        report_types.append("Request for Authorization")
    if report_type.get("other") and report_type.get("otherText"):
        report_types.append(f"Other: {report_type.get('otherText')}")
    
    if report_types:
        transcription_parts.append(f"Report Type: {', '.join(report_types)}")
    
    # Add subjective complaints
    if pr2_data.get("subjectiveComplaints"):
        transcription_parts.append(f"\nSubjective Complaints:\n{pr2_data.get('subjectiveComplaints')}")
    
    # Add objective findings
    if pr2_data.get("objectiveFindings"):
        transcription_parts.append(f"\nObjective Findings:\n{pr2_data.get('objectiveFindings')}")
    
    # Add diagnoses
    diagnoses = pr2_data.get("diagnoses", [])
    if diagnoses:
        diagnosis_list = []
        for diag in diagnoses:
            if diag.get("diagnosis"):
                diag_text = diag.get("diagnosis")
                if diag.get("icd10"):
                    diag_text += f" (ICD-10: {diag.get('icd10')})"
                diagnosis_list.append(diag_text)
        if diagnosis_list:
            transcription_parts.append(f"\nDiagnoses:\n" + "\n".join(f"- {d}" for d in diagnosis_list))
    
    # Add treatment plan
    if pr2_data.get("treatmentPlan"):
        transcription_parts.append(f"\nTreatment Plan:\n{pr2_data.get('treatmentPlan')}")
    
    # Add work status
    work_status = pr2_data.get("workStatus", {})
    if work_status:
        work_status_parts = []
        if work_status.get("remainOffWorkUntil"):
            work_status_parts.append(f"Remain off work until: {work_status.get('remainOffWorkUntil')}")
        if work_status.get("returnToModifiedWorkOn"):
            work_status_parts.append(f"Return to modified work on: {work_status.get('returnToModifiedWorkOn')}")
        if work_status.get("limitationsRestrictions"):
            work_status_parts.append(f"Limitations/Restrictions: {work_status.get('limitationsRestrictions')}")
        if work_status.get("returnToFullDutyOn"):
            work_status_parts.append(f"Return to full duty on: {work_status.get('returnToFullDutyOn')}")
        if work_status_parts:
            transcription_parts.append(f"\nWork Status:\n" + "\n".join(work_status_parts))
    
    # Add physician information
    physician = pr2_data.get("physician", {})
    if physician:
        if physician.get("physicianName"):
            transcription_parts.append(f"\nPhysician: {physician.get('physicianName')}")
        if physician.get("dateOfExam"):
            transcription_parts.append(f"Date of Exam: {physician.get('dateOfExam')}")
        if physician.get("specialty"):
            transcription_parts.append(f"Specialty: {physician.get('specialty')}")
    
    # Combine all parts
    transcription_text = "\n".join(transcription_parts)
    
    # If no content was extracted, create a basic transcription
    if not transcription_text.strip():
        patient_name = f"{patient.get('firstName', '')} {patient.get('lastName', '')}".strip() or "Patient"
        transcription_text = f"PR2 Form created for {patient_name} on {datetime.utcnow().strftime('%Y-%m-%d')}"
    
    return transcription_text


# ============================================
# PR2 FORMS API ENDPOINTS
# ============================================

@router.post("/pr2-form")
async def create_pr2_form(data: PR2Form):
    """
    Create a new PR2 form (save to database only, no transcription/SOAP creation)
    
    **Parameters:**
    - data: PR2Form object with all sections:
        - reportType: Report type checkboxes
        - patient: Patient information
        - claimsAdministrator: Claims administrator information
        - subjectiveComplaints: Subjective complaints text
        - objectiveFindings: Objective findings text
        - diagnoses: List of diagnoses with ICD-10 codes
        - treatmentPlan: Treatment plan text
        - workStatus: Work status information
        - physician: Physician information
    
    **Returns:**
    - status: Success status
    - message: Success message
    - document_id: MongoDB document ID of the saved PR2 form
    
    **Example Request:**
    ```json
    {
        "reportType": {
            "periodicReport": true,
            "changeInWorkStatus": true
        },
        "patient": {
            "lastName": "Doe",
            "firstName": "John",
            "dateOfBirth": "1980-01-01",
            "dateOfInjury": "2024-01-15"
        },
        "subjectiveComplaints": "Patient reports ongoing pain in left shoulder...",
        "objectiveFindings": "Physical examination reveals...",
        "diagnoses": [
            {
                "diagnosis": "Left shoulder strain",
                "icd10": "S46.012A"
            }
        ],
        "treatmentPlan": "Continue physical therapy...",
        "workStatus": {
            "returnToModifiedWorkOn": "2024-12-01"
        },
        "physician": {
            "physicianName": "Dr. Smith",
            "dateOfExam": "2024-11-10"
        }
    }
    ```
    """
    try:
        # Get the database (using existing database connection)
        db = get_database()
        if db is None:
            raise HTTPException(
                status_code=503,
                detail="Database connection not available"
            )
        
        collection = db[PR2_FORMS_COLLECTION]
        
        # Convert Pydantic model to dict and add timestamp
        form_data = data.model_dump()
        form_data["created_at"] = datetime.utcnow()
        
        # Insert PR2 form into MongoDB
        result = await collection.insert_one(form_data)
        pr2_document_id = str(result.inserted_id)
        
        logger.info(f"✅ PR2 form saved with ID: {pr2_document_id}")
        
        return JSONResponse({
            "status": "success",
            "message": "PR2 form saved successfully.",
            "document_id": pr2_document_id
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving PR2 form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save PR2 form: {str(e)}"
        )


def build_pr2_from_data(
    intake_doc: Optional[Dict[str, Any]],
    follow_doc: Optional[Dict[str, Any]],
    soap_doc: Optional[Dict[str, Any]],
    pr1_doc: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Build PR2 form data structure from intake, follow-up, SOAP, and/or PR1 data
    """
    logger.info(f"🔍 Building PR2 from data - Has intake: {bool(intake_doc)}, Has followup: {bool(follow_doc)}, Has SOAP: {bool(soap_doc)}, Has PR1: {bool(pr1_doc)}")
    
    # Extract patient information
    patient_name = ""
    patient_dob = ""
    patient_doi = ""
    
    if intake_doc:
        section_a = intake_doc.get("section_a", {})
        patient_name = section_a.get("full_name", "")
        patient_dob = section_a.get("date_of_birth", "")
        section_c = intake_doc.get("section_c", {})
        patient_doi = section_c.get("date_of_injury", "")
    
    if soap_doc:
        # Extract patient info from patient_info object
        patient_info = soap_doc.get("patient_info", {})
        if isinstance(patient_info, dict):
            if not patient_name:
                patient_name = patient_info.get("name", "") or soap_doc.get("patient_name", "")
            if not patient_dob:
                patient_dob = patient_info.get("dob", "") or soap_doc.get("dob", "")
        else:
            # Fallback to direct fields
            if not patient_name:
                patient_name = soap_doc.get("patient_name", "")
            if not patient_dob:
                patient_dob = soap_doc.get("dob", "")
        
        if not patient_doi:
            patient_doi = soap_doc.get("date_of_injury", "")
    
    # Split patient name
    name_parts = patient_name.split() if patient_name else []
    first_name = name_parts[0] if len(name_parts) > 0 else ""
    last_name = name_parts[-1] if len(name_parts) > 1 else (name_parts[0] if len(name_parts) == 1 else "")
    middle_initial = name_parts[1][0] if len(name_parts) > 2 else ""
    
    # Extract diagnoses - Using comprehensive extraction like PR1
    diagnoses = []
    if soap_doc:
        import re
        doc = soap_doc
        dxs = []
        
        # Priority 1: Root level diagnoses array
        root_dxs = doc.get("diagnoses") or []
        if isinstance(root_dxs, list) and len(root_dxs) > 0:
            dxs.extend(root_dxs)
            logger.info(f"Found {len(root_dxs)} diagnosis(es) at root level")
        
        # Priority 2: Nested in clinical_information.assessment.diagnoses
        clinical_info = doc.get("clinical_information")
        if isinstance(clinical_info, dict):
            assessment = clinical_info.get("assessment")
            if isinstance(assessment, dict):
                assessment_dxs = assessment.get("diagnoses") or []
                if isinstance(assessment_dxs, list) and len(assessment_dxs) > 0:
                    dxs.extend(assessment_dxs)
                    logger.info(f"Found {len(assessment_dxs)} diagnosis(es) in clinical_information.assessment.diagnoses")
        
        # Normalize diagnoses - handle field name variations (like PR1 does)
        def normalize_diagnosis(d):
            """Normalize diagnosis dict with ICD-10 code field variations"""
            if not d or not isinstance(d, dict):
                return None
            
            # Handle ICD-10 code field variations: icd10, ICD-10, icd_10, ICD10, icd10_code
            icd10_code = (
                d.get("icd10") or 
                d.get("ICD-10") or 
                d.get("icd_10") or
                d.get("ICD10") or
                d.get("icd10_code") or
                d.get("icd_10_code") or
                ""
            )
            
            # Handle diagnosis/condition field variations
            diagnosis_text = (
                d.get("condition") or 
                d.get("diagnosis") or
                d.get("name") or
                ""
            )
            
            if diagnosis_text:
                return {
                    "diagnosis": str(diagnosis_text).strip(),
                    "icd10": str(icd10_code).strip() if icd10_code else ""
                }
            return None
        
        # Normalize all diagnoses
        normalized_diagnoses = []
        for dx in dxs:
            norm_dx = normalize_diagnosis(dx)
            if norm_dx:
                normalized_diagnoses.append(norm_dx)
        
        # Deduplicate: Remove duplicates based on condition + ICD-10 code
        seen = set()
        unique_diagnoses = []
        for dx in normalized_diagnoses:
            if dx:
                condition = str(dx.get("diagnosis", "")).strip().lower()
                icd10 = str(dx.get("icd10", "")).strip().lower()
                key = f"{condition}|{icd10}"
                
                if key not in seen and condition:
                    seen.add(key)
                    unique_diagnoses.append(dx)
        
        diagnoses.extend(unique_diagnoses[:12])
        
        # If diagnoses exist but some are missing ICD-10 codes, try to extract codes from assessment text
        if diagnoses:
            # Check which diagnoses are missing ICD-10 codes
            diagnoses_needing_icd = [dx for dx in diagnoses if not dx.get("icd10") or not dx.get("icd10").strip()]
            
            if diagnoses_needing_icd:
                logger.info(f"Found {len(diagnoses_needing_icd)} diagnoses without ICD-10 codes, attempting to extract from assessment text...")
                
                # Get assessment text to search for ICD-10 codes
                assessment_text = ""
                
                # Try assessment field directly
                if doc.get("assessment"):
                    assessment_text = str(doc.get("assessment", "")).strip()
                
                # Try from formatted_soap_note
                if not assessment_text and doc.get("formatted_soap_note"):
                    formatted_note = doc.get("formatted_soap_note", "")
                    assessment_patterns = [
                        r'##\s*A\s*[–\-]\s*ASSESSMENT\s*\n(.*?)(?=##\s*P\s*[–\-]\s*PLAN|##|---|$)',
                        r'##\s*ASSESSMENT\s*\n(.*?)(?=##\s*PLAN|---|$)',
                        r'ASSESSMENT[:\s]*\n(.*?)(?=PLAN|---|$)',
                    ]
                    for pattern in assessment_patterns:
                        match = re.search(pattern, formatted_note, re.DOTALL | re.IGNORECASE)
                        if match:
                            assessment_text = match.group(1).strip()
                            break
                
                # Try from nested clinical_information
                if not assessment_text and clinical_info and isinstance(clinical_info, dict):
                    assessment_obj = clinical_info.get("assessment")
                    if isinstance(assessment_obj, str):
                        assessment_text = assessment_obj.strip()
                    elif isinstance(assessment_obj, dict):
                        assessment_text = str(assessment_obj.get("text", assessment_obj.get("discussion_assessment", ""))).strip()
                
                # Try to match diagnosis text with ICD-10 codes in assessment
                if assessment_text:
                    for dx in diagnoses_needing_icd:
                        diagnosis_text = dx.get("diagnosis", "").strip()
                        if diagnosis_text:
                            # Extract key words from diagnosis to match
                            # Try to find ICD-10 code near this diagnosis text
                            # Pattern: Look for diagnosis text followed by ICD-10 code
                            diagnosis_words = re.split(r'\s+', diagnosis_text.lower())[:5]  # First 5 words
                            if len(diagnosis_words) >= 2:
                                # Create pattern to find this diagnosis with ICD code
                                # Example: "tear of right distal biceps" -> look for "tear.*biceps.*ICD-10:"
                                key_phrase = " ".join(diagnosis_words[-2:])  # Last 2 words
                                
                                # Try to find ICD-10 code near this diagnosis
                                # Pattern: diagnosis text... (ICD-10: CODE) or diagnosis text ICD-10: CODE
                                patterns = [
                                    rf'({re.escape(key_phrase)}[^.]*?)\s*\(ICD[- ]?10[:\s]+([A-Z0-9.]+)\)',
                                    rf'({re.escape(key_phrase)}[^.]*?)\s*\[ICD[- ]?10[:\s]+([A-Z0-9.]+)\]',
                                    rf'({re.escape(key_phrase)}[^.]*?)\s*ICD[- ]?10[:\s]+([A-Z0-9.]+)',
                                    rf'ICD[- ]?10[:\s]+([A-Z0-9.]+)[^.]*?({re.escape(key_phrase)})',
                                ]
                                
                                for pattern in patterns:
                                    matches = re.findall(pattern, assessment_text, re.IGNORECASE)
                                    if matches:
                                        # Found ICD code near this diagnosis
                                        for match in matches:
                                            if len(match) >= 2:
                                                # Check if this match is related to our diagnosis
                                                matched_text = match[0].lower() if isinstance(match[0], str) else ""
                                                icd_code = match[1].strip() if isinstance(match[1], str) else ""
                                                
                                                if key_phrase in matched_text and icd_code:
                                                    dx["icd10"] = icd_code
                                                    logger.info(f"✅ Found ICD-10 code {icd_code} for diagnosis: {diagnosis_text}")
                                                    break
                                    if dx.get("icd10"):
                                        break
                            
                            # If still not found, try simpler pattern: look for any ICD-10 code in the same sentence/paragraph
                            if not dx.get("icd10"):
                                # Find sentences containing diagnosis text (even partial match)
                                sentences = re.split(r'[.!?]\s+', assessment_text)
                                for sentence in sentences:
                                    # Try partial matching - check if any key words from diagnosis are in sentence
                                    diagnosis_lower = diagnosis_text.lower()
                                    diagnosis_keywords = [w for w in diagnosis_lower.split() if len(w) > 3]  # Words longer than 3 chars
                                    
                                    # Check if sentence contains diagnosis text or key keywords
                                    if (diagnosis_text.lower() in sentence.lower() or 
                                        any(keyword in sentence.lower() for keyword in diagnosis_keywords)):
                                        # Look for ICD-10 code in this sentence
                                        icd_match = re.search(r'ICD[- ]?10[:\s]+([A-Z0-9.]+)', sentence, re.IGNORECASE)
                                        if icd_match:
                                            dx["icd10"] = icd_match.group(1).strip()
                                            logger.info(f"✅ Found ICD-10 code {dx['icd10']} in same sentence as diagnosis: {diagnosis_text}")
                                            break
                                        
                                        # Also try looking in nearby sentences (next sentence)
                                        sentence_idx = sentences.index(sentence)
                                        if sentence_idx < len(sentences) - 1:
                                            next_sentence = sentences[sentence_idx + 1]
                                            icd_match = re.search(r'ICD[- ]?10[:\s]+([A-Z0-9.]+)', next_sentence, re.IGNORECASE)
                                            if icd_match:
                                                dx["icd10"] = icd_match.group(1).strip()
                                                logger.info(f"✅ Found ICD-10 code {dx['icd10']} in next sentence after diagnosis: {diagnosis_text}")
                                                break
                                    if dx.get("icd10"):
                                        break
                                
                                # If still not found, try broader search: find ALL ICD codes in assessment and match by proximity
                                if not dx.get("icd10"):
                                    # Find all ICD codes in assessment text
                                    all_icd_codes = re.findall(r'ICD[- ]?10[:\s]+([A-Z0-9.]+)', assessment_text, re.IGNORECASE)
                                    if all_icd_codes:
                                        # Find position of diagnosis text in assessment
                                        diagnosis_pos = assessment_text.lower().find(diagnosis_text.lower())
                                        if diagnosis_pos >= 0:
                                            # Find nearest ICD code to this diagnosis
                                            best_icd = None
                                            min_distance = float('inf')
                                            
                                            for icd_code in all_icd_codes:
                                                icd_pos = assessment_text.lower().find(f"icd-10: {icd_code.lower()}")
                                                if icd_pos >= 0:
                                                    distance = abs(icd_pos - diagnosis_pos)
                                                    if distance < min_distance:
                                                        min_distance = distance
                                                        best_icd = icd_code
                                            
                                            # If ICD code is within 500 characters, use it
                                            if best_icd and min_distance < 500:
                                                dx["icd10"] = best_icd.strip()
                                                logger.info(f"✅ Found nearest ICD-10 code {dx['icd10']} (distance: {min_distance} chars) for diagnosis: {diagnosis_text}")
                                        elif len(all_icd_codes) > 0:
                                            # If diagnosis position not found but we have ICD codes, use first one
                                            dx["icd10"] = all_icd_codes[0].strip()
                                            logger.info(f"✅ Using first available ICD-10 code {dx['icd10']} for diagnosis: {diagnosis_text}")
        
        # If still no diagnoses, try extracting from assessment text
        if not diagnoses:
            assessment_text = ""
            
            # Try assessment field directly
            if doc.get("assessment"):
                assessment_text = str(doc.get("assessment", "")).strip()
            
            # Try from formatted_soap_note
            if not assessment_text and doc.get("formatted_soap_note"):
                formatted_note = doc.get("formatted_soap_note", "")
                assessment_patterns = [
                    r'##\s*A\s*[–\-]\s*ASSESSMENT\s*\n(.*?)(?=##\s*P\s*[–\-]\s*PLAN|##|---|$)',
                    r'##\s*ASSESSMENT\s*\n(.*?)(?=##\s*PLAN|---|$)',
                    r'ASSESSMENT[:\s]*\n(.*?)(?=PLAN|---|$)',
                ]
                for pattern in assessment_patterns:
                    match = re.search(pattern, formatted_note, re.DOTALL | re.IGNORECASE)
                    if match:
                        assessment_text = match.group(1).strip()
                        break
            
            # Try from nested clinical_information
            if not assessment_text and clinical_info and isinstance(clinical_info, dict):
                assessment_obj = clinical_info.get("assessment")
                if isinstance(assessment_obj, str):
                    assessment_text = assessment_obj.strip()
                elif isinstance(assessment_obj, dict):
                    assessment_text = str(assessment_obj.get("text", assessment_obj.get("discussion_assessment", ""))).strip()
            
            if assessment_text:
                # Try multiple patterns to extract diagnoses with ICD-10 codes
                dx_patterns = [
                    r'([A-Z][^()]+?)\s*\(ICD[- ]?10[:\s]+([A-Z0-9.]+)\)',  # "Diagnosis (ICD-10: M54.5)"
                    r'([A-Z][^()]+?)\s*\[ICD[- ]?10[:\s]+([A-Z0-9.]+)\]',  # "Diagnosis [ICD-10: M54.5]"
                    r'([A-Z][^()]+?)\s*ICD[- ]?10[:\s]+([A-Z0-9.]+)',      # "Diagnosis ICD-10: M54.5"
                    r'ICD[- ]?10[:\s]+([A-Z0-9.]+)[:\s]+([A-Z][^,\.;]+)',  # "ICD-10: M54.5: Diagnosis"
                ]
                
                for dx_pattern in dx_patterns:
                    matches = re.findall(dx_pattern, assessment_text, re.IGNORECASE)
                    for match in matches[:12]:
                        if len(match) >= 2:
                            diagnosis_text = match[0].strip() if isinstance(match[0], str) else ""
                            icd10_code = match[1].strip() if isinstance(match[1], str) else ""
                            
                            if diagnosis_text and len(diagnosis_text) > 3:
                                # Avoid duplicates
                                key = f"{diagnosis_text.lower()}|{icd10_code.lower()}"
                                if key not in seen:
                                    seen.add(key)
                                    diagnoses.append({
                                        "diagnosis": diagnosis_text,
                                        "icd10": icd10_code
                                    })
                                    if len(diagnoses) >= 12:
                                        break
                    if len(diagnoses) >= 12:
                        break
                
                # If still no diagnoses with ICD codes, extract conditions without codes
                if not diagnoses and len(assessment_text) > 20:
                    # Look for common diagnosis patterns
                    diagnosis_keywords = [
                        r'(?:diagnosis|diagnoses|condition|concern for|status post|s/p|complete|partial|tear|injury|fracture|strain|sprain|dislocation|tendon|ligament|bicep|shoulder|elbow|knee|back|neck|wrist|ankle|ankle)[\s,:-]+([A-Z][^,\.;]+?)(?:\.|,|;|$)',
                        r'([A-Z][a-z]+(?:\s+[a-z]+)+?)\s+(?:complete|partial|full)?\s*(?:tear|injury|fracture|strain|sprain|dislocation|rupture)',
                        r'(?:right|left|bilateral)\s+([A-Z][a-z]+(?:\s+[a-z]+)*?)\s+(?:tear|injury|fracture|strain|sprain|dislocation)',
                    ]
                    for pattern in diagnosis_keywords:
                        matches = re.findall(pattern, assessment_text, re.IGNORECASE)
                        for match in matches[:12]:
                            diagnosis_text = match.strip() if isinstance(match, str) else " ".join(match).strip()
                            if diagnosis_text and len(diagnosis_text) > 5 and len(diagnosis_text) < 150:
                                key = f"{diagnosis_text.lower()}|"
                                if key not in seen:
                                    seen.add(key)
                                    diagnoses.append({
                                        "diagnosis": diagnosis_text,
                                        "icd10": ""
                                    })
                                    if len(diagnoses) >= 12:
                                        break
                        if len(diagnoses) >= 12:
                            break
        
        # Final pass: Try to extract any remaining ICD-10 codes from assessment text for diagnoses missing codes
        if diagnoses:
            # Get full assessment text one more time (also check formatted_soap_note entirely, not just assessment section)
            assessment_text_all = ""
            
            # Priority 1: Check assessment field
            if doc.get("assessment"):
                assessment_text_all = str(doc.get("assessment", "")).strip()
            
            # Priority 2: Extract assessment section from formatted_soap_note
            if not assessment_text_all and doc.get("formatted_soap_note"):
                formatted_note = doc.get("formatted_soap_note", "")
                assessment_patterns = [
                    r'##\s*A\s*[–\-]\s*ASSESSMENT\s*\n(.*?)(?=##\s*P\s*[–\-]\s*PLAN|##|---|$)',
                    r'##\s*ASSESSMENT\s*\n(.*?)(?=##\s*PLAN|---|$)',
                    r'A\s*[–\-]\s*ASSESSMENT\s*\n(.*?)(?=P\s*[–\-]\s*PLAN|##|---|$)',
                ]
                for pattern in assessment_patterns:
                    match = re.search(pattern, formatted_note, re.DOTALL | re.IGNORECASE)
                    if match:
                        assessment_text_all = match.group(1).strip()
                        break
            
            # Priority 3: Use entire formatted_soap_note if assessment section not found (ICD codes might be anywhere)
            if not assessment_text_all and doc.get("formatted_soap_note"):
                assessment_text_all = str(doc.get("formatted_soap_note", "")).strip()
                logger.info("Using entire formatted_soap_note for ICD-10 code extraction")
            
            # For each diagnosis without ICD code, try to find it
            if assessment_text_all:
                for dx in diagnoses:
                    if not dx.get("icd10") or not dx.get("icd10").strip():
                        diagnosis_text = dx.get("diagnosis", "").strip()
                        if diagnosis_text:
                            diagnosis_lower = diagnosis_text.lower()
                            
                            # Try multiple strategies to find ICD code
                            # Strategy 1: Look for diagnosis text followed by ICD code in same sentence/line
                            lines = assessment_text_all.split('\n')
                            for line in lines:
                                line_lower = line.lower()
                                # Check if line contains diagnosis text (even partial)
                                if (diagnosis_lower in line_lower or 
                                    any(word in line_lower for word in diagnosis_lower.split() if len(word) > 3)):
                                    icd_matches = re.findall(r'ICD[- ]?10[:\s]+([A-Z0-9.]+)', line, re.IGNORECASE)
                                    if icd_matches:
                                        dx["icd10"] = icd_matches[0].strip()
                                        logger.info(f"✅ Found ICD-10 code {dx['icd10']} for diagnosis: {diagnosis_text[:50]}...")
                                        break
                            
                            # Strategy 2: Look for any ICD code near keywords from diagnosis
                            if not dx.get("icd10"):
                                # Extract important keywords (all words longer than 3 chars)
                                words = [w for w in diagnosis_text.split() if len(w) > 3]
                                if len(words) >= 1:
                                    # Try with all keywords, then last 3, then last 2, then last 1
                                    search_terms_list = [
                                        " ".join(words).lower(),  # All keywords
                                        " ".join(words[-3:]).lower() if len(words) >= 3 else "",  # Last 3
                                        " ".join(words[-2:]).lower() if len(words) >= 2 else "",  # Last 2
                                        words[-1].lower() if words else "",  # Last word
                                    ]
                                    
                                    for search_terms in search_terms_list:
                                        if not search_terms:
                                            continue
                                        
                                        # Find sentences with these terms
                                        sentences = re.split(r'[.!?]\s+', assessment_text_all)
                                        for sentence in sentences:
                                            if search_terms in sentence.lower():
                                                icd_match = re.search(r'ICD[- ]?10[:\s]+([A-Z0-9.]+)', sentence, re.IGNORECASE)
                                                if icd_match:
                                                    dx["icd10"] = icd_match.group(1).strip()
                                                    logger.info(f"✅ Found ICD-10 code {dx['icd10']} near keywords '{search_terms}' for diagnosis: {diagnosis_text[:50]}...")
                                                    break
                                            if dx.get("icd10"):
                                                break
                                        if dx.get("icd10"):
                                            break
                            
                            # Strategy 3: Find ALL ICD codes using multiple patterns and match by proximity
                            if not dx.get("icd10"):
                                # Find all ICD codes using multiple patterns (more aggressive)
                                all_icd_patterns = [
                                    r'ICD[- ]?10[:\s]+([A-Z0-9.]+)',  # "ICD-10: S46.301A"
                                    r'ICD10[:\s]+([A-Z0-9.]+)',  # "ICD10: S46.301A"
                                    r'\(ICD[- ]?10[:\s]+([A-Z0-9.]+)\)',  # "(ICD-10: S46.301A)"
                                    r'\[ICD[- ]?10[:\s]+([A-Z0-9.]+)\]',  # "[ICD-10: S46.301A]"
                                    r'\b([A-Z][0-9]{2}\.[0-9A-Z]{3,4}[A-Z]?)\b',  # Standalone ICD-10 codes (e.g., "S46.301A")
                                ]
                                
                                all_icd_codes = []
                                for pattern in all_icd_patterns:
                                    matches = re.findall(pattern, assessment_text_all, re.IGNORECASE)
                                    all_icd_codes.extend(matches)
                                
                                # Remove duplicates while preserving order
                                seen_codes = set()
                                unique_icd_codes = []
                                for code in all_icd_codes:
                                    code_upper = code.upper().strip()
                                    if code_upper not in seen_codes and len(code_upper) >= 5:  # Valid ICD-10 codes are at least 5 chars (e.g., S46.3)
                                        seen_codes.add(code_upper)
                                        unique_icd_codes.append(code_upper)
                                
                                if unique_icd_codes:
                                    logger.info(f"Found {len(unique_icd_codes)} unique ICD-10 codes in assessment: {unique_icd_codes[:5]}")
                                    
                                    # Find position of any keyword from diagnosis
                                    words = [w for w in diagnosis_text.split() if len(w) > 3]
                                    if words:
                                        # Find position of significant keywords (try multiple)
                                        diagnosis_keywords = []
                                        if len(words) >= 2:
                                            diagnosis_keywords.append(words[-2] + " " + words[-1])  # Last 2 words
                                        if words:
                                            diagnosis_keywords.append(words[-1])  # Last word
                                        diagnosis_keywords.extend([w for w in words[-3:] if len(w) > 4])  # Last 3 long words
                                        
                                        best_icd = None
                                        min_distance = float('inf')
                                        
                                        for keyword in diagnosis_keywords:
                                            keyword_lower = keyword.lower()
                                            diagnosis_pos = assessment_text_all.lower().find(keyword_lower)
                                            
                                            if diagnosis_pos >= 0:
                                                # Find nearest ICD code to this keyword
                                                for icd_code in unique_icd_codes:
                                                    # Find all positions of this ICD code (try multiple patterns)
                                                    for icd_pattern in [
                                                        rf'ICD[- ]?10[:\s]+{re.escape(icd_code)}',
                                                        rf'\(ICD[- ]?10[:\s]+{re.escape(icd_code)}\)',
                                                        rf'\b{re.escape(icd_code)}\b',
                                                    ]:
                                                        for match in re.finditer(icd_pattern, assessment_text_all, re.IGNORECASE):
                                                            distance = abs(match.start() - diagnosis_pos)
                                                            if distance < min_distance:
                                                                min_distance = distance
                                                                best_icd = icd_code
                                                
                                                if best_icd:
                                                    break
                                        
                                        # If ICD code is within 1500 characters, use it
                                        if best_icd and min_distance < 1500:
                                            dx["icd10"] = best_icd.strip()
                                            logger.info(f"✅ Found nearest ICD-10 code {dx['icd10']} (distance: {min_distance} chars) for diagnosis: {diagnosis_text[:50]}...")
                                    elif len(unique_icd_codes) > 0:
                                        # If diagnosis keywords not found but we have ICD codes, use first one
                                        dx["icd10"] = unique_icd_codes[0].strip()
                                        logger.info(f"✅ Using first available ICD-10 code {dx['icd10']} for diagnosis: {diagnosis_text[:50]}...")
        
        # Final pass: If diagnoses still missing ICD codes, try matching with similar diagnoses that have codes
        # Example: If "tear of right distal biceps tendon" has no code, but "tear of the right distal biceps tendon" has S46.301A, use it
        diagnoses_with_codes = {i: dx for i, dx in enumerate(diagnoses) if dx.get("icd10") and dx.get("icd10").strip()}
        diagnoses_without_codes = {i: dx for i, dx in enumerate(diagnoses) if not dx.get("icd10") or not dx.get("icd10").strip()}
        
        if diagnoses_with_codes and diagnoses_without_codes:
            logger.info(f"Trying to match {len(diagnoses_without_codes)} diagnoses without codes to {len(diagnoses_with_codes)} with codes...")
            for idx_without, dx_without in diagnoses_without_codes.items():
                diagnosis_without = dx_without.get("diagnosis", "").lower()
                if diagnosis_without:
                    # Extract key words from diagnosis without code
                    words_without = set(w for w in diagnosis_without.split() if len(w) > 3)
                    
                    # Find best matching diagnosis with code
                    best_match = None
                    best_match_score = 0
                    
                    for idx_with, dx_with in diagnoses_with_codes.items():
                        diagnosis_with = dx_with.get("diagnosis", "").lower()
                        if diagnosis_with:
                            words_with = set(w for w in diagnosis_with.split() if len(w) > 3)
                            
                            # Calculate similarity score (shared words)
                            shared_words = words_without.intersection(words_with)
                            if len(words_without) > 0:
                                score = len(shared_words) / len(words_without)
                                if score > best_match_score and score > 0.5:  # At least 50% word overlap
                                    best_match_score = score
                                    best_match = dx_with.get("icd10")
                    
                    if best_match:
                        dx_without["icd10"] = best_match
                        logger.info(f"✅ Matched ICD-10 code {best_match} from similar diagnosis for: {diagnosis_without[:50]}...")
        
        logger.info(f"📋 Extracted {len(diagnoses)} diagnoses for PR2")
        for idx, dx in enumerate(diagnoses[:12], 1):  # Log all diagnoses
            icd_status = dx.get('icd10', 'N/A') if dx.get('icd10') else 'MISSING'
            logger.info(f"  {idx}. {dx.get('diagnosis', 'N/A')[:60]}... - ICD-10: {icd_status}")
    
    # Extract subjective, objective, treatment plan (using PR1's comprehensive logic)
    subjective_complaints = ""
    objective_findings = ""
    treatment_plan = ""
    
    if soap_doc:
        import re
        doc = soap_doc
        
        # Log SOAP doc structure for debugging
        logger.info(f"🔍 SOAP doc keys: {list(doc.keys())}")
        logger.info(f"🔍 SOAP subjective exists: {bool(doc.get('subjective'))}, length: {len(str(doc.get('subjective', '')))}")
        logger.info(f"🔍 SOAP objective exists: {bool(doc.get('objective'))}, length: {len(str(doc.get('objective', '')))}")
        logger.info(f"🔍 SOAP plan exists: {bool(doc.get('plan'))}, length: {len(str(doc.get('plan', '')))}")
        
        # SUBJECTIVE COMPLAINTS - Using PR1's comprehensive extraction logic
        subjective_parts = []
        
        # Priority 1: Standard SOAP subjective section (from MongoDB)
        soap_subjective = doc.get("subjective")
        if soap_subjective:
            if isinstance(soap_subjective, str) and soap_subjective.strip():
                subjective_parts.append(soap_subjective.strip())
                logger.info("Including Subjective from SOAP (subjective field)")
            elif isinstance(soap_subjective, dict):
                subjective_text = soap_subjective.get("text") or soap_subjective.get("content") or str(soap_subjective)
                if subjective_text and str(subjective_text).strip():
                    subjective_parts.append(str(subjective_text).strip())
                    logger.info("Including Subjective from SOAP (subjective dict)")
        
        # Priority 2: Extract from nested clinical_information structure
        clinical_info = doc.get("clinical_information")
        if clinical_info and isinstance(clinical_info, dict):
            subjective_obj = clinical_info.get("subjective")
            if isinstance(subjective_obj, dict):
                chief_complaint_nested = subjective_obj.get("chief_complaint")
                brief_history_nested = subjective_obj.get("brief_history")
                
                if chief_complaint_nested and str(chief_complaint_nested).strip():
                    subjective_text = str(chief_complaint_nested).strip()
                    if not any(subjective_text == existing.strip() for existing in subjective_parts):
                        subjective_parts.append(subjective_text)
                        logger.info("Including Subjective from SOAP (clinical_information.subjective.chief_complaint)")
                
                if brief_history_nested and str(brief_history_nested).strip():
                    subjective_text = str(brief_history_nested).strip()
                    if not any(subjective_text == existing.strip() for existing in subjective_parts):
                        subjective_parts.append(subjective_text)
                        logger.info("Including Subjective from SOAP (clinical_information.subjective.brief_history)")
            elif isinstance(subjective_obj, str) and subjective_obj.strip():
                if not any(subjective_obj.strip() == existing.strip() for existing in subjective_parts):
                    subjective_parts.append(subjective_obj.strip())
                    logger.info("Including Subjective from SOAP (clinical_information.subjective)")
            
            # Also check flat fields in clinical_info (including HPI - History of Present Illness)
            subjective_flat = (
                clinical_info.get("chief_complaint") or 
                clinical_info.get("brief_history") or
                clinical_info.get("history") or
                clinical_info.get("HPI") or
                clinical_info.get("history_of_present_illness") or
                clinical_info.get("history_of_present_illness_text") or
                clinical_info.get("hpi_text")
            )
            if subjective_flat and str(subjective_flat).strip():
                subjective_text = str(subjective_flat).strip()
                is_duplicate = any(subjective_text == existing.strip() for existing in subjective_parts)
                if not is_duplicate:
                    subjective_parts.append(subjective_text)
                    logger.info("Including Subjective Findings from SOAP dictation (clinical_information flat fields including HPI)")
        
        # Priority 3: PR-1 specific fields
        soap_chief_complaint = doc.get("chief_complaint") or doc.get("brief_history")
        if soap_chief_complaint and str(soap_chief_complaint).strip():
            subjective_text = str(soap_chief_complaint).strip()
            if not any(subjective_text == existing.strip() for existing in subjective_parts):
                subjective_parts.append(subjective_text)
                logger.info("Including Subjective from SOAP (chief_complaint/brief_history)")
        
        # Priority 4: reason_for_visit
        reason_for_visit = doc.get("reason_for_visit")
        if reason_for_visit and str(reason_for_visit).strip():
            reason_text = str(reason_for_visit).strip()
            if not any(reason_text == existing.strip() for existing in subjective_parts):
                subjective_parts.append(reason_text)
                logger.info("Including Subjective from SOAP (reason_for_visit)")
        
        # Combine all subjective sources
        subjective_complaints = " | ".join(filter(None, subjective_parts)) if subjective_parts else ""
        
        # Also extract structured subjective data as object (like PR1 format)
        subjective_complaints_obj = None
        objective_findings_obj = None
        treatment_plan_obj = None
        if soap_doc:
            clinical_info = doc.get("clinical_information")
            if clinical_info and isinstance(clinical_info, dict):
                subjective_obj = clinical_info.get("subjective")
                if isinstance(subjective_obj, dict):
                    # Extract structured fields
                    subjective_complaints_obj = {
                        "chief_complaint": subjective_obj.get("chief_complaint") or doc.get("chief_complaint") or None,
                        "brief_history": subjective_obj.get("brief_history") or doc.get("brief_history") or None,
                        "pain_level": subjective_obj.get("pain_level") or None,
                        "mechanism_of_injury": subjective_obj.get("mechanism_of_injury") or None,
                        "other_subjective": subjective_obj.get("other_subjective") or None
                    }
                    # Only include object if at least one field has a non-None, non-empty value
                    has_any_value = any(
                        v is not None and v != "" and (isinstance(v, str) and v.strip())
                        for v in subjective_complaints_obj.values()
                    )
                    if not has_any_value:
                        subjective_complaints_obj = None
        
        # If structured object not found, try to parse from formatted text or combined string
        if not subjective_complaints_obj and subjective_complaints:
            # Try to parse structured data from formatted text
            parsed_obj = parse_subjective_from_text(subjective_complaints)
            if parsed_obj:
                subjective_complaints_obj = parsed_obj
                logger.info("✅ Parsed structured subjective complaints from formatted text")
        
        # Also try to parse from formatted_soap_note if available
        if not subjective_complaints_obj and doc.get("formatted_soap_note"):
            formatted_note = str(doc.get("formatted_soap_note", ""))
            parsed_obj = parse_subjective_from_text(formatted_note)
            if parsed_obj:
                subjective_complaints_obj = parsed_obj
                logger.info("✅ Parsed structured subjective complaints from formatted_soap_note")
        
        # ALWAYS try to extract from formatted_soap_note (CRITICAL - this is where the data usually is)
        # PRIORITY: If structured fields are empty or very short (< 50 chars), prioritize formatted_soap_note extraction
        if doc.get("formatted_soap_note"):
            formatted_note = str(doc.get("formatted_soap_note", ""))
            logger.info(f"🔍 Attempting to extract Subjective from formatted_soap_note (length: {len(formatted_note)})")
            logger.info(f"🔍 Current subjective_complaints length: {len(subjective_complaints)}")
            
            subjective_patterns = [
                r'History of Present Illness\s*\(HPI\)\s*:\s*\n\s*=\s*(.*?)(?=Objective|##|OBJECTIVE|Physical Exam|Assessment|Treatment|WORK|Chief Complaint|---|$)',  # "History of Present Illness (HPI): \n = content"
                r'History of Present Illness\s*\(HPI\)\s*:\s*\n(.*?)(?=Objective|##|OBJECTIVE|Physical Exam|Assessment|Treatment|WORK|Chief Complaint|---|$)',  # "History of Present Illness (HPI):"
                r'History of Present Illness\s*\(HPI\)\s*\n\s*=\s*(.*?)(?=Objective|##|OBJECTIVE|Physical Exam|Assessment|Treatment|WORK|Chief Complaint|---|$)',  # "History of Present Illness (HPI) \n = content"
                r'History of Present Illness\s*\(HPI\)\s*\n(.*?)(?=Objective|##|OBJECTIVE|Physical Exam|Assessment|Treatment|WORK|Chief Complaint|---|$)',  # "History of Present Illness (HPI)"
                r'History of Present Illness[:\s]*\n\s*=\s*(.*?)(?=Objective|##|OBJECTIVE|Physical Exam|Assessment|Treatment|WORK|Chief Complaint|---|$)',  # "History of Present Illness: \n = content"
                r'History of Present Illness[:\s]*\n(.*?)(?=Objective|##|OBJECTIVE|Physical Exam|Assessment|Treatment|WORK|Chief Complaint|---|$)',  # "History of Present Illness"
                r'HPI[:\s]*\n\s*=\s*(.*?)(?=Objective|##|OBJECTIVE|Physical Exam|Assessment|Treatment|WORK|Chief Complaint|---|$)',  # "HPI: \n = content"
                r'HPI[:\s]*\n(.*?)(?=Objective|##|OBJECTIVE|Physical Exam|Assessment|Treatment|WORK|Chief Complaint|---|$)',  # "HPI:"
                r'Chief Complaint[:\s]*\n\s*=\s*(.*?)(?=History|HPI|Objective|##|OBJECTIVE|Physical Exam|Assessment|Treatment|WORK|---|$)',  # "Chief Complaint: \n = content"
                r'Chief Complaint[:\s]*\n(.*?)(?=History|HPI|Objective|##|OBJECTIVE|Physical Exam|Assessment|Treatment|WORK|---|$)',  # "Chief Complaint:"
                r'SUBJECTIVE COMPLAINTS\s*\n(.*?)(?=OBJECTIVE FINDINGS|##|---|$)',  # "SUBJECTIVE COMPLAINTS" header
                r'##\s*SUBJECTIVE COMPLAINTS\s*\n(.*?)(?=##|OBJECTIVE|---|$)',  # "## SUBJECTIVE COMPLAINTS"
                r'SUBJECTIVE COMPLAINTS\s*:\s*\n(.*?)(?=OBJECTIVE FINDINGS|##|---|$)',  # "SUBJECTIVE COMPLAINTS:"
                r'##\s*S\s*[–\-]\s*SUBJECTIVE\s*\n(.*?)(?=##\s*O\s*[–\-]\s*OBJECTIVE|##\s*A\s*[–\-]\s*ASSESSMENT|---|$)',
                r'##\s*S\s*[–\-]\s*SUBJECTIVE\s*COMPLAINTS\s*\n(.*?)(?=##\s*O|##\s*A|---|$)',  # "## S – SUBJECTIVE COMPLAINTS"
                r'##\s*SUBJECTIVE\s*\n(.*?)(?=##\s*OBJECTIVE|##\s*ASSESSMENT|---|$)',
                r'S\s*[–\-]\s*SUBJECTIVE\s*\n(.*?)(?=O\s*[–\-]\s*OBJECTIVE|A\s*[–\-]\s*ASSESSMENT|---|$)',  # "S – SUBJECTIVE"
                r'SUBJECTIVE[:\s]*\n(.*?)(?=OBJECTIVE|ASSESSMENT|---|$)',
            ]
            
            best_extracted = ""
            best_pattern = ""
            chief_complaint_extracted = ""
            hpi_extracted = ""
            
            for pattern in subjective_patterns:
                match = re.search(pattern, formatted_note, re.DOTALL | re.IGNORECASE)
                if match:
                    extracted = match.group(1).strip()
                    # Clean up extracted text (remove markdown formatting)
                    extracted = re.sub(r'^\*\*[^*]+\*\*[:\s]*', '', extracted)  # Remove bold headers
                    extracted = re.sub(r'^#{1,6}\s+', '', extracted, flags=re.MULTILINE)  # Remove markdown headers
                    extracted = re.sub(r'^[A-Z\s]+[:\-]\s*', '', extracted, flags=re.MULTILINE)  # Remove section headers
                    # Remove "=" prefix if present at start of line (sometimes formatting adds = on new line)
                    extracted = re.sub(r'^=\s*', '', extracted)
                    extracted = re.sub(r'\n=\s*', '\n', extracted)  # Remove "=" at start of any line
                    # Remove empty lines at start
                    extracted = re.sub(r'^\s*\n+', '', extracted)
                    if extracted and len(extracted) > 10:
                        # Track Chief Complaint and HPI separately
                        if 'chief complaint' in pattern.lower():
                            if len(extracted) > len(chief_complaint_extracted):
                                chief_complaint_extracted = extracted
                        elif 'hpi' in pattern.lower() or 'history of present illness' in pattern.lower():
                            if len(extracted) > len(hpi_extracted):
                                hpi_extracted = extracted
                        
                        if len(extracted) > len(best_extracted):
                            best_extracted = extracted
                            best_pattern = pattern
            
            # If both Chief Complaint and HPI are found, combine them (like PR1 does)
            if chief_complaint_extracted and hpi_extracted:
                combined = f"{chief_complaint_extracted} {hpi_extracted}".strip()
                if len(combined) > len(best_extracted):
                    best_extracted = combined
                    best_pattern = "Combined Chief Complaint + HPI"
                    logger.info(f"✅ Found both Chief Complaint and HPI, combining them (CC: {len(chief_complaint_extracted)} chars, HPI: {len(hpi_extracted)} chars)")
            elif chief_complaint_extracted and not hpi_extracted:
                # If only Chief Complaint found, use it if it's better
                if len(chief_complaint_extracted) > len(best_extracted):
                    best_extracted = chief_complaint_extracted
                    best_pattern = "Chief Complaint"
            elif hpi_extracted and not chief_complaint_extracted:
                # If only HPI found, use it if it's better
                if len(hpi_extracted) > len(best_extracted):
                    best_extracted = hpi_extracted
                    best_pattern = "HPI"
            
            # CRITICAL: Use formatted_soap_note extraction if:
            # 1. Structured fields are empty/short (< 50 chars), OR
            # 2. formatted_soap_note extraction is longer/better
            should_use_formatted = False
            if best_extracted:
                if not subjective_complaints or len(subjective_complaints.strip()) < 50:
                    # If structured field is empty or too short, definitely use formatted_soap_note
                    should_use_formatted = True
                    logger.info(f"✅ Using formatted_soap_note extraction because structured field is empty/short (length: {len(subjective_complaints)})")
                elif len(best_extracted) > len(subjective_complaints):
                    # If formatted_soap_note has more content, use it
                    should_use_formatted = True
                    logger.info(f"✅ Using formatted_soap_note extraction because it's longer (formatted: {len(best_extracted)} vs structured: {len(subjective_complaints)})")
                
                if should_use_formatted:
                    subjective_complaints = best_extracted
                    logger.info(f"✅ Extracted Subjective from formatted_soap_note using pattern: {best_pattern[:50]}... (length: {len(best_extracted)})")
                    
                    # Try to parse structured object from the extracted formatted text
                    if not subjective_complaints_obj:
                        parsed_obj = parse_subjective_from_text(best_extracted)
                        if parsed_obj:
                            subjective_complaints_obj = parsed_obj
                            logger.info("✅ Parsed structured subjective complaints from formatted_soap_note extracted text")
                else:
                    logger.info(f"⚠️ Formatted_soap_note has Subjective (length: {len(best_extracted)}), but structured field is longer (length: {len(subjective_complaints)})")
            else:
                logger.warning(f"⚠️ Could not extract Subjective from formatted_soap_note using any pattern")
                # Log a preview of formatted_note to help debug
                if len(formatted_note) > 0:
                    preview = formatted_note[:500] if len(formatted_note) > 500 else formatted_note
                    logger.info(f"🔍 Formatted SOAP note preview (first 500 chars): {preview}")
            
            # FINAL FALLBACK for Subjective: If still empty, try to extract from anywhere in formatted_note by looking for HPI/keywords
            if not subjective_complaints or len(subjective_complaints.strip()) < 20:
                # Look for subjective/complaint keywords and extract surrounding text
                subjective_keywords = [
                    r'(?:history of present illness|HPI|chief complaint|patient reports|patient states|patient is)',
                    r'(?:presenting with|complains of|pain|injury occurred|mechanism of injury)',
                ]
                
                for keyword_pattern in subjective_keywords:
                    matches = list(re.finditer(keyword_pattern, formatted_note, re.IGNORECASE))
                    if matches:
                        # Take text around first match (200 chars before and 1000 chars after)
                        first_match = matches[0]
                        start_pos = max(0, first_match.start() - 200)
                        end_pos = min(len(formatted_note), first_match.end() + 1000)
                        extracted = formatted_note[start_pos:end_pos].strip()
                        
                        # Clean up
                        extracted = re.sub(r'^\*\*[^*]+\*\*[:\s]*', '', extracted)
                        extracted = re.sub(r'^#{1,6}\s+', '', extracted, flags=re.MULTILINE)
                        extracted = re.sub(r'^[A-Z\s]+[:\-]\s*', '', extracted, flags=re.MULTILINE)
                        # Remove "=" prefix if present
                        extracted = re.sub(r'^=\s*', '', extracted)
                        if extracted and len(extracted) > 50:
                            subjective_complaints = extracted
                            logger.info(f"✅ Extracted Subjective using keyword-based fallback search (length: {len(extracted)})")
                            break
        
        # OBJECTIVE FINDINGS - Using PR1's comprehensive extraction logic (EXACT SAME AS PR1)
        # MUST include BOTH SOAP dictation AND intake form Section I vitals (but PR2 doesn't use intake vitals)
        objective_parts = []
        
        # Get objective findings from SOAP dictation (required per mapping)
        # Priority: Check both physical_exam (PR-1 specific) and objective (standard SOAP field)
        # Always include objective field from SOAP notes stored in MongoDB
        soap_objective = doc.get("objective")
        soap_physical_exam = doc.get("physical_exam")
        
        # Include physical_exam if it exists (PR-1 specific field)
        if soap_physical_exam and str(soap_physical_exam).strip():
            objective_parts.append(str(soap_physical_exam).strip())
            logger.info("Including Objective Findings from SOAP dictation (physical_exam field)")
        
        # ALWAYS include objective field from SOAP (standard SOAP note field stored in MongoDB)
        if soap_objective and str(soap_objective).strip():
            objective_text = str(soap_objective).strip()
            # Only add if not already added (to avoid duplicates if physical_exam and objective are the same)
            is_duplicate = any(objective_text == existing.strip() for existing in objective_parts)
            if not is_duplicate:
                objective_parts.append(objective_text)
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
                    is_duplicate = any(objective_text == existing.strip() for existing in objective_parts)
                    if not is_duplicate:
                        objective_parts.append(objective_text)
                        logger.info("Including Objective Findings from SOAP dictation (clinical_information.objective)")
                elif isinstance(objective_from_clinical, dict):
                    # If objective is a dict, extract text content
                    objective_text = objective_from_clinical.get("text") or objective_from_clinical.get("content") or str(objective_from_clinical)
                    if objective_text and str(objective_text).strip():
                        objective_text_str = str(objective_text).strip()
                        is_duplicate = any(objective_text_str == existing.strip() for existing in objective_parts)
                        if not is_duplicate:
                            objective_parts.append(objective_text_str)
                            logger.info("Including Objective Findings from SOAP dictation (clinical_information.objective dict)")
            
            physical_exam_from_clinical = (
                clinical_info.get("physical_exam") or
                clinical_info.get("physical_examination") or
                clinical_info.get("exam") or
                clinical_info.get("examination")
            )
            if physical_exam_from_clinical and str(physical_exam_from_clinical).strip():
                objective_text = str(physical_exam_from_clinical).strip()
                is_duplicate = any(objective_text == existing.strip() for existing in objective_parts)
                if not is_duplicate:
                    objective_parts.append(objective_text)
                    logger.info("Including Objective Findings from SOAP dictation (clinical_information.physical_exam)")
        
        # Combine all objective sources
        objective_findings = " | ".join(filter(None, objective_parts)) if objective_parts else ""
        
        # ALWAYS try to extract from formatted_soap_note (CRITICAL - this is where the data usually is)
        if doc.get("formatted_soap_note"):
            formatted_note = str(doc.get("formatted_soap_note", ""))
            logger.info(f"🔍 Attempting to extract Objective from formatted_soap_note (length: {len(formatted_note)})")
            
            objective_patterns = [
                r'OBJECTIVE FINDINGS\s*\n(.*?)(?=ASSESSMENT|TREATMENT PLAN|WORK STATUS|##|---|$)',  # "OBJECTIVE FINDINGS" header
                r'##\s*OBJECTIVE FINDINGS\s*\n(.*?)(?=##|ASSESSMENT|TREATMENT|WORK|---|$)',  # "## OBJECTIVE FINDINGS"
                r'OBJECTIVE FINDINGS\s*:\s*\n(.*?)(?=ASSESSMENT|TREATMENT PLAN|WORK STATUS|##|---|$)',  # "OBJECTIVE FINDINGS:"
                r'##\s*O\s*[–\-]\s*OBJECTIVE\s*\n(.*?)(?=##\s*A\s*[–\-]\s*ASSESSMENT|##\s*P\s*[–\-]\s*PLAN|---|$)',
                r'##\s*O\s*[–\-]\s*OBJECTIVE\s*\/\s*Physical Exam\s*\n(.*?)(?=##\s*A\s*[–\-]\s*ASSESSMENT|##\s*P\s*[–\-]\s*PLAN|---|$)',  # "## O – OBJECTIVE/Physical Exam"
                r'##\s*O\s*[–\-]\s*OBJECTIVE FINDINGS\s*\n(.*?)(?=##\s*A|##\s*P|---|$)',  # "## O – OBJECTIVE FINDINGS"
                r'##\s*OBJECTIVE\s*\n(.*?)(?=##\s*ASSESSMENT|##\s*PLAN|---|$)',
                r'O\s*[–\-]\s*OBJECTIVE\s*\n(.*?)(?=A\s*[–\-]\s*ASSESSMENT|P\s*[–\-]\s*PLAN|---|$)',  # "O – OBJECTIVE"
                r'OBJECTIVE[:\s]*\n(.*?)(?=ASSESSMENT|PLAN|---|$)',
                r'Physical Exam[:\s]*\n(.*?)(?=ASSESSMENT|PLAN|---|$)',  # "Physical Exam"
            ]
            
            best_extracted = ""
            best_pattern = ""
            for pattern in objective_patterns:
                match = re.search(pattern, formatted_note, re.DOTALL | re.IGNORECASE)
                if match:
                    extracted = match.group(1).strip()
                    # Clean up extracted text (remove markdown formatting)
                    extracted = re.sub(r'^\*\*[^*]+\*\*[:\s]*', '', extracted)  # Remove bold headers
                    extracted = re.sub(r'^#{1,6}\s+', '', extracted, flags=re.MULTILINE)  # Remove markdown headers
                    # Remove any remaining section headers
                    extracted = re.sub(r'^[A-Z\s]+[:\-]\s*', '', extracted, flags=re.MULTILINE)
                    if extracted and len(extracted) > 10:
                        if len(extracted) > len(best_extracted):
                            best_extracted = extracted
                            best_pattern = pattern
            
            # CRITICAL: Use formatted_soap_note extraction if:
            # 1. Structured fields are empty/short (< 50 chars), OR
            # 2. formatted_soap_note extraction is longer/better
            should_use_formatted = False
            if best_extracted:
                if not objective_findings or len(objective_findings.strip()) < 50:
                    # If structured field is empty or too short, definitely use formatted_soap_note
                    should_use_formatted = True
                    logger.info(f"✅ Using formatted_soap_note extraction because structured field is empty/short (length: {len(objective_findings)})")
                elif len(best_extracted) > len(objective_findings):
                    # If formatted_soap_note has more content, use it
                    should_use_formatted = True
                    logger.info(f"✅ Using formatted_soap_note extraction because it's longer (formatted: {len(best_extracted)} vs structured: {len(objective_findings)})")
                
                if should_use_formatted:
                    objective_findings = best_extracted
                    logger.info(f"✅ Extracted Objective from formatted_soap_note using pattern: {best_pattern[:50]}... (length: {len(best_extracted)})")
                    
                    # Try to parse structured object from the extracted formatted text
                    if not objective_findings_obj:
                        parsed_obj = parse_objective_from_text(best_extracted)
                        if parsed_obj:
                            objective_findings_obj = parsed_obj
                            logger.info("✅ Parsed structured objective findings from formatted_soap_note extracted text")
                else:
                    logger.info(f"⚠️ Formatted_soap_note has Objective (length: {len(best_extracted)}), but structured field is longer (length: {len(objective_findings)})")
        
        # If structured object not found, try to parse from formatted text or combined string
        if not objective_findings_obj and objective_findings:
            # Try to parse structured data from formatted text
            parsed_obj = parse_objective_from_text(objective_findings)
            if parsed_obj:
                objective_findings_obj = parsed_obj
                logger.info("✅ Parsed structured objective findings from formatted text")
        
        # Also try to parse from formatted_soap_note if available
        if not objective_findings_obj and doc.get("formatted_soap_note"):
            formatted_note = str(doc.get("formatted_soap_note", ""))
            parsed_obj = parse_objective_from_text(formatted_note)
            if parsed_obj:
                objective_findings_obj = parsed_obj
                logger.info("✅ Parsed structured objective findings from formatted_soap_note")
            else:
                logger.warning(f"⚠️ Could not extract Objective from formatted_soap_note using any pattern")
                # Log a preview of formatted_note to help debug
                if len(formatted_note) > 0:
                    preview = formatted_note[:500] if len(formatted_note) > 500 else formatted_note
                    logger.info(f"🔍 Formatted SOAP note preview (first 500 chars): {preview}")
            
            # If still not found, try a more aggressive search - find anything between "OBJECTIVE" and "ASSESSMENT" or "PLAN"
            if not objective_findings or len(objective_findings.strip()) < 20:
                # Look for the word "OBJECTIVE" or "Physical Exam" anywhere in the note
                obj_section_start = None
                for line in formatted_note.split('\n'):
                    if re.search(r'\bOBJECTIVE\b|\bPhysical Exam\b|\bOBJECTIVE FINDINGS\b', line, re.IGNORECASE):
                        obj_section_start = formatted_note.find(line)
                        break
                
                if obj_section_start is not None:
                    # Find the next major section (ASSESSMENT, PLAN, etc.)
                    rest_of_note = formatted_note[obj_section_start:]
                    end_markers = [
                        r'\n##\s*A\s*[–\-]\s*ASSESSMENT',
                        r'\n##\s*ASSESSMENT',
                        r'\n##\s*P\s*[–\-]\s*PLAN',
                        r'\n##\s*PLAN',
                        r'\nASSESSMENT',
                        r'\nTREATMENT PLAN',
                        r'\nWORK STATUS',
                    ]
                    
                    end_pos = len(rest_of_note)
                    for marker in end_markers:
                        match = re.search(marker, rest_of_note, re.IGNORECASE)
                        if match:
                            end_pos = min(end_pos, match.start())
                    
                    if end_pos < len(rest_of_note):
                        extracted = rest_of_note[:end_pos].strip()
                        # Remove the header line itself
                        lines = extracted.split('\n')
                        if len(lines) > 1:
                            # Skip first line (the header) and take rest
                            extracted = '\n'.join(lines[1:]).strip()
                            # Clean up
                            extracted = re.sub(r'^\*\*[^*]+\*\*[:\s]*', '', extracted)
                            extracted = re.sub(r'^#{1,6}\s+', '', extracted, flags=re.MULTILINE)
                            extracted = re.sub(r'^[A-Z\s]+[:\-]\s*', '', extracted, flags=re.MULTILINE)
                            if extracted and len(extracted) > 10:
                                objective_findings = extracted
                                logger.info(f"✅ Extracted Objective using aggressive search (length: {len(extracted)})")
            
            # FINAL FALLBACK: If still empty, try to extract from anywhere in formatted_note by looking for key phrases
            if not objective_findings or len(objective_findings.strip()) < 20:
                # Look for physical exam keywords and extract surrounding text
                physical_exam_keywords = [
                    r'(?:physical examination|exam reveals|examination shows|clinical examination|on exam)',
                    r'(?:range of motion|ROM|tenderness|swelling|ecchymosis|palpation)',
                ]
                
                for keyword_pattern in physical_exam_keywords:
                    matches = list(re.finditer(keyword_pattern, formatted_note, re.IGNORECASE))
                    if matches:
                        # Take text around first match (500 chars before and after)
                        first_match = matches[0]
                        start_pos = max(0, first_match.start() - 200)
                        end_pos = min(len(formatted_note), first_match.end() + 1000)
                        extracted = formatted_note[start_pos:end_pos].strip()
                        
                        # Clean up
                        extracted = re.sub(r'^\*\*[^*]+\*\*[:\s]*', '', extracted)
                        extracted = re.sub(r'^#{1,6}\s+', '', extracted, flags=re.MULTILINE)
                        if extracted and len(extracted) > 50:
                            objective_findings = extracted
                            logger.info(f"✅ Extracted Objective using keyword-based fallback search (length: {len(extracted)})")
                            break
        
        # TREATMENT PLAN - Using PR1's comprehensive extraction logic
        treatment_plan_parts = []
        
        # Priority 1: PR-1 specific field
        treatment_plan_text = doc.get("treatment_plan_text")
        if treatment_plan_text and str(treatment_plan_text).strip():
            treatment_plan_parts.append(str(treatment_plan_text).strip())
            logger.info("Including Treatment Plan from SOAP (treatment_plan_text)")
        
        # Priority 2: Extract from nested treatment_plan_information structure
        treatment_plan_info = doc.get("treatment_plan_information")
        if treatment_plan_info:
            if isinstance(treatment_plan_info, dict):
                plan_text = (
                    treatment_plan_info.get("treatment") or 
                    treatment_plan_info.get("plan") or
                    treatment_plan_info.get("text") or
                    treatment_plan_info.get("treatment_plan")
                )
                if plan_text and str(plan_text).strip():
                    plan_text_str = str(plan_text).strip()
                    if not any(plan_text_str == existing.strip() for existing in treatment_plan_parts):
                        treatment_plan_parts.append(plan_text_str)
                        logger.info("Including Treatment Plan from SOAP (treatment_plan_information)")
            elif isinstance(treatment_plan_info, str) and treatment_plan_info.strip():
                if not any(treatment_plan_info.strip() == existing.strip() for existing in treatment_plan_parts):
                    treatment_plan_parts.append(treatment_plan_info.strip())
                    logger.info("Including Treatment Plan from SOAP (treatment_plan_information as string)")
        
        # Priority 3: Standard SOAP plan section
        plan_obj = doc.get("plan")
        if plan_obj:
            if isinstance(plan_obj, dict):
                plan_text = plan_obj.get("treatment") or plan_obj.get("text") or plan_obj.get("plan")
                if plan_text and str(plan_text).strip():
                    plan_text_str = str(plan_text).strip()
                    if not any(plan_text_str == existing.strip() for existing in treatment_plan_parts):
                        treatment_plan_parts.append(plan_text_str)
                        logger.info("Including Treatment Plan from SOAP (plan.treatment/text)")
            elif isinstance(plan_obj, str) and plan_obj.strip():
                if not any(plan_obj.strip() == existing.strip() for existing in treatment_plan_parts):
                    treatment_plan_parts.append(plan_obj.strip())
                    logger.info("Including Treatment Plan from SOAP (plan section)")
        
        # Priority 4: Extract from nested clinical_information.plan structure
        if clinical_info and isinstance(clinical_info, dict):
            plan_obj_clinical = clinical_info.get("plan")
            if isinstance(plan_obj_clinical, dict):
                treatment_plan_nested = (
                    plan_obj_clinical.get("treatment_plan_text") or
                    plan_obj_clinical.get("treatment_plan") or
                    plan_obj_clinical.get("treatment") or
                    plan_obj_clinical.get("plan") or
                    plan_obj_clinical.get("text")
                )
                if treatment_plan_nested and str(treatment_plan_nested).strip():
                    plan_text_str = str(treatment_plan_nested).strip()
                    if not any(plan_text_str == existing.strip() for existing in treatment_plan_parts):
                        treatment_plan_parts.append(plan_text_str)
                        logger.info("Including Treatment Plan from SOAP (clinical_information.plan)")
            elif isinstance(plan_obj_clinical, str) and plan_obj_clinical.strip():
                if not any(plan_obj_clinical.strip() == existing.strip() for existing in treatment_plan_parts):
                    treatment_plan_parts.append(plan_obj_clinical.strip())
                    logger.info("Including Treatment Plan from SOAP (clinical_information.plan as string)")
        
        # Combine all treatment plan sources
        treatment_plan = " | ".join(filter(None, treatment_plan_parts)) if treatment_plan_parts else ""
        
        # If structured object not found, try to parse from formatted text or combined string
        if not treatment_plan_obj and treatment_plan:
            # Try to parse structured data from formatted text
            parsed_obj = parse_treatment_plan_from_text(treatment_plan)
            if parsed_obj:
                treatment_plan_obj = parsed_obj
                logger.info("✅ Parsed structured treatment plan from formatted text")
        
        # Also try to parse from formatted_soap_note if available
        if not treatment_plan_obj and doc.get("formatted_soap_note"):
            formatted_note = str(doc.get("formatted_soap_note", ""))
            parsed_obj = parse_treatment_plan_from_text(formatted_note)
            if parsed_obj:
                treatment_plan_obj = parsed_obj
                logger.info("✅ Parsed structured treatment plan from formatted_soap_note")
        
        # Fallback: Extract from formatted_soap_note if no structured fields found
        if not treatment_plan and doc.get("formatted_soap_note"):
            formatted_note = doc.get("formatted_soap_note", "")
            plan_patterns = [
                r'##\s*P\s*[–\-]\s*PLAN\s*\n(.*?)(?=##|---|$)',  # "## P - PLAN" or "## PLAN"
                r'##\s*PLAN\s*\n(.*?)(?=##|---|$)',  # "## PLAN"
                r'TREATMENT PLAN\s*\n(.*?)(?=WORK STATUS|##|---|$)',  # "TREATMENT PLAN"
                r'Treatment Plan\s*\n(.*?)(?=Work Status|##|---|$)',  # "Treatment Plan"
                r'##\s*TREATMENT PLAN\s*\n(.*?)(?=##|---|$)',  # "## TREATMENT PLAN"
                r'P\s*[–\-]\s*PLAN\s*\n(.*?)(?=##|---|$)',  # "P - PLAN"
                r'PLAN\s*[:\-]\s*\n(.*?)(?=##|---|$)',  # "PLAN:" or "PLAN -"
            ]
            for pattern in plan_patterns:
                match = re.search(pattern, formatted_note, re.DOTALL | re.IGNORECASE)
                if match:
                    extracted = match.group(1).strip()
                    # Clean up extracted text (remove extra markdown, etc.)
                    extracted = re.sub(r'^\*\*[^*]+\*\*[:\s]*', '', extracted)  # Remove bold headers
                    extracted = re.sub(r'^#{1,6}\s+', '', extracted, flags=re.MULTILINE)  # Remove markdown headers
                    if extracted and len(extracted) > 10:
                        treatment_plan = extracted
                        logger.info(f"✅ Extracted Treatment Plan from formatted_soap_note using pattern: {pattern[:50]}... (length: {len(extracted)})")
                        
                        # Try to parse structured object from the extracted formatted text
                        if not treatment_plan_obj:
                            parsed_obj = parse_treatment_plan_from_text(extracted)
                            if parsed_obj:
                                treatment_plan_obj = parsed_obj
                                logger.info("✅ Parsed structured treatment plan from formatted_soap_note extracted text")
                        break
        
        # Log extracted data for debugging
        logger.info(f"📋 PR2 Data Extraction Results:")
        logger.info(f"  - Subjective: length={len(subjective_complaints)}, preview: '{subjective_complaints[:100] if subjective_complaints else 'EMPTY'}...'")
        logger.info(f"  - Objective: length={len(objective_findings)}, preview: '{objective_findings[:100] if objective_findings else 'EMPTY'}...'")
        logger.info(f"  - Treatment Plan: length={len(treatment_plan)}, preview: '{treatment_plan[:100] if treatment_plan else 'EMPTY'}...'")
        logger.info(f"  - Diagnoses count: {len(diagnoses)}")
        logger.info(f"👤 Patient - Name: '{patient_name}', DOB: '{patient_dob}', DOI: '{patient_doi}'")
        
        # Log formatted_soap_note preview for debugging if objective is empty
        if (not objective_findings or len(objective_findings.strip()) < 10) and doc.get("formatted_soap_note"):
            formatted_preview = str(doc.get("formatted_soap_note", ""))[:500]
            logger.info(f"🔍 Formatted SOAP note preview (first 500 chars): {formatted_preview}")
            # Check if OBJECTIVE FINDINGS exists in the note
            if "OBJECTIVE FINDINGS" in str(doc.get("formatted_soap_note", "")).upper():
                logger.warning(f"⚠️ 'OBJECTIVE FINDINGS' header found in formatted_soap_note but extraction failed!")
            if "OBJECTIVE" in str(doc.get("formatted_soap_note", "")).upper():
                logger.warning(f"⚠️ 'OBJECTIVE' found in formatted_soap_note but extraction failed!")
        
        # Warn if subjective or objective are still empty
        if not subjective_complaints or len(subjective_complaints.strip()) < 10:
            logger.warning(f"⚠️ WARNING: Subjective complaints is empty or too short (length: {len(subjective_complaints)}). PR2 form may be incomplete.")
        if not objective_findings or len(objective_findings.strip()) < 10:
            logger.warning(f"⚠️ WARNING: Objective findings is empty or too short (length: {len(objective_findings)}). PR2 form may be incomplete.")
            logger.warning(f"⚠️ DEBUG: Objective field check - doc.get('objective'): {bool(doc.get('objective'))}, doc.get('physical_exam'): {bool(doc.get('physical_exam'))}, formatted_soap_note exists: {bool(doc.get('formatted_soap_note'))}")
        if not treatment_plan or len(treatment_plan.strip()) < 10:
            logger.warning(f"⚠️ WARNING: Treatment plan is empty or too short (length: {len(treatment_plan)}). PR2 form may be incomplete.")
    
    # Extract work status
    work_status = {
        "remainOffWorkUntil": "",
        "returnToModifiedWorkOn": "",
        "limitationsRestrictions": "",
        "returnToFullDutyOn": ""
    }
    if soap_doc:
        doc = soap_doc
        ws = doc.get("work_status", {})
        if isinstance(ws, dict):
            work_status["remainOffWorkUntil"] = ws.get("off_work_to", "") or ws.get("remainOffWorkUntil", "")
            work_status["returnToModifiedWorkOn"] = ws.get("modified_duty_from", "") or ws.get("returnToModifiedWorkOn", "")
            work_status["returnToFullDutyOn"] = ws.get("full_duty_date", "") or ws.get("returnToFullDutyOn", "")
            work_status["limitationsRestrictions"] = ws.get("restrictions", "") or ws.get("limitationsRestrictions", "")
        
        # If work status fields are still empty, try extracting from formatted_soap_note
        if not any(work_status.values()) and doc.get("formatted_soap_note"):
            formatted_note = doc.get("formatted_soap_note", "")
            import re
            
            # Extract WORK STATUS section from formatted_soap_note
            work_status_patterns = [
                r'WORK STATUS\s*SECTION\s*\n(.*?)(?=##|---|$)',  # "WORK STATUS SECTION"
                r'##\s*WORK STATUS\s*\n(.*?)(?=##|---|$)',  # "## WORK STATUS"
                r'Work Status\s*\n(.*?)(?=##|---|$)',  # "Work Status"
                r'WORK STATUS\s*\n(.*?)(?=##|---|$)',  # "WORK STATUS"
            ]
            
            work_status_text = ""
            for pattern in work_status_patterns:
                match = re.search(pattern, formatted_note, re.DOTALL | re.IGNORECASE)
                if match:
                    work_status_text = match.group(1).strip()
                    logger.info(f"✅ Found WORK STATUS section in formatted_soap_note (length: {len(work_status_text)})")
                    break
            
            if work_status_text:
                # Extract individual fields from work status text
                # Look for "Remain off work until" or similar patterns
                remain_off_patterns = [
                    r'Remain\s+off\s+work\s+until[:\s]+([^\n]+)',
                    r'Off\s+work\s+until[:\s]+([^\n]+)',
                    r'TTD\s+until[:\s]+([^\n]+)',
                    r'Unable\s+to\s+work\s+until[:\s]+([^\n]+)',
                ]
                for pattern in remain_off_patterns:
                    match = re.search(pattern, work_status_text, re.IGNORECASE)
                    if match:
                        work_status["remainOffWorkUntil"] = match.group(1).strip().rstrip('.')
                        logger.info(f"✅ Extracted remainOffWorkUntil: {work_status['remainOffWorkUntil']}")
                        break
                
                # Look for "Return to modified work" or similar
                modified_work_patterns = [
                    r'Return\s+to\s+modified\s+work\s+on[:\s]+([^\n]+)',
                    r'Modified\s+duty\s+on[:\s]+([^\n]+)',
                    r'Return\s+to\s+modified\s+duty[:\s]+([^\n]+)',
                ]
                for pattern in modified_work_patterns:
                    match = re.search(pattern, work_status_text, re.IGNORECASE)
                    if match:
                        work_status["returnToModifiedWorkOn"] = match.group(1).strip().rstrip('.')
                        logger.info(f"✅ Extracted returnToModifiedWorkOn: {work_status['returnToModifiedWorkOn']}")
                        break
                
                # Look for "Return to full duty" or similar
                full_duty_patterns = [
                    r'Return\s+to\s+full\s+duty\s+on[:\s]+([^\n]+)',
                    r'Full\s+duty\s+on[:\s]+([^\n]+)',
                    r'Return\s+to\s+full\s+duty[:\s]+([^\n]+)',
                    r'Full\s+duty[:\s]+([^\n]+)',
                ]
                for pattern in full_duty_patterns:
                    match = re.search(pattern, work_status_text, re.IGNORECASE)
                    if match:
                        work_status["returnToFullDutyOn"] = match.group(1).strip().rstrip('.')
                        logger.info(f"✅ Extracted returnToFullDutyOn: {work_status['returnToFullDutyOn']}")
                        break
                
                # Extract limitations/restrictions
                restrictions_patterns = [
                    r'Limitations\s*[\/\s]+Restrictions[:\s]+(.*?)(?=\n\n|\n[A-Z]|$)',
                    r'Restrictions[:\s]+(.*?)(?=\n\n|\n[A-Z]|$)',
                    r'Limitations[:\s]+(.*?)(?=\n\n|\n[A-Z]|$)',
                ]
                for pattern in restrictions_patterns:
                    match = re.search(pattern, work_status_text, re.IGNORECASE | re.DOTALL)
                    if match:
                        restrictions = match.group(1).strip()
                        if restrictions and len(restrictions) > 5:
                            work_status["limitationsRestrictions"] = restrictions
                            logger.info(f"✅ Extracted limitationsRestrictions: {work_status['limitationsRestrictions'][:100]}...")
                            break
                
                # If no specific fields found, use entire work status text as restrictions
                if not work_status["limitationsRestrictions"] and len(work_status_text) > 20:
                    work_status["limitationsRestrictions"] = work_status_text
                    logger.info(f"✅ Using entire WORK STATUS section as limitationsRestrictions (length: {len(work_status_text)})")
        
        # Log extracted work status
        logger.info(f"📋 Work Status Extraction Results:")
        logger.info(f"  - remainOffWorkUntil: '{work_status['remainOffWorkUntil']}'")
        logger.info(f"  - returnToModifiedWorkOn: '{work_status['returnToModifiedWorkOn']}'")
        logger.info(f"  - returnToFullDutyOn: '{work_status['returnToFullDutyOn']}'")
        logger.info(f"  - limitationsRestrictions: length={len(work_status['limitationsRestrictions'])}, preview: '{work_status['limitationsRestrictions'][:100] if work_status['limitationsRestrictions'] else 'EMPTY'}...'")
        
        # Warn if work status is still empty
        if not any(work_status.values()):
            logger.warning(f"⚠️ WARNING: Work status is empty. PR2 form may be incomplete.")
    
    # Extract physician info
    physician = {
        "signature": "",
        "dateOfExam": soap_doc.get("date_of_service", "") if soap_doc else "",
        "executedAt": "",
        "date": datetime.now().strftime("%m/%d/%Y"),
        "physicianName": soap_doc.get("examiner", "") if soap_doc else "",
        "specialty": soap_doc.get("specialty", "") if soap_doc else "",
        "address": "",
        "phoneNumber": "",
        "californiaLicenseNumber": ""
    }
    
    # Report type - default to periodic report
    report_type = {
        "periodicReport": True,
        "changeInTreatmentPlan": False,
        "releaseFromCare": False,
        "changeInWorkStatus": False,
        "needForReferral": False,
        "responseToRequest": False,
        "changeInPatientCondition": False,
        "needForSurgery": False,
        "requestForAuthorization": False,
        "other": False,
        "otherText": ""
    }
    
    if pr1_doc:
        pr1_checkboxes = pr1_doc.get("page1_checkboxes", {})
        if pr1_checkboxes:
            report_type["changeInTreatmentPlan"] = pr1_checkboxes.get("change_in_treatment_plan", False)
            report_type["releaseFromCare"] = pr1_checkboxes.get("released_from_care", False)
            report_type["changeInWorkStatus"] = pr1_checkboxes.get("change_in_work_status", False)
            report_type["requestForAuthorization"] = pr1_checkboxes.get("request_for_authorization", False)
    
    return {
        "reportType": report_type,
        "patient": {
            "lastName": last_name,
            "firstName": first_name,
            "middleInitial": middle_initial,
            "streetAddress": "",
            "city": "",
            "state": "",
            "zipCode": "",
            "sex": "",
            "occupation": "",
            "phoneNumber": "",
            "dateOfBirth": patient_dob,
            "claimsAdministrator": "",
            "dateOfInjury": patient_doi
        },
        "claimsAdministrator": {
            "name": "",
            "claimNumber": "",
            "streetAddress": "",
            "city": "",
            "state": "",
            "zipCode": "",
            "phoneNumber": "",
            "faxNumber": "",
            "employerName": "",
            "employerPhoneNumber": ""
        },
        "subjectiveComplaints": subjective_complaints_obj if subjective_complaints_obj else subjective_complaints,
        "objectiveFindings": objective_findings_obj if objective_findings_obj else objective_findings,
        "diagnoses": diagnoses,
        "treatmentPlan": treatment_plan_obj if treatment_plan_obj else treatment_plan,
        "workStatus": work_status,
        "physician": physician
    }


@router.post("/pr2/generate-from-transcription")
async def generate_pr2_from_transcription(
    transcription_id: Optional[str] = Form(None, description="Transcription MongoDB ID"),
    transcription: Optional[str] = Form(None, description="Transcription text"),
    use_latest_intake: bool = Form(False, description="Use latest intake form"),
    use_latest_followup: bool = Form(False, description="Use latest follow-up form"),
    pr1_id: Optional[str] = Form(None, description="PR1 form MongoDB ID (optional)")
):
    """
    Automatically generate PR2 form directly from transcription.
    This endpoint will:
    1. Generate SOAP note from transcription (if not already exists)
    2. Generate PR2 form from SOAP note
    3. Return PR2 form data ready to populate the form
    
    **Parameters:**
    - transcription_id: MongoDB transcription document ID (required if transcription not provided)
    - transcription: Clinical transcription text (required if transcription_id not provided)
    - use_latest_intake: Use latest intake form (default: False)
    - use_latest_followup: Use latest follow-up form (default: False)
    - pr1_id: Optional PR1 form MongoDB ID
    
    **Returns:**
    - status: Success status
    - pr2_values: Complete PR2 form data structure
    - document_id: MongoDB document ID of the saved PR2 form
    - soap_id: MongoDB document ID of the generated SOAP note
    """
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection not available")
        
        # Step 1: Get transcription text
        transcription_text = None
        if transcription_id:
            transcription_doc = await get_transcription_by_id(transcription_id)
            if not transcription_doc:
                raise HTTPException(status_code=404, detail=f"Transcription not found with ID: {transcription_id}")
            transcription_text = transcription_doc.get("text", "")
        elif transcription:
            transcription_text = transcription.strip()
        else:
            raise HTTPException(status_code=400, detail="Either transcription_id or transcription text must be provided")
        
        if not transcription_text:
            raise HTTPException(status_code=400, detail="Transcription text is empty")
        
        # Step 2: Generate SOAP note from transcription
        logger.info("Generating SOAP note from transcription...")
        
        # Check if SOAP note already exists for this transcription
        soap_doc = None
        if transcription_id:
            soap_doc = await get_soap_note_by_transcription_id(transcription_id)
        
        if not soap_doc:
            # Generate new SOAP note by calling the SOAP generation endpoint internally
            # Use httpx to make an internal API call to avoid circular imports
            import os
            
            # Try to get base URL from environment, or use localhost
            base_url = os.getenv("BASE_URL") or os.getenv("API_BASE_URL") or "http://127.0.0.1:8000"
            
            # Prepare SOAP request payload
            soap_payload = {
                "transcription": transcription_text
            }
            if transcription_id:
                soap_payload["transcription_id"] = transcription_id
            
            # Add query parameters for intake form
            url = f"{base_url}/api/v1/generate-soap"
            if use_latest_intake:
                url += "?use_latest_intake=true"
            
            # Call SOAP generation endpoint
            try:
                async with httpx.AsyncClient() as client:
                    soap_response = await client.post(
                        url,
                        json=soap_payload,
                        timeout=300.0  # 5 minute timeout for SOAP generation
                    )
                    soap_response.raise_for_status()
                    soap_data = soap_response.json()
            except httpx.RequestError as e:
                logger.error(f"Error calling SOAP generation endpoint: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to generate SOAP note: {str(e)}")
            except httpx.HTTPStatusError as e:
                logger.error(f"SOAP generation endpoint returned error: {e.response.status_code} - {e.response.text}")
                raise HTTPException(status_code=e.response.status_code, detail=f"SOAP generation failed: {e.response.text}")
            
            # Get the SOAP document from database using document_id
            soap_document_id = soap_data.get("document_id")
            if soap_document_id:
                soap_doc = await fetch_if_needed(None, soap_document_id, COLL_SOAP)
                if soap_doc:
                    logger.info(f"✅ SOAP note generated and saved with ID: {soap_document_id}")
                else:
                    raise HTTPException(status_code=500, detail="SOAP note generated but could not be retrieved from database")
            else:
                raise HTTPException(status_code=500, detail="SOAP note generated but document_id not returned")
        else:
            logger.info(f"✅ Using existing SOAP note with ID: {soap_doc.get('_id')}")
        
        # Step 3: Generate PR2 from SOAP
        # Fetch intake/followup if requested
        intake_doc = await fetch_latest_document(COLL_INTAKE) if use_latest_intake else None
        follow_doc = await fetch_latest_document(COLL_FOLLOWUP) if use_latest_followup else None
        
        # Fetch PR1 if provided
        pr1_doc = None
        if pr1_id:
            pr1_doc = await fetch_if_needed(None, pr1_id, "pr1_forms")
        
        # Log SOAP document structure before building PR2
        if soap_doc:
            logger.info(f"📄 SOAP Document Structure - Keys: {list(soap_doc.keys())[:20]}")  # First 20 keys
            logger.info(f"📄 SOAP Document - Has subjective: {bool(soap_doc.get('subjective'))}, Has objective: {bool(soap_doc.get('objective'))}, Has plan: {bool(soap_doc.get('plan'))}")
            logger.info(f"📄 SOAP Document - Subjective preview: {str(soap_doc.get('subjective', ''))[:100]}...")
            logger.info(f"📄 SOAP Document - Objective preview: {str(soap_doc.get('objective', ''))[:100]}...")
            logger.info(f"📄 SOAP Document - Plan preview: {str(soap_doc.get('plan', ''))[:100]}...")
        
        # Build PR2 form
        pr2_data = build_pr2_from_data(intake_doc, follow_doc, soap_doc, pr1_doc)
        
        # Log final PR2 data structure
        logger.info(f"✅ PR2 Data Built - Subjective: {len(pr2_data.get('subjectiveComplaints', ''))} chars, Objective: {len(pr2_data.get('objectiveFindings', ''))} chars, Plan: {len(pr2_data.get('treatmentPlan', ''))} chars")
        logger.info(f"✅ PR2 Data Built - Patient name: '{pr2_data.get('patient', {}).get('firstName', '')} {pr2_data.get('patient', {}).get('lastName', '')}'")
        logger.info(f"✅ PR2 Data Built - Diagnoses count: {len(pr2_data.get('diagnoses', []))}")
        
        # Save to database
        pr2_data["created_at"] = datetime.utcnow()
        if transcription_id:
            pr2_data["transcription_id"] = transcription_id
        collection = db[PR2_FORMS_COLLECTION]
        result = await collection.insert_one(pr2_data)
        pr2_document_id = str(result.inserted_id)
        
        logger.info(f"✅ PR2 form generated and saved with ID: {pr2_document_id}")
        
        # Serialize pr2_data to handle datetime objects
        serialized_pr2_data = serialize_mongodb_doc(pr2_data)
        
        return JSONResponse({
            "status": "success",
            "message": "PR2 form generated successfully from transcription.",
            "pr2_values": serialized_pr2_data,
            "document_id": pr2_document_id,
            "soap_id": str(soap_doc.get("_id")) if soap_doc else None
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating PR2 form from transcription: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate PR2 form from transcription: {str(e)}")


@router.post("/pr2/generate-from-soap")
async def generate_pr2_from_soap(
    soap_id: str = Form(..., description="SOAP note MongoDB ID"),
    use_latest_intake: bool = Form(False, description="Use latest intake form"),
    use_latest_followup: bool = Form(False, description="Use latest follow-up form"),
    pr1_id: Optional[str] = Form(None, description="PR1 form MongoDB ID (optional)")
):
    """
    Automatically generate PR2 form from SOAP note and optionally intake/followup/PR1 data
    No transcription or SOAP creation - just generates PR2 form and saves it
    """
    try:
        db = get_database()
        if db is None:
            raise HTTPException(status_code=503, detail="Database connection not available")
        
        # Fetch SOAP note
        soap_doc = await fetch_if_needed(None, soap_id, COLL_SOAP)
        if not soap_doc:
            raise HTTPException(status_code=404, detail=f"SOAP note not found with ID: {soap_id}")
        
        # Fetch intake/followup if requested
        intake_doc = await fetch_latest_document(COLL_INTAKE) if use_latest_intake else None
        follow_doc = await fetch_latest_document(COLL_FOLLOWUP) if use_latest_followup else None
        
        # Fetch PR1 if provided
        pr1_doc = None
        if pr1_id:
            pr1_doc = await fetch_if_needed(None, pr1_id, "pr1_forms")
        
        # Build PR2 form
        pr2_data = build_pr2_from_data(intake_doc, follow_doc, soap_doc, pr1_doc)
        
        # Save to database
        pr2_data["created_at"] = datetime.utcnow()
        collection = db[PR2_FORMS_COLLECTION]
        result = await collection.insert_one(pr2_data)
        pr2_document_id = str(result.inserted_id)
        
        logger.info(f"✅ PR2 form generated and saved with ID: {pr2_document_id}")
        
        # Serialize pr2_data to handle datetime objects
        serialized_pr2_data = serialize_mongodb_doc(pr2_data)
        
        return JSONResponse({
            "status": "success",
            "message": "PR2 form generated successfully.",
            "pr2_values": serialized_pr2_data,
            "document_id": pr2_document_id
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating PR2 form: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate PR2 form: {str(e)}")


@router.get("/pr2-form/latest")
async def get_latest_pr2_form():
    """
    Get the most recently created PR2 form
    
    **Returns:**
    - Complete PR2 form document with all fields
    - Includes document_id, transcription_id, and created_at timestamp
    - Returns 404 if no PR2 forms exist
    """
    try:
        # Get the database (using existing database connection)
        db = get_database()
        if db is None:
            raise HTTPException(
                status_code=503,
                detail="Database connection not available"
            )
        
        collection = db[PR2_FORMS_COLLECTION]
        
        # Find the latest PR2 form (sorted by created_at descending, limit 1)
        latest_form = await collection.find_one(
            sort=[("created_at", -1)]
        )
        
        if latest_form is None:
            raise HTTPException(
                status_code=404,
                detail="No PR2 forms found"
            )
        
        # Serialize MongoDB document (convert ObjectId and datetime to strings)
        serialized_form = serialize_mongodb_doc(latest_form)
        
        # Add document_id for convenience
        serialized_form["document_id"] = serialized_form.get("_id")
        
        logger.info(f"✅ Retrieved latest PR2 form with ID: {serialized_form.get('_id')}")
        
        return JSONResponse(serialized_form)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving latest PR2 form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve latest PR2 form: {str(e)}"
        )


@router.get("/pr2-form/{form_id}")
async def get_pr2_form_by_id(form_id: str):
    """
    Get a specific PR2 form by ID
    
    **Parameters:**
    - form_id: MongoDB document ID of the PR2 form
    
    **Returns:**
    - Complete PR2 form document with all fields
    - Includes document_id, transcription_id, and created_at timestamp
    - Returns 404 if form not found
    """
    try:
        # Get the database
        db = get_database()
        if db is None:
            raise HTTPException(
                status_code=503,
                detail="Database connection not available"
            )
        
        collection = db[PR2_FORMS_COLLECTION]
        
        # Validate ObjectId format
        try:
            object_id = ObjectId(form_id)
        except Exception:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid form ID format: {form_id}"
            )
        
        # Find the form by ID
        form = await collection.find_one({"_id": object_id})
        
        if form is None:
            raise HTTPException(
                status_code=404,
                detail=f"PR2 form not found with ID: {form_id}"
            )
        
        # Serialize MongoDB document
        serialized_form = serialize_mongodb_doc(form)
        
        # Add document_id for convenience
        serialized_form["document_id"] = serialized_form.get("_id")
        
        logger.info(f"✅ Retrieved PR2 form with ID: {form_id}")
        
        return JSONResponse(serialized_form)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving PR2 form: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve PR2 form: {str(e)}"
        )

