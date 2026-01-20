
import logging
import os
import re
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, BackgroundTasks, Query, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import settings
from app.routes.transcription_routes import fix_terms
from app.services.soap_service import save_soap_note_to_db, create_pending_soap_note, get_soap_note_by_id, inject_intake_data_into_soap, extract_intake_form_values
from app.services.transcription_service import get_transcription_by_id
from app.services.cpt_service import validate_and_correct_cpt_codes, extract_and_validate_cpt_codes_from_soap, validate_all_cpt_codes_in_soap, aggressive_validate_rfa_supportive_cpts

# Try to import prompts - handling potential missing file
try:
    from app.prompts import (
        ORTHOPEDIC_SOAP_SYSTEM_PROMPT,
        ORTHOPEDIC_SOAP_USER_PROMPT_TEMPLATE,
    )
except ImportError:
    # Fallback prompts if file missing
    ORTHOPEDIC_SOAP_SYSTEM_PROMPT = "You are an expert medical documentation assistant..."
    ORTHOPEDIC_SOAP_USER_PROMPT_TEMPLATE = "Transform this transcription: {transcription}"

# Try to import schemas
try:
    from app.schemas.soap_schema import SOAPRequest, SOAPResponse
except ImportError:
    # Generic schemas if missing
    class SOAPRequest(BaseModel):
        transcription_id: Optional[str] = None
        transcription: Optional[str] = None
        patient: Optional[dict] = None
        userId: Optional[str] = None
        date_of_service: Optional[str] = None
        location: Optional[str] = None
        reason_for_visit: Optional[str] = None
        system_prompt: Optional[str] = None
        user_prompt_template: Optional[str] = None
        model: Optional[str] = None
    
    class SOAPResponse(BaseModel):
        document_id: Optional[str] = None
        status: Optional[str] = None
        transcription: Optional[str] = None
        formatted_soap_note: Optional[str] = None
        subjective: Optional[str] = None
        objective: Optional[str] = None
        assessment: Optional[str] = None
        plan: Optional[str] = None

# OpenAI Client Setup
import httpx
import openai
from openai import OpenAI

def create_openai_client():
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY")
    
    # Create httpx client explicitly to avoid proxy issues (Same as legacy code)
    http_client = httpx.Client(
        timeout=60.0,
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        # Explicitly set trust_env to False if we suspect proxy issues from env vars
        # trust_env=False 
    )
    
    return OpenAI(
        api_key=api_key,
        max_retries=2,
        timeout=60.0,
        http_client=http_client
    )

logger = logging.getLogger(__name__)

router = APIRouter()

OPENAI_MODEL = "gpt-5.1"

# ==========================================
# Helpers
# ==========================================

def extract_soap_sections_from_formatted_note(formatted_soap_note: str) -> dict:
    """Extract S/O/A/P sections from the note"""
    sections = {"subjective": "", "objective": "", "assessment": "", "plan": ""}
    if not formatted_soap_note: return sections

    note = formatted_soap_note.replace("\r\n", "\n").replace("\r", "\n")
    
    def _between(start_regex, end_regexes):
        start_match = re.search(start_regex, note, flags=re.IGNORECASE | re.MULTILINE)
        if not start_match: return ""
        start_idx = start_match.end()
        end_idx = len(note)
        tail = note[start_idx:]
        for end_regex in end_regexes:
            m = re.search(end_regex, tail, flags=re.IGNORECASE | re.MULTILINE)
            if m: end_idx = min(end_idx, start_idx + m.start())
        return note[start_idx:end_idx].strip()

    # Try standard headers
    sections["subjective"] = _between(r"^SUBJECTIVE\s*$", [r"^OBJECTIVE", r"^ASSESSMENT", r"^---\s*$"])
    sections["objective"] = _between(r"^OBJECTIVE.*$", [r"^ASSESSMENT", r"^PLAN", r"^---\s*$"])
    sections["assessment"] = _between(r"^ASSESSMENT\s*$", [r"^PLAN", r"^---\s*$"])
    sections["plan"] = _between(r"^PLAN\s*$", [r"^---\s*$", r"^SIGNATURE", r"^CPT"])
    
    # Fallback to Markdown
    if not any(sections.values()):
        sections["subjective"] = _between(r"^##\s*S\s*[-–]\s*SUBJECTIVE", [r"^##\s*O", r"^---"])
        sections["objective"] = _between(r"^##\s*O\s*[-–]\s*OBJECTIVE", [r"^##\s*A", r"^---"])
        sections["assessment"] = _between(r"^##\s*A\s*[-–]\s*ASSESSMENT", [r"^##\s*P", r"^---"])
        sections["plan"] = _between(r"^##\s*P\s*[-–]\s*PLAN", [r"^---", r"$"])

    return sections

async def generate_comprehensive_soap_note(
    request: SOAPRequest,
    intake_form_data: str = None,
    intake_doc: dict = None,
    model: str = None,
    pending_soap_id: str = None
) -> dict:
    """Core logic to generate SOAP note using OpenAI"""
    try:
        client = create_openai_client()
        
        # 1. Prepare Prompts
        transcription = fix_terms(request.transcription or "")
        
        patient_context = ""
        header_section = ""
        if request.patient:
            p = request.patient
            info = []
            if isinstance(p, dict):
                 if p.get('name'): info.append(f"Name: {p.get('name')}")
                 if p.get('age'): info.append(f"Age: {p.get('age')}")
            else:
                 if getattr(p, 'name', None): info.append(f"Name: {p.name}")
            
            if info:
                patient_context = "**PATIENT INFORMATION:**\n" + "\n".join(info)
                header_section = f"Patient: {', '.join(info)}\n"
        
        if request.date_of_service: header_section += f"Date of Service: {request.date_of_service}\n"
        
        system_prompt = request.system_prompt or ORTHOPEDIC_SOAP_SYSTEM_PROMPT
        user_template = request.user_prompt_template or ORTHOPEDIC_SOAP_USER_PROMPT_TEMPLATE
        
        user_prompt = user_template.replace("{transcription}", transcription)
        user_prompt = user_prompt.replace("{patient_context}", patient_context)
        user_prompt = user_prompt.replace("{header_section}", header_section)
        user_prompt = user_prompt.replace("{intake_form_data}", intake_form_data or "")

        # 2. Call AI
        selected_model = model or OPENAI_MODEL
        logger.info(f"Sending request to OpenAI ({selected_model})...")
        
        response = client.chat.completions.create(
            model=selected_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,
            max_completion_tokens=12000
        )
        
        generated_text = response.choices[0].message.content
        
        # --- FIX: "Employer / Carrier: Not on File" Hallucination ---
        # If AI generated "Not on File", try to fix it from transcription or remove it
        if "Employer / Carrier: Not on File" in generated_text or "Employer / Carrier: [Not on File]" in generated_text:
             # Try to find it in transcription
             emp_match = re.search(r'(Employer\s*/\s*Carrier|Employer|Carrier|Insurance)\s*:\s*(.+?)(?=\n|$)', transcription, re.IGNORECASE)
             if emp_match:
                 found_value = emp_match.group(2).strip()
                 if found_value and found_value.lower() not in ["not on file", "none", "n/a"]:
                     generated_text = re.sub(r'Employer / Carrier:.*?(?=\n)', f'Employer / Carrier: {found_value}', generated_text)
                     logger.info(f"✅ Fixed 'Not on File' with transcription value: {found_value}")
                 else:
                     generated_text = re.sub(r'Employer / Carrier:.*?\n', '', generated_text)
             else:
                 generated_text = re.sub(r'Employer / Carrier:.*?\n', '', generated_text)
        


        # 2.5 Post-processing (Restore Old Backend Logic)
        
        # Inject intake form data directly if available
        if intake_doc:
            intake_values = extract_intake_form_values(intake_doc)
            if intake_values:
                logger.info(f"🔧 Post-processing SOAP note to inject intake form data: {intake_values}")
                generated_text = inject_intake_data_into_soap(generated_text, intake_values)
        
        # Validate and CORRECT CPT codes using AI (Old Backend Logic)
        # All CPT codes are generated dynamically by AI - NO static code lists
        generated_text = await validate_and_correct_cpt_codes(generated_text, transcription, client)
        
        # Final validation (Clean CPT codes)
        generated_text = validate_all_cpt_codes_in_soap(generated_text)
        
        # AGGRESSIVE FINAL VALIDATION: Ensure 100% valid codes in RFA Supportive CPTs section
        generated_text = aggressive_validate_rfa_supportive_cpts(generated_text)

        # 3. Post-process (Extract sections)
        sections = extract_soap_sections_from_formatted_note(generated_text)
        
        # 4. Save
        soap_data = {
            "transcription": request.transcription,
            "corrected_transcription": transcription,
            "transcription_id": request.transcription_id,
            "formatted_soap_note": generated_text,
            "subjective": sections["subjective"],
            "objective": sections["objective"],
            "assessment": sections["assessment"],
            "plan": sections["plan"],
            "patient_info": request.patient.dict() if hasattr(request.patient, 'dict') else request.patient,
            "userId": request.userId,
            "date_of_service": request.date_of_service,
            "location": request.location,
            "reason_for_visit": request.reason_for_visit,
            "system_prompt": system_prompt if request.system_prompt else None,
            "user_prompt_template": request.user_prompt_template if request.user_prompt_template else None,
        }
        
        saved_doc = None
        if pending_soap_id:
             from app.services.soap_service import update_soap_note_in_db
             logger.info(f"Using existing pending SOAP note: {pending_soap_id}")
             # Update existing pending note
             soap_data["updated_at"] = datetime.utcnow()
             soap_data["status"] = "completed"
             await update_soap_note_in_db(pending_soap_id, soap_data)
             saved_doc = soap_data
             saved_doc["_id"] = pending_soap_id
        else:
             saved_doc = await save_soap_note_to_db(soap_data)
        
        # NOTE: Pipeline triggering is now handled by the caller (endpoint) via BackgroundTasks
        # See trigger_downstream_pipeline below
        
        return saved_doc
    except Exception as e:
        logger.error(f"SOAP Generation Error: {e}")
        raise


async def trigger_downstream_pipeline(soap_id: str, request_data: dict, intake_doc: dict = None):
    """
    Trigger downstream PR1 and Work Status generation in the background.
    """
    try:
        from app.services import pr1_service, work_status_service
        
        logger.info(f"🚀 [Background] Triggering Downstream Pipeline for SOAP {soap_id}")
        
        # Run PR1 Generation
        try:
             logger.info(f"🔄 [Background] Starting PR1 Generation for {soap_id}...")
             await pr1_service.process_pr1_generation(
                 soap_id=soap_id,
                 use_latest_intake=True if intake_doc else False,
                 use_latest_followup=False,
                 flags=request_data.get("flags", {})
             )
             logger.info(f"✅ [Background] PR1 Pipeline Completed for {soap_id}")
        except Exception as e:
             logger.error(f"❌ [Background] PR1 Generation Failed for {soap_id}: {e}")

        # Run Work Status Generation
        try:
             logger.info(f"🔄 [Background] Starting Work Status Generation for {soap_id}...")
             await work_status_service.process_work_status_generation(soap_id)
             logger.info(f"✅ [Background] Work Status Pipeline Completed for {soap_id}")
        except Exception as e:
             logger.error(f"❌ [Background] Work Status Generation Failed for {soap_id}: {e}")
             
    except Exception as e:
        logger.error(f"❌ [Background] Pipeline Trigger Error: {e}")

# ==========================================
# Endpoints
# ==========================================

@router.post("/api/v1/generate-soap", response_model=SOAPResponse)
async def generate_soap_endpoint(
    request: SOAPRequest,
    background_tasks: BackgroundTasks
):
    """
    Generate SOAP note from transcription.
    Replicates the legacy /api/v1/generate-soap endpoint.
    """
    try:
        # Resolve transcription
        if request.transcription_id and not request.transcription:
            t_doc = await get_transcription_by_id(request.transcription_id)
            if not t_doc:
                raise HTTPException(status_code=404, detail="Transcription not found")
            request.transcription = t_doc.get("text", "")
        
        if not request.transcription:
            raise HTTPException(status_code=400, detail="Transcription text required")

        # Create pending - Restored to match Old Backend behavior
        pending_id = None
        if request.transcription_id:
             from app.services.soap_service import create_pending_soap_note
             pending_doc = await create_pending_soap_note(request.transcription_id, request.userId)
             pending_id = pending_doc.get("_id")

        # Generate (Synchronous - matches Old Backend)
        # Pass pending_id to update the existing record
        result = await generate_comprehensive_soap_note(
            request, 
            pending_soap_id=pending_id
        )
        
        # Trigger Background Pipeline
        soap_id = result.get("_id") or pending_id
        if soap_id:
            # Pass necessary data to background task
            request_data = request.dict() if hasattr(request, 'dict') else request.model_dump()
            background_tasks.add_task(trigger_downstream_pipeline, str(soap_id), request_data)
        
        # Return response
        return SOAPResponse(
            document_id=result.get("_id"),
            status="completed",
            transcription=result.get("transcription"),
            formatted_soap_note=result.get("formatted_soap_note"),
            subjective=result.get("subjective"),
            objective=result.get("objective"),
            assessment=result.get("assessment"),
            plan=result.get("plan")
        )

    except HTTPException: raise
    except Exception as e:
        logger.error(f"Error in generate-soap: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/v1/soap-prompts/default")
async def get_default_prompts():
    """Get default prompts"""
    return {
        "system_prompt": ORTHOPEDIC_SOAP_SYSTEM_PROMPT,
        "user_prompt_template": ORTHOPEDIC_SOAP_USER_PROMPT_TEMPLATE
    }
