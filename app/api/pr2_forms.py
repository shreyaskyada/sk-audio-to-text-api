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

logger = logging.getLogger(__name__)

router = APIRouter()

# Configuration
PR2_FORMS_COLLECTION = 'pr2_forms'


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
    
    # Extract diagnoses
    diagnoses = []
    if soap_doc:
        import re
        # Try to get diagnoses from multiple possible locations
        soap_diagnoses = soap_doc.get("diagnoses", [])
        
        # First try diagnoses array
        if isinstance(soap_diagnoses, list) and len(soap_diagnoses) > 0:
            for dx in soap_diagnoses[:12]:
                if isinstance(dx, dict):
                    diagnoses.append({
                        "diagnosis": dx.get("condition", dx.get("diagnosis", "")),
                        "icd10": dx.get("icd10", dx.get("icd_10", ""))
                    })
        
        # If no diagnoses from array, try assessment section
        if not diagnoses and soap_doc.get("assessment"):
            assessment = soap_doc.get("assessment", "")
            # Look for diagnosis patterns in assessment text
            # Pattern to match diagnosis with ICD-10 codes: "Diagnosis Name (ICD-10: M54.5)" or similar
            dx_pattern = r'([A-Z][^()]+?)\s*\(ICD[- ]?10[:\s]+([A-Z0-9.]+)\)'
            matches = re.findall(dx_pattern, assessment, re.IGNORECASE)
            for match in matches[:12]:
                diagnoses.append({
                    "diagnosis": match[0].strip(),
                    "icd10": match[1].strip()
                })
        
        # If still no diagnoses, try formatted_soap_note
        if not diagnoses and soap_doc.get("formatted_soap_note"):
            formatted_note = soap_doc.get("formatted_soap_note", "")
            # Try to extract Assessment section
            assessment_match = re.search(
                r'## A – ASSESSMENT\s*\n(.*?)(?=##|---|$)',
                formatted_note,
                re.DOTALL | re.IGNORECASE
            )
            if assessment_match:
                assessment_text = assessment_match.group(1).strip()
                # Look for diagnosis patterns
                dx_pattern = r'([A-Z][^()]+?)\s*\(ICD[- ]?10[:\s]+([A-Z0-9.]+)\)'
                matches = re.findall(dx_pattern, assessment_text, re.IGNORECASE)
                for match in matches[:12]:
                    diagnoses.append({
                        "diagnosis": match[0].strip(),
                        "icd10": match[1].strip()
                    })
    
    # Extract subjective, objective, treatment plan
    subjective_complaints = ""
    objective_findings = ""
    treatment_plan = ""
    
    if soap_doc:
        # Log SOAP doc structure for debugging
        logger.info(f"🔍 SOAP doc keys: {list(soap_doc.keys())}")
        logger.info(f"🔍 SOAP subjective exists: {bool(soap_doc.get('subjective'))}, length: {len(soap_doc.get('subjective', ''))}")
        logger.info(f"🔍 SOAP objective exists: {bool(soap_doc.get('objective'))}, length: {len(soap_doc.get('objective', ''))}")
        logger.info(f"🔍 SOAP plan exists: {bool(soap_doc.get('plan'))}, length: {len(soap_doc.get('plan', ''))}")
        logger.info(f"🔍 SOAP assessment exists: {bool(soap_doc.get('assessment'))}, length: {len(soap_doc.get('assessment', ''))}")
        
        # Subjective - try multiple field names
        subjective_complaints = (
            soap_doc.get("subjective", "") or 
            soap_doc.get("chief_complaint", "") or
            soap_doc.get("chief_complaint_and_history", "") or
            ""
        )
        
        # If subjective is empty, try to extract from formatted_soap_note
        if not subjective_complaints and soap_doc.get("formatted_soap_note"):
            import re
            formatted_note = soap_doc.get("formatted_soap_note", "")
            # Try to extract Subjective section from formatted note
            subjective_match = re.search(
                r'## S – SUBJECTIVE\s*\n(.*?)(?=##|---|$)',
                formatted_note,
                re.DOTALL | re.IGNORECASE
            )
            if subjective_match:
                subjective_complaints = subjective_match.group(1).strip()
        
        # Objective - try multiple field names
        objective_findings = (
            soap_doc.get("objective", "") or 
            soap_doc.get("physical_exam", "") or
            soap_doc.get("physical_examination", "") or
            ""
        )
        
        # If objective is empty, try to extract from formatted_soap_note
        if not objective_findings and soap_doc.get("formatted_soap_note"):
            import re
            formatted_note = soap_doc.get("formatted_soap_note", "")
            # Try to extract Objective section from formatted note
            objective_match = re.search(
                r'## O – OBJECTIVE\s*\n(.*?)(?=##|---|$)',
                formatted_note,
                re.DOTALL | re.IGNORECASE
            )
            if objective_match:
                objective_findings = objective_match.group(1).strip()
        
        # Treatment plan - try multiple field names
        treatment_plan = (
            soap_doc.get("plan", "") or 
            soap_doc.get("treatment_plan_text", "") or
            soap_doc.get("treatment_plan", "") or
            ""
        )
        
        # If plan is empty, try to extract from formatted_soap_note
        if not treatment_plan and soap_doc.get("formatted_soap_note"):
            import re
            formatted_note = soap_doc.get("formatted_soap_note", "")
            # Try to extract Plan section from formatted note
            plan_match = re.search(
                r'## P – PLAN\s*\n(.*?)(?=##|---|$)',
                formatted_note,
                re.DOTALL | re.IGNORECASE
            )
            if plan_match:
                treatment_plan = plan_match.group(1).strip()
        
        # Log extracted data for debugging
        logger.info(f"📋 PR2 Data Extraction - Subjective length: {len(subjective_complaints)}, Objective length: {len(objective_findings)}, Plan length: {len(treatment_plan)}, Diagnoses count: {len(diagnoses)}")
        logger.info(f"👤 Patient - Name: '{patient_name}', DOB: '{patient_dob}', DOI: '{patient_doi}'")
    
    # Extract work status
    work_status = {
        "remainOffWorkUntil": "",
        "returnToModifiedWorkOn": "",
        "limitationsRestrictions": "",
        "returnToFullDutyOn": ""
    }
    if soap_doc:
        ws = soap_doc.get("work_status", {})
        if isinstance(ws, dict):
            work_status["remainOffWorkUntil"] = ws.get("off_work_to", "")
            work_status["returnToModifiedWorkOn"] = ws.get("modified_duty_from", "")
            work_status["returnToFullDutyOn"] = ws.get("full_duty_date", "")
            work_status["limitationsRestrictions"] = ws.get("restrictions", "")
    
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
        "subjectiveComplaints": subjective_complaints,
        "objectiveFindings": objective_findings,
        "diagnoses": diagnoses,
        "treatmentPlan": treatment_plan,
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

