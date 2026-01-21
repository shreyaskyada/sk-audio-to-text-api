
"""
PDF Processing Services
Functions for extracting text and data from PDF files
"""
import logging
import os
import tempfile
from typing import Dict, Any, Optional

from fastapi import UploadFile, HTTPException
from pypdf import PdfReader
from app.services.openai import create_openai_client

logger = logging.getLogger(__name__)

# OpenAI Model configuration
OPENAI_MODEL = 'gpt-5.1'

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
        # Validate OpenAI API key
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            error_msg = "OpenAI API key not configured. Please set OPENAI_API_KEY environment variable."
            logger.error(error_msg)
            raise HTTPException(status_code=500, detail=error_msg)
        
        # Truncate PDF text if it's too long
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
            
            # Simple retry logic for transient errors
            if "rate_limit" in error_msg.lower() or "timeout" in error_msg.lower():
                import time
                logger.info("Retrying OpenAI API call after 2 seconds...")
                time.sleep(2)
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
            else:
                raise
        
        response_content = response.choices[0].message.content
        if not response_content:
             raise Exception("Empty response from OpenAI API")
             
        import json
        json_response = json.loads(response_content)
        
        logger.info("✅ Successfully converted PDF text to structured JSON")
        return json_response
        
    except Exception as e:
        logger.error(f"Error converting PDF text to SOAP JSON: {e}")
        # Allow caller to handle default values or re-raise
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process PDF content: {str(e)}"
        )
